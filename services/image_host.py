"""Photo hosting on imgbb — eBay fetches listing photos from these URLs."""
import base64
import os

import requests

import config
import settings
from db import get_db

IMGBB_ENDPOINT = "https://api.imgbb.com/1/upload"


def upload_to_imgbb(file_path):
    if not settings.get("imgbb_api_key"):
        raise RuntimeError("Add a free imgbb API key in Settings (Photo hosting) so eBay can load your photos")
    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    resp = requests.post(IMGBB_ENDPOINT, data={"key": settings.get("imgbb_api_key"), "image": b64}, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"imgbb error: {data}")
    return data["data"]["url"]


def host_images(item_id):
    """Upload any not-yet-hosted photos of an item; return all URLs in order."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, local_path, hosted_url FROM product_images WHERE product_id = ? ORDER BY sort_order, id",
        (item_id,),
    ).fetchall()
    urls = []
    for r in rows:
        url = r["hosted_url"]
        if not url:
            path = os.path.join(config.BASE_DIR, r["local_path"])
            if not os.path.exists(path):
                continue
            url = upload_to_imgbb(path)
            conn.execute("UPDATE product_images SET hosted_url = ? WHERE id = ?", (url, r["id"]))
            conn.commit()
        urls.append(url)
    conn.close()
    if not urls:
        raise RuntimeError("no photos could be hosted")
    return urls
