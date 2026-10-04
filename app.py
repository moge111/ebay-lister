"""eBay Lister — JSON API + single-page UI (static/). Run: python3 app.py"""
import datetime
import os

from flask import Flask, abort, jsonify, redirect, request, send_file, send_from_directory

import config
import jobs
import settings
from db import get_db, get_item, get_items, init_db, item_kind, update_item
from services import ai, catalog, comps, csv_export, ebay, grouping, listing, photos

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # big photo batches
init_db()

EDITABLE = {"title", "price", "category_id", "category_name", "condition_id", "condition_descriptors",
            "aspects", "description", "brand", "package", "quantity", "notes", "cert_number", "grade",
            "grader", "status", "cost"}


def _bad(msg, code=400):
    return jsonify({"error": msg}), code


def _item_json(item, with_issues=True):
    item["kind"] = item_kind(item)
    if not item.get("category_name") and str(item.get("category_id") or "") in catalog.BY_ID:
        item["category_name"] = catalog.BY_ID[str(item["category_id"])][1]
    if with_issues:
        item["issues"] = listing.issues(item)
    return item


# ------------------------------------------------------------------ pages / files

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/photos/<path:filepath>")
def serve_photo(filepath):
    if ".." in filepath.split("/") or not filepath.startswith(config.SERVABLE_PREFIXES):
        abort(404)
    return send_from_directory(config.BASE_DIR, filepath)


@app.route("/thumb/<int:image_id>")
def serve_thumb(image_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM product_images WHERE id = ?", (image_id,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    rel = photos.ensure_thumb(dict(row))
    if not rel:
        abort(404)
    resp = send_file(os.path.join(config.BASE_DIR, rel))
    resp.cache_control.max_age = 86400
    return resp


# ------------------------------------------------------------------ overview

@app.route("/api/state")
def api_state():
    conn = get_db()
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM products GROUP BY status")}
    conn.close()
    return jsonify({
        "counts": counts,
        "ai_backend": ai.backend(),
        "ebay": {"app_keys": ebay.has_app_keys(), "connected": ebay.is_connected(), "can_promote": ebay.can_promote()},
        "comps": comps.token_status(),
        "jobs": [j for j in jobs.recent(5) if j["status"] == "running"],
        "categories": [{"key": k, "id": cid, "name": name} for k, (cid, name, _) in catalog.CATEGORIES.items()],
        "condition_names": catalog.CONDITION_NAMES,
        "packages": config.PACKAGE_SIZES,
        "import_dir": config.DEFAULT_IMPORT_DIR,
    })


# ------------------------------------------------------------------ batches

@app.route("/api/batches")
def api_batches():
    conn = get_db()
    rows = conn.execute("""
        SELECT b.*,
          (SELECT COUNT(*) FROM products p WHERE p.batch_id = b.id) AS items,
          (SELECT COUNT(*) FROM products p WHERE p.batch_id = b.id AND p.status = 'listed') AS listed,
          (SELECT COUNT(*) FROM product_images i WHERE i.batch_id = b.id AND i.product_id IS NULL) AS loose
        FROM batches b ORDER BY b.id DESC""").fetchall()
    legacy = conn.execute("SELECT COUNT(*) n FROM products WHERE batch_id IS NULL").fetchone()["n"]
    conn.close()
    return jsonify({"batches": [dict(r) for r in rows], "legacy_items": legacy})


@app.route("/api/batches", methods=["POST"])
def api_create_batch():
    name = (request.json or {}).get("name") or datetime.date.today().strftime("Batch %b %-d")
    conn = get_db()
    batch_id = conn.execute("INSERT INTO batches (name) VALUES (?)", (name,)).lastrowid
    conn.commit()
    conn.close()
    return jsonify({"id": batch_id, "name": name})


@app.route("/api/batches/<int:batch_id>", methods=["PATCH"])
def api_rename_batch(batch_id):
    conn = get_db()
    conn.execute("UPDATE batches SET name = ? WHERE id = ?", ((request.json or {}).get("name", "Batch"), batch_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/batches/<int:batch_id>", methods=["DELETE"])
def api_delete_batch(batch_id):
    """Removes the batch's DB rows (items + photos). Photo files stay on disk."""
    conn = get_db()
    conn.execute("DELETE FROM products WHERE batch_id = ? AND status != 'listed'", (batch_id,))
    conn.execute("DELETE FROM product_images WHERE batch_id = ? AND product_id IS NULL", (batch_id,))
    left = conn.execute("SELECT COUNT(*) n FROM products WHERE batch_id = ?", (batch_id,)).fetchone()["n"]
    if not left:
        conn.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "kept_listed": left})


@app.route("/api/batches/<int:batch_id>")
def api_batch(batch_id):
    conn = get_db()
    batch = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if not batch:
        conn.close()
        return _bad("no such batch", 404)
    loose = photos.ungrouped(conn, batch_id)
    items = [_item_json(i) for i in get_items(conn, "batch_id = ?", (batch_id,))]
    conn.close()
    items.sort(key=lambda i: i["id"])
    return jsonify({"batch": dict(batch), "loose": loose, "items": items})


@app.route("/api/batches/<int:batch_id>/upload", methods=["POST"])
def api_upload(batch_id):
    saved, errors = 0, []
    for f in request.files.getlist("photos"):
        try:
            photos.save_upload(batch_id, f)
            saved += 1
        except Exception as e:  # noqa: BLE001 — report per file
            errors.append(f"{f.filename}: {e}")
    return jsonify({"saved": saved, "errors": errors})


@app.route("/api/folder-preview")
def api_folder_preview():
    path = request.args.get("path") or config.DEFAULT_IMPORT_DIR
    try:
        names = photos.list_folder(path)
    except FileNotFoundError as e:
        return _bad(str(e))
    return jsonify({"path": os.path.expanduser(path), "count": len(names),
                    "first": names[0] if names else "", "last": names[-1] if names else ""})


@app.route("/api/batches/<int:batch_id>/import-folder", methods=["POST"])
def api_import_folder(batch_id):
    path = (request.json or {}).get("path") or config.DEFAULT_IMPORT_DIR
    try:
        photos.list_folder(path)
    except FileNotFoundError as e:
        return _bad(str(e))
    job_id = jobs.start("import", f"Importing photos from {os.path.basename(os.path.expanduser(path))}",
                        lambda job: photos.import_folder(batch_id, path, job), batch_id=batch_id)
    return jsonify({"job": job_id})


@app.route("/api/batches/<int:batch_id>/autogroup", methods=["POST"])
def api_autogroup(batch_id):
    body = request.json or {}
    mode = body.get("mode", "ai")
    if mode == "every":
        return jsonify({"made": grouping.group_every(batch_id, body.get("n", 2))})
    if mode == "time":
        return jsonify({"made": grouping.group_by_time(batch_id, int(body.get("gap", 20)))})
    job_id = jobs.start("group", "AI grouping photos into items",
                        lambda job: job.log(f"Made {grouping.group_with_ai(batch_id, job)} items"), batch_id=batch_id)
    return jsonify({"job": job_id})


@app.route("/api/batches/<int:batch_id>/group", methods=["POST"])
def api_group(batch_id):
    ids = [int(i) for i in (request.json or {}).get("photo_ids", [])]
    if not ids:
        return _bad("select photos first")
    conn = get_db()
    item_id = grouping.create_item(conn, batch_id, ids)
    conn.close()
    return jsonify({"id": item_id})


@app.route("/api/batches/<int:batch_id>/process", methods=["POST"])
def api_process(batch_id):
    """Identify + price every item in the batch that isn't done yet."""
    body = request.json or {}
    conn = get_db()
    if body.get("ids"):
        ids = [int(i) for i in body["ids"]]
    else:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM products WHERE batch_id = ? AND status = 'draft' AND (title = '' OR ai NOT LIKE '%\"confidence\"%')",
            (batch_id,))]
    conn.close()
    return jsonify({"job": _start_process(ids, body.get("steps", ["identify", "price"]), batch_id)})


def _start_process(ids, steps, batch_id=None):
    label = {("identify", "price"): "Identifying + pricing", ("identify",): "Identifying", ("price",): "Pricing"}
    title = f"{label.get(tuple(steps), 'Processing')} {len(ids)} item{'s' * (len(ids) != 1)}"

    def run(job):
        job.set_total(len(ids))
        auth_failed = {}

        def one(item_id):
            if auth_failed:
                job.log(f"– #{item_id}: skipped (AI backend unavailable)", advance=1)
                return
            msg = []
            if "identify" in steps:
                try:
                    data = listing.identify_item(item_id)
                except ai.AIAuthError as e:
                    auth_failed["e"] = e
                    job.log(f"✗ {e}", advance=1)
                    return
                msg.append(f"{data['title'][:60]} ({data['confidence']})")
            if "price" in steps:
                try:
                    r = listing.price_item(item_id, overwrite="identify" not in steps)
                    msg.append(f"${r['suggested']:.2f} from {r['n']} comps" if r.get("suggested") else "no comps")
                except comps.CompsUnavailable as e:
                    msg.append(f"price skipped: {e}")
            job.log(f"✓ #{item_id} " + " · ".join(msg), advance=1)

        job.map(one, ids, workers=int(settings.get("ai_workers") or 4) if "identify" in steps else 6,
                label=lambda i: f"#{i}")

    return jobs.start("process", title, run, total=len(ids), batch_id=batch_id)


# ------------------------------------------------------------------ items

@app.route("/api/items")
def api_items():
    where, params = ["1=1"], []
    if request.args.get("batch") == "none":
        where.append("batch_id IS NULL")
    elif request.args.get("batch"):
        where.append("batch_id = ?")
        params.append(int(request.args["batch"]))
    if request.args.get("status"):
        where.append("status = ?")
        params.append(request.args["status"])
    conn = get_db()
    items = [_item_json(i) for i in get_items(conn, " AND ".join(where), params)]
    conn.close()
    return jsonify({"items": items})


@app.route("/api/items/<int:item_id>")
def api_item(item_id):
    conn = get_db()
    item = get_item(conn, item_id)
    conn.close()
    if not item:
        return _bad("no such item", 404)
    item = _item_json(item)
    item["full_description"] = listing.full_description(item)
    return jsonify(item)


@app.route("/api/items/<int:item_id>", methods=["PATCH"])
def api_update_item(item_id):
    body = request.json or {}
    fields = {k: v for k, v in body.items() if k in EDITABLE}
    try:
        if "price" in fields:
            fields["price"] = round(float(fields["price"]), 2) if fields["price"] not in ("", None) else None
        if "quantity" in fields:
            fields["quantity"] = max(1, int(fields["quantity"] or 1))
    except (TypeError, ValueError):
        return _bad("price/quantity must be numbers")
    if "title" in fields:
        fields["title"] = (fields["title"] or "").strip()
        fields["title_locked"] = 1
    conn = get_db()
    item = get_item(conn, item_id)
    if not item:
        conn.close()
        return _bad("no such item", 404)
    # Changing category or grading re-derives a valid condition + descriptors.
    if {"category_id", "cert_number", "grade", "grader", "condition_id"} & fields.keys() and "condition_descriptors" not in fields:
        merged = {**item, **fields}
        graded = {"grader": merged.get("grader") or "PSA", "grade": merged.get("grade"), "cert": merged.get("cert_number")} \
            if merged.get("grade") else None
        cond, desc = listing.resolve_condition(str(merged.get("category_id") or ""), str(merged.get("condition_id") or "3000"), graded)
        fields.update(condition_id=cond, condition_descriptors=desc)
    update_item(conn, item_id, fields)
    item = _item_json(get_item(conn, item_id))
    conn.close()
    return jsonify(item)


@app.route("/api/items/<int:item_id>", methods=["DELETE"])
def api_delete_item(item_id):
    """Deletes the item; its photos go back to the batch's loose pile."""
    conn = get_db()
    conn.execute("UPDATE product_images SET product_id = NULL WHERE product_id = ? AND batch_id IS NOT NULL", (item_id,))
    conn.execute("DELETE FROM products WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/items/bulk", methods=["POST"])
def api_bulk():
    body = request.json or {}
    ids = [int(i) for i in body.get("ids", [])]
    action = body.get("action")
    if not ids:
        return _bad("nothing selected")
    if action in ("identify", "price", "process"):
        steps = ["identify", "price"] if action == "process" else [action]
        return jsonify({"job": _start_process(ids, steps)})
    if action == "publish":
        return jsonify({"job": _start_publish(ids)})
    if action == "csv":
        path, n, skipped = csv_export.export(ids, action=body.get("csv_action", "Add"))
        return jsonify({"file": os.path.basename(path), "count": n, "skipped": skipped})
    conn = get_db()
    marks = ",".join("?" * len(ids))
    if action == "delete":
        conn.execute(f"UPDATE product_images SET product_id = NULL WHERE product_id IN ({marks}) AND batch_id IS NOT NULL", ids)
        conn.execute(f"DELETE FROM products WHERE id IN ({marks})", ids)
    elif action == "status" and body.get("status") in ("draft", "exported", "listed", "ended", "sold"):
        conn.execute(f"UPDATE products SET status = ? WHERE id IN ({marks})", [body["status"], *ids])
    elif action == "set":
        fields = {k: v for k, v in (body.get("fields") or {}).items() if k in ("package", "quantity", "price")}
        for i in ids:
            update_item(conn, i, fields)
    elif action == "merge":
        target = ids[0]
        order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) m FROM product_images WHERE product_id = ?", (target,)).fetchone()["m"]
        for other in ids[1:]:
            for r in conn.execute("SELECT id FROM product_images WHERE product_id = ? ORDER BY sort_order", (other,)).fetchall():
                order += 1
                conn.execute("UPDATE product_images SET product_id = ?, sort_order = ? WHERE id = ?", (target, order, r["id"]))
            conn.execute("DELETE FROM products WHERE id = ?", (other,))
    else:
        conn.close()
        return _bad(f"unknown action {action}")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/items/<int:item_id>/photos", methods=["PUT"])
def api_set_photos(item_id):
    """Set an item's photos + order (ids may come from loose photos or other items)."""
    ids = [int(i) for i in (request.json or {}).get("photo_ids", [])]
    conn = get_db()
    current = [r["id"] for r in conn.execute("SELECT id FROM product_images WHERE product_id = ?", (item_id,))]
    for pid in set(current) - set(ids):
        conn.execute("UPDATE product_images SET product_id = NULL WHERE id = ? AND batch_id IS NOT NULL", (pid,))
    for order, pid in enumerate(ids):
        conn.execute("UPDATE product_images SET product_id = ?, sort_order = ? WHERE id = ?", (item_id, order, pid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/items/<int:item_id>/split", methods=["POST"])
def api_split(item_id):
    """Move the given photos out of this item into a new item."""
    ids = [int(i) for i in (request.json or {}).get("photo_ids", [])]
    conn = get_db()
    item = get_item(conn, item_id)
    if not ids or len(ids) >= len(item["images"]):
        conn.close()
        return _bad("pick some (not all) photos to split off")
    new_id = grouping.create_item(conn, item["batch_id"], ids)
    conn.close()
    return jsonify({"id": new_id})


@app.route("/api/photos/<int:photo_id>", methods=["DELETE"])
def api_delete_photo(photo_id):
    conn = get_db()
    conn.execute("DELETE FROM product_images WHERE id = ?", (photo_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/items/<int:item_id>/identify", methods=["POST"])
def api_identify(item_id):
    hint = (request.json or {}).get("hint", "")
    try:
        listing.identify_item(item_id, hint=hint)
    except ai.AIError as e:
        return _bad(str(e), 502)
    return api_item(item_id)


@app.route("/api/items/<int:item_id>/comps", methods=["POST"])
def api_comps(item_id):
    body = request.json or {}
    try:
        listing.price_item(item_id, query=body.get("query"), overwrite=False, fill_empty=False)
    except (comps.CompsUnavailable, ValueError) as e:
        return _bad(str(e), 502)
    return api_item(item_id)


@app.route("/api/items/<int:item_id>/publish", methods=["POST"])
def api_publish(item_id):
    try:
        listing_id = listing.publish_item(item_id)
    except (ebay.EbayError, ValueError, RuntimeError) as e:
        conn = get_db()
        update_item(conn, item_id, {"publish_error": str(e)})
        conn.close()
        return _bad(str(e), 502)
    return jsonify({"listing_id": listing_id, "item": api_item(item_id).json})


@app.route("/api/items/<int:item_id>/end", methods=["POST"])
def api_end(item_id):
    try:
        listing.end_listing(item_id)
    except (ebay.EbayError, ValueError) as e:
        return _bad(str(e), 502)
    return api_item(item_id)


def _start_publish(ids):
    def run(job):
        job.set_total(len(ids))

        def one(item_id):
            try:
                lid = listing.publish_item(item_id)
                job.log(f"✓ #{item_id} live — ebay.com/itm/{lid}", advance=1)
            except (ebay.EbayError, ValueError, RuntimeError) as e:
                conn = get_db()
                update_item(conn, item_id, {"publish_error": str(e)})
                conn.close()
                job.log(f"✗ #{item_id}: {e}", advance=1)

        job.map(one, ids, workers=3, label=lambda i: f"#{i}")
    return jobs.start("publish", f"Publishing {len(ids)} item{'s' * (len(ids) != 1)} to eBay", run, total=len(ids))


# ------------------------------------------------------------------ categories

@app.route("/api/categories/suggest")
def api_category_suggest():
    q = (request.args.get("q") or "").strip()
    known = [{"id": cid, "name": name, "path": name, "known": True}
             for _, (cid, name, _) in catalog.CATEGORIES.items() if q.lower() in name.lower()] if q else []
    remote = []
    if q and ebay.has_app_keys():
        try:
            remote = ebay.suggest_categories(q)
        except ebay.EbayError as e:
            return jsonify({"results": known, "error": str(e)})
    return jsonify({"results": known + remote})


@app.route("/api/categories/<category_id>/meta")
def api_category_meta(category_id):
    out = {"aspects": listing.aspect_defs(category_id), "card": catalog.is_card(category_id), "source": "builtin"}
    conditions = None
    if ebay.has_app_keys():
        out["source"] = "ebay"
        try:
            conditions = ebay.category_conditions(category_id)["conditions"]
        except ebay.EbayError as e:
            out["error"] = str(e)
    if not conditions:
        ids = ["1000", "3000"] if category_id in catalog.NEW_USED_ONLY else \
            ["1000", "1500", "2750", "3000", "4000", "5000", "6000", "7000"]
        conditions = [{"id": i, "name": catalog.CONDITION_NAMES[i], "descriptors": []} for i in ids]
    out["conditions"] = conditions
    return jsonify(out)


# ------------------------------------------------------------------ jobs / exports

@app.route("/api/jobs/<int:job_id>")
def api_job(job_id):
    job = jobs.get(job_id)
    return jsonify(job) if job else _bad("no such job", 404)


@app.route("/api/jobs")
def api_jobs():
    return jsonify({"jobs": jobs.recent(15)})


@app.route("/api/exports/<path:name>")
def api_download_export(name):
    return send_from_directory(config.OUTPUT_DIR, os.path.basename(name), as_attachment=True)


# ------------------------------------------------------------------ settings / eBay connect

@app.route("/api/settings")
def api_settings():
    return jsonify(settings.public())


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    body = request.json or {}
    allowed = set(config.DEFAULT_SETTINGS) - {"ebay_refresh_token", "ebay_user_token", "ebay_user_token_expires_at", "ebay_scopes",
                                              "ebay_refresh_expires_at", "ebay_location_key"}
    changes = {k: v for k, v in body.items() if k in allowed and not (k in settings.SECRET_KEYS and v in ("", True, None))}
    changes = {k: v.strip() if isinstance(v, str) else v for k, v in changes.items()}
    if "-SBX-" in changes.get("ebay_app_id", "") or changes.get("ebay_cert_id", "").startswith("SBX-"):
        return _bad("That's a Sandbox key (SBX). Use the Production keyset (PRD) — sandbox can't create real listings.")
    if {"ebay_app_id", "ebay_cert_id"} & changes.keys():
        ebay._app_token.update(token="", exp=0)
    settings.update(**changes)
    return jsonify(settings.public())


@app.route("/api/settings/test-ai", methods=["POST"])
def api_test_ai():
    return jsonify(ai.health())


@app.route("/api/ebay/connect")
def api_ebay_connect():
    try:
        return redirect(ebay.consent_url())
    except ebay.NotConfigured as e:
        return _bad(str(e))


@app.route("/ebay/callback")
def ebay_callback():
    code = request.args.get("code")
    if not code:
        return redirect("/#/settings?ebay=denied")
    try:
        ebay.exchange_code(code)
    except ebay.EbayError as e:
        return redirect(f"/#/settings?ebay_error={e}")
    return redirect("/#/settings?ebay=connected")


@app.route("/api/ebay/code", methods=["POST"])
def api_ebay_code():
    try:
        ebay.exchange_code((request.json or {}).get("code", ""))
    except ebay.EbayError as e:
        return _bad(str(e))
    return jsonify({"ok": True})


@app.route("/api/ebay/disconnect", methods=["POST"])
def api_ebay_disconnect():
    settings.update(ebay_refresh_token="", ebay_user_token="", ebay_user_token_expires_at=0)
    return jsonify({"ok": True})


@app.route("/api/ebay/policies")
def api_ebay_policies():
    try:
        return jsonify(ebay.policies())
    except ebay.EbayError as e:
        return _bad(str(e), 502)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=config.PORT, debug=False, threaded=True)
