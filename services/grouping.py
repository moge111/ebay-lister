"""Grouping a batch's loose photos into items.

AI mode sends numbered contact sheets (30 photos at a time, in shoot order,
captioned with the seconds since the previous shot) and asks Claude which
consecutive photos show the same item. The last group of each sheet is carried
into the next sheet so an item split across the boundary stays together.
"""
import datetime
import json

from db import get_db, next_sku
from services import ai, photos

SHEET_SIZE = 30

GROUP_SYSTEM = """You sort a seller's product photos into items. Photos are numbered in the order they were shot. The seller photographs each item from several angles (front, back, sides, details, labels) before moving to the next, so photos of one item are almost always consecutive. A long time gap (caption "+Ns") often means a new item; a short gap usually means the same item.

Rules:
- Every photo number appears in exactly one group.
- Different copies of the same product (e.g. two identical boxes shot separately) are SEPARATE items unless they are clearly in the same shot as a set.
- Order each group's photos with the best front/cover shot first.
- label: 2-6 words naming the item."""

GROUP_SCHEMA = {
    "type": "object",
    "properties": {"groups": {"type": "array", "items": {
        "type": "object",
        "properties": {"photos": {"type": "array", "items": {"type": "integer"}}, "label": {"type": "string"}},
        "required": ["photos", "label"], "additionalProperties": False}}},
    "required": ["groups"], "additionalProperties": False,
}


def _gap_seconds(prev, cur):
    try:
        a = datetime.datetime.fromisoformat(prev["taken_at"])
        b = datetime.datetime.fromisoformat(cur["taken_at"])
        return max(0, int((b - a).total_seconds()))
    except (TypeError, ValueError, KeyError):
        return None


def _caption(prev, cur):
    gap = _gap_seconds(prev, cur) if prev else None
    return f"+{gap}s" if gap is not None else ""


def create_item(conn, batch_id, photo_ids, label=""):
    sku = next_sku(conn)
    item_id = conn.execute(
        "INSERT INTO products (title, sku, status, batch_id, ai) VALUES ('', ?, 'draft', ?, ?)",
        (sku, batch_id, json.dumps({"label": label})),
    ).lastrowid
    for order, pid in enumerate(photo_ids):
        conn.execute("UPDATE product_images SET product_id = ?, sort_order = ? WHERE id = ?", (item_id, order, pid))
    conn.commit()
    return item_id


def group_every(batch_id, n):
    conn = get_db()
    loose = photos.ungrouped(conn, batch_id)
    n = max(1, int(n))
    made = [create_item(conn, batch_id, [p["id"] for p in loose[i:i + n]]) for i in range(0, len(loose), n)]
    conn.close()
    return len(made)


def group_by_time(batch_id, gap_seconds=20):
    conn = get_db()
    loose = photos.ungrouped(conn, batch_id)
    groups, current = [], []
    for prev, cur in zip([None] + loose[:-1], loose):
        gap = _gap_seconds(prev, cur) if prev else None
        if current and (gap is None or gap > gap_seconds):
            groups.append(current)
            current = []
        current.append(cur["id"])
    if current:
        groups.append(current)
    for g in groups:
        create_item(conn, batch_id, g)
    conn.close()
    return len(groups)


def group_with_ai(batch_id, job):
    conn = get_db()
    loose = photos.ungrouped(conn, batch_id)
    conn.close()
    if not loose:
        job.log("No loose photos to group")
        return 0
    job.set_total(len(loose))
    made = 0
    carry = []  # photos of the unfinished last group from the previous sheet
    pos = 0
    while pos < len(loose) or carry:
        window = carry + loose[pos:pos + SHEET_SIZE - len(carry)]
        pos += len(window) - len(carry)
        final_sheet = pos >= len(loose)
        entries = [(i + 1, p["local_path"], _caption(window[i - 1] if i else None, p)) for i, p in enumerate(window)]
        sheet = photos.contact_sheet_b64(entries)
        result = ai.ask_json(
            [{"image": sheet},
             {"text": f"Photos 1-{len(window)}. Group them into items."
                      + ("" if final_sheet else " More photos follow after this sheet, so the last item may continue.")}],
            GROUP_SCHEMA, GROUP_SYSTEM, max_tokens=4000,
        )
        groups = _clean_groups(result.get("groups") or [], len(window))
        if not final_sheet and len(groups) > 1 and len(groups[-1][0]) < SHEET_SIZE // 2:
            carry = [window[i - 1] for i in groups[-1][0]]
            groups = groups[:-1]
        else:
            carry = []
        conn = get_db()
        for idxs, label in groups:
            create_item(conn, batch_id, [window[i - 1]["id"] for i in idxs], label)
            made += 1
            job.log(f"• {label or 'item'} ({len(idxs)} photos)", advance=len(idxs))
        conn.close()
        if final_sheet and not carry:
            break
    return made


def _clean_groups(groups, n):
    """Drop invalid/duplicate numbers; any photo the model skipped becomes its own item."""
    seen, out = set(), []
    for g in groups:
        idxs = [i for i in g.get("photos") or [] if isinstance(i, int) and 1 <= i <= n and i not in seen]
        seen.update(idxs)
        if idxs:
            out.append((idxs, g.get("label", "")))
    for i in range(1, n + 1):
        if i not in seen:
            out.append(([i], "unmatched photo"))
    out.sort(key=lambda g: min(g[0]))
    return out
