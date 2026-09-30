"""Photo storage: import (upload or local folder), HEIC -> JPEG, EXIF shoot
time, thumbnails, and numbered contact sheets for AI grouping."""
import base64
import datetime
import io
import os
import re
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw, ImageFont, ImageOps

import config
from db import get_db, image_dict

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:  # HEIC just won't open
    pillow_heif = None

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")
THUMB_PX = 480
EXIF_DATETIME_ORIGINAL = 36867
EXIF_IFD = 0x8769


def batch_dir(batch_id):
    return os.path.join(config.PHOTOS_DIR, f"batch_{batch_id:03d}")


def _safe_stem(filename):
    stem = os.path.splitext(os.path.basename(filename or "photo"))[0]
    return re.sub(r"[^A-Za-z0-9._-]", "_", stem) or "photo"


def _taken_at(im):
    try:
        exif = im.getexif()
        raw = exif.get_ifd(EXIF_IFD).get(EXIF_DATETIME_ORIGINAL) or exif.get(306)
        if raw:
            return datetime.datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S").isoformat()
    except (ValueError, KeyError, AttributeError):
        pass
    return ""


def _store(batch_id, original_name, opener):
    """Decode an image, write full JPEG + thumbnail into the batch folder.
    Returns (rel_path, rel_thumb, taken_at)."""
    folder = batch_dir(batch_id)
    os.makedirs(os.path.join(folder, "thumbs"), exist_ok=True)
    stem = _safe_stem(original_name)
    name = f"{stem}.jpg"
    n = 1
    while os.path.exists(os.path.join(folder, name)):
        n += 1
        name = f"{stem}_{n}.jpg"

    with opener() as im:
        taken = _taken_at(im)
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.save(os.path.join(folder, name), format="JPEG", quality=90)
        im.thumbnail((THUMB_PX, THUMB_PX))
        im.save(os.path.join(folder, "thumbs", name), format="JPEG", quality=80)

    rel = os.path.relpath(os.path.join(folder, name), config.BASE_DIR)
    rel_thumb = os.path.relpath(os.path.join(folder, "thumbs", name), config.BASE_DIR)
    return rel, rel_thumb, taken


def _insert(conn, batch_id, original_name, rel, rel_thumb, taken):
    return conn.execute(
        "INSERT INTO product_images (batch_id, file_name, local_path, thumb_path, taken_at, sort_order) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        (batch_id, original_name, rel, rel_thumb, taken),
    ).lastrowid


def save_upload(batch_id, file_storage):
    data = file_storage.read()
    rel, thumb, taken = _store(batch_id, file_storage.filename, lambda: Image.open(io.BytesIO(data)))
    conn = get_db()
    _insert(conn, batch_id, os.path.basename(file_storage.filename), rel, thumb, taken)
    conn.commit()
    conn.close()


def list_folder(path):
    path = os.path.expanduser(path or "")
    if not os.path.isdir(path):
        raise FileNotFoundError(f"Not a folder: {path}")
    return sorted(
        f for f in os.listdir(path)
        if f.lower().endswith(IMAGE_EXTS) and not f.startswith(".")
    )


def import_folder(batch_id, path, job=None):
    """Import every image in a folder (non-recursive). Files already imported
    into this batch (same name) are skipped, so re-running is safe."""
    path = os.path.expanduser(path)
    names = list_folder(path)
    conn = get_db()
    have = {r["file_name"] for r in conn.execute(
        "SELECT file_name FROM product_images WHERE batch_id = ?", (batch_id,))}
    conn.close()
    todo = [n for n in names if n not in have]
    if job:
        job.set_total(len(todo))
        if len(names) != len(todo):
            job.log(f"Skipping {len(names) - len(todo)} already-imported photos")

    def one(name):
        stored = _store(batch_id, name, lambda: Image.open(os.path.join(path, name)))
        return name, stored

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, todo))
    conn = get_db()
    for name, (rel, thumb, taken) in results:
        _insert(conn, batch_id, name, rel, thumb, taken)
    conn.commit()
    conn.close()
    if job:
        job.log(f"Imported {len(results)} photos from {path}", advance=len(results))
    return len(results)


def ensure_thumb(img_row):
    """Legacy images (pre-v2) have no thumbnail; make one on demand."""
    if img_row.get("thumb_path"):
        return img_row["thumb_path"]
    src = os.path.join(config.BASE_DIR, img_row["local_path"] or "")
    if not os.path.exists(src):
        return ""
    thumb_dir = os.path.join(os.path.dirname(src), "thumbs")
    os.makedirs(thumb_dir, exist_ok=True)
    dst = os.path.join(thumb_dir, os.path.splitext(os.path.basename(src))[0] + ".jpg")
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((THUMB_PX, THUMB_PX))
        im.convert("RGB").save(dst, format="JPEG", quality=80)
    rel = os.path.relpath(dst, config.BASE_DIR)
    conn = get_db()
    conn.execute("UPDATE product_images SET thumb_path = ? WHERE id = ?", (rel, img_row["id"]))
    conn.commit()
    conn.close()
    return rel


def ungrouped(conn, batch_id):
    rows = conn.execute(
        "SELECT * FROM product_images WHERE batch_id = ? AND product_id IS NULL "
        "ORDER BY COALESCE(NULLIF(taken_at, ''), '9999'), file_name, id",
        (batch_id,),
    ).fetchall()
    return [image_dict(r) for r in rows]


def abs_path(rel):
    return os.path.join(config.BASE_DIR, rel)


def jpeg_b64(rel_path, max_px=1280, quality=82):
    with Image.open(abs_path(rel_path)) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=quality)
    return base64.standard_b64encode(buf.getvalue()).decode()


def _font(size):
    for p in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def contact_sheet_b64(entries, cols=6, cell=300):
    """entries: [(label_number, rel_path, caption)] -> base64 JPEG grid with a
    big number on every cell (the model answers in those numbers)."""
    rows = (len(entries) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * cell), "white")
    draw = ImageDraw.Draw(sheet)
    big, small = _font(38), _font(22)
    for i, (num, rel, caption) in enumerate(entries):
        x, y = (i % cols) * cell, (i // cols) * cell
        with Image.open(abs_path(rel)) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((cell - 8, cell - 8))
            sheet.paste(im.convert("RGB"), (x + (cell - im.width) // 2, y + (cell - im.height) // 2))
        draw.rectangle([x, y, x + 62, y + 46], fill="black")
        draw.text((x + 6, y + 2), str(num), fill="yellow", font=big)
        if caption:
            draw.rectangle([x, y + cell - 30, x + cell, y + cell], fill="black")
            draw.text((x + 6, y + cell - 28), caption, fill="white", font=small)
        draw.rectangle([x, y, x + cell - 1, y + cell - 1], outline="#888")
    buf = io.BytesIO()
    sheet.save(buf, format="JPEG", quality=80)
    return base64.standard_b64encode(buf.getvalue()).decode()
