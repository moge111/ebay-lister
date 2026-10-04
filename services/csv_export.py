"""File Exchange CSV — the fallback path when the Sell API isn't connected.

Item specifics come straight from each item's `aspects` (one C:<name> column
per aspect used by any exported row), so new categories need no code here.
"""
import csv
import datetime
import os

import config
import settings
from db import get_db, get_items, update_item
from services.image_host import host_images
from services.listing import full_description, issues, shipping_package

ACTION_HEADER = "*Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)"


def export(item_ids, action="Add", mark_exported=True):
    """Write a CSV for the given items. Photos are hosted on the way.
    Returns (path, exported_count, skipped[{id, sku, title, issues}])."""
    conn = get_db()
    marks = ",".join("?" * len(item_ids)) or "NULL"
    items = get_items(conn, f"id IN ({marks})", list(item_ids))
    conn.close()

    rows, skipped = [], []
    for item in sorted(items, key=lambda i: i["id"]):
        problems = issues(item)
        if action == "Add" and problems:
            skipped.append({"id": item["id"], "sku": item["sku"], "title": item["title"], "issues": problems})
            continue
        try:
            urls = host_images(item["id"])
        except Exception as e:  # noqa: BLE001 — report per item
            skipped.append({"id": item["id"], "sku": item["sku"], "title": item["title"], "issues": [f"photo hosting: {e}"]})
            continue
        rows.append((item, urls))

    aspect_names = sorted({name for item, _ in rows for name in (item["aspects"] or {})})
    header = [ACTION_HEADER, "CustomLabel", "*Category", "*Title", "Product:UPC", "*StartPrice", "*Quantity",
              "PicURL", "*ConditionID", "CD:27501", "CD:27502", "CDA:27503", "CD:40001", "Description", "*Format",
              *[f"C:{n}" for n in aspect_names],
              "ShippingProfileName", "ReturnProfileName", "PaymentProfileName", "Promoted", "BestOfferEnabled",
              "*Duration", "*Location", "PostalCode"]

    s = settings.load()
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    path = os.path.join(config.OUTPUT_DIR, f"ebay_{action.lower()}_{stamp}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for item, urls in rows:
            desc = item["condition_descriptors"] or {}
            aspects = item["aspects"] or {}
            w.writerow([
                action, item["sku"], item["category_id"], item["title"], item.get("upc") or "Does not apply",
                f"{float(item['price'] or 0):.2f}", item.get("quantity") or 1, "|".join(urls[:24]),
                item["condition_id"], desc.get("27501", ""), desc.get("27502", ""), desc.get("27503", ""),
                desc.get("40001", ""), full_description(item).replace("\n", "<br>"), "FixedPrice",
                *[aspects.get(n, "") for n in aspect_names],
                (s.get("csv_shipping_profiles") or {}).get(shipping_package(item), ""),
                s.get("csv_return_profile", ""), s.get("csv_payment_profile", ""), s.get("promoted_ad_rate", ""), 1, "GTC",
                f"{s.get('seller_city', '')}, {s.get('seller_state', '')}".strip(", "), s.get("seller_postal_code", ""),
            ])

    if mark_exported and action == "Add":
        conn = get_db()
        for item, _ in rows:
            if item["status"] != "listed":
                update_item(conn, item["id"], {"status": "exported", "exported_at": datetime.datetime.now().isoformat(timespec="seconds")})
        conn.close()
    return path, len(rows), skipped

