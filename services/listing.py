"""Turning photos into a publishable eBay listing.

identify_item  -> Claude reads the photos: title, category, condition, specifics
price_item     -> sold comps -> suggested price
issues         -> what still blocks publishing (drives the UI's "needs work")
publish_item   -> host photos, push inventory item + offer, publish via Sell API
"""
import datetime
import html
import re

import config
import settings
from db import get_db, get_item, update_item, now
from services import ai, catalog, comps, ebay, photos
from services.image_host import host_images

AI_CONDITIONS = {
    "new": "1000", "new_other": "1500", "like_new": "2750", "very_good": "4000",
    "good": "5000", "acceptable": "6000", "used": "3000", "for_parts": "7000",
}
# If eBay doesn't allow a condition in a category, fall back to the closest allowed one.
CONDITION_FALLBACKS = {
    "1500": ["1500", "3000"], "2750": ["2750", "3000", "4000"], "3000": ["3000", "4000", "5000"],
    "4000": ["4000", "3000", "5000"], "5000": ["5000", "3000", "4000"], "6000": ["6000", "3000", "5000"],
}

IDENTIFY_SYSTEM = """You identify items from photos for eBay listings. The seller photographs their own inventory (toys, collectibles, trading cards, video games, electronics) and you write the listing.

Rules:
- TITLE: max 80 characters, written for eBay keyword search. Lead with the most-searched noun (brand + character/player/product), then identifiers you can actually read: set name, card number, parallel/serial (#/75), year, model number, platform spelled out ("Sony PlayStation 5 PS5"). Plain keywords, no parentheses, no filler words ("Rare", "L@@K"), no ALL CAPS. For trading cards include year, manufacturer, set, player/character, card number, parallel.
- YEAR: the product year is the copyright year printed on the item (e.g. "© 2026 The Topps Company" on a card back, or the year on the box). Do NOT use years from stats rows ("2025 season", "YR 2025"), game dates, or career notes. Products newer than your training data are real — never "correct" a printed year to one you recognize.
- HONESTY: never claim sealed / new / complete / manual included / tested / working unless the photos clearly show it. Put anything uncertain in "notes", never in the title or description.
- CONDITION: new = factory sealed or unopened retail packaging; new_other = open box but unused; like_new/very_good/good/acceptable = used media & electronics graded by wear; used = any other used item; for_parts = visibly broken. Trading cards: use "used" for raw cards (the app handles card grading); if it's a graded slab, fill "graded".
- CATEGORY: choose category_key from the list when one fits; otherwise "other" and write category_query (2-5 words naming the kind of item, e.g. "tamagotchi virtual pet").
- ASPECTS: fill every eBay item specific you can determine from the photos (Brand, Character, Franchise, Type, Model, Color, Platform, Game Name, Sport, Player/Athlete, Team, Set, Card Number, Parallel/Variety, Year Manufactured, Manufacturer, Card Name, Game, Autographed, Features, Material, Size, Scale...). Values short and canonical.
- PACKAGE (for shipping): card = single raw/graded card or other flat item under 3 oz that fits in an envelope; small = fits in a small box/padded mailer under 1 lb (most figures, games, blister packs); medium = 1-5 lb box (ETBs, LEGO sets, big boxed toys, tumblers); large = 5-10 lb.
- search_query: 3-7 words that find this exact item's sold listings (name + set/model + key number). No condition words.
- flaws: ONLY concrete visible defects a buyer would care about, stated the way a seller would ("Soft corner on the bottom left.", "Crease on the top box flap.", "Disc has light surface scratches."). Empty string if nothing is clearly visible. Never describe what the item looks like, never say "appears to be in good condition"."""


def _identify_schema():
    keys = list(catalog.CATEGORIES) + ["other"]
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "brand": {"type": "string"},
            "category_key": {"type": "string", "enum": keys},
            "category_query": {"type": "string"},
            "condition": {"type": "string", "enum": list(AI_CONDITIONS)},
            "graded": {
                "type": "object",
                "properties": {"grader": {"type": "string"}, "grade": {"type": "string"}, "cert": {"type": "string"}},
                "required": ["grader", "grade", "cert"], "additionalProperties": False,
            },
            "aspects": {
                "type": "array",
                "items": {"type": "object", "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                          "required": ["name", "value"], "additionalProperties": False},
            },
            "flaws": {"type": "string"},
            "package": {"type": "string", "enum": config.PACKAGE_SIZES},
            "search_query": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "notes": {"type": "string"},
        },
        "required": ["title", "brand", "category_key", "category_query", "condition", "graded", "aspects",
                     "flaws", "package", "search_query", "confidence", "notes"],
        "additionalProperties": False,
    }


def _category_list_text():
    return "\n".join(f"- {k}: {name}" for k, (_, name, _) in catalog.CATEGORIES.items())


# ------------------------------------------------------------------ identify

def identify_item(item_id, hint=""):
    conn = get_db()
    item = get_item(conn, item_id)
    conn.close()
    if not item["images"]:
        raise ValueError("item has no photos")

    blocks = [{"image": photos.jpeg_b64(img["local_path"])} for img in item["images"][:5]]
    text = ("Categories you can pick (category_key):\n" + _category_list_text()
            + f"\n\nToday is {datetime.date.today().isoformat()}."
            + f"\nThese {len(blocks)} photos show ONE item (the first is the cover). Identify it.")
    if hint:
        text += f"\nSeller's hint: {hint}"
    blocks.append({"text": text})
    data = ai.ask_json(blocks, _identify_schema(), IDENTIFY_SYSTEM)

    category_id, category_name = _resolve_category(data)
    graded = data.get("graded") or {}
    is_graded = bool(graded.get("grade") and graded.get("grader"))
    condition_id, descriptors = resolve_condition(category_id, AI_CONDITIONS.get(data["condition"], "3000"),
                                                  graded if is_graded else None)
    ai_aspects = {a["name"].strip(): a["value"].strip() for a in data.get("aspects") or [] if a["value"].strip()}
    if data.get("brand"):
        ai_aspects.setdefault("Brand", data["brand"])
    aspects, missing = resolve_aspects(category_id, ai_aspects)
    if missing:
        aspects.update(_fill_missing_aspects(category_id, data, aspects, missing))

    fields = {
        "brand": data.get("brand", ""),
        "category_id": category_id,
        "category_name": category_name,
        "condition_id": condition_id,
        "condition_descriptors": descriptors,
        "aspects": aspects,
        "package": data.get("package") or "small",
        "description": data.get("flaws", "").strip(),
        "ai": {
            "confidence": data.get("confidence"), "notes": data.get("notes", ""),
            "search_query": data.get("search_query", ""), "category_query": data.get("category_query", ""),
            "at": now(),
        },
        "publish_error": "",
    }
    if is_graded:
        fields.update(grader=graded["grader"].upper(), grade=graded["grade"], cert_number=graded.get("cert", ""))
    if not item["title_locked"]:
        fields["title"] = fit_title(data["title"])
    conn = get_db()
    update_item(conn, item_id, fields)
    conn.close()
    return data


def fit_title(title, limit=80):
    """Trim to eBay's 80 chars on a word boundary (never "Refracto")."""
    title = " ".join((title or "").split())
    if len(title) <= limit:
        return title
    return title[:limit + 1].rsplit(" ", 1)[0].rstrip(" -,/")


def _resolve_category(data):
    key = data.get("category_key")
    if key in catalog.CATEGORIES:
        cid, name, _ = catalog.CATEGORIES[key]
        return cid, name
    query = data.get("category_query") or data.get("title", "")
    if ebay.has_app_keys() and query:
        try:
            suggestions = ebay.suggest_categories(query)
            if suggestions:
                return suggestions[0]["id"], suggestions[0]["path"]
        except ebay.EbayError:
            pass
    return "", ""


def resolve_condition(category_id, condition_id, graded=None):
    """-> (condition_id, descriptors) valid for the category."""
    if catalog.is_card(category_id):
        if graded:
            grade = re.sub(r"[^0-9.]", "", str(graded.get("grade", ""))) or "10"
            desc = {"27501": catalog.GRADER_IDS.get(str(graded.get("grader", "PSA")).upper(), "275010"),
                    "27502": catalog.GRADE_IDS.get(grade, "275020")}
            if graded.get("cert"):
                desc["27503"] = graded["cert"]
            return "2750", desc
        return "4000", dict(catalog.UNGRADED_CARD_DESCRIPTOR)

    allowed = None
    if category_id and ebay.has_app_keys():
        try:
            allowed = {c["id"] for c in ebay.category_conditions(category_id)["conditions"]}
        except ebay.EbayError:
            allowed = None
    if not allowed and category_id in catalog.NEW_USED_ONLY:
        allowed = {"1000", "3000"}
    if allowed and condition_id not in allowed:
        for alt in CONDITION_FALLBACKS.get(condition_id, []):
            if alt in allowed:
                return alt, {}
    return condition_id, {}


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def aspect_defs(category_id):
    """eBay's aspect list for a category, or the built-in fallback."""
    if category_id and ebay.has_app_keys():
        try:
            return ebay.category_aspects(category_id)
        except ebay.EbayError:
            pass
    fallback = catalog.FALLBACK_ASPECTS.get(str(category_id or ""), {})
    return [{"name": n, "required": True, "recommended": False, "mode": "FREE_TEXT", "multi": False,
             "values": [v] if v else []} for n, v in fallback.items()]


def _match_value(value, allowed):
    v = value.lower().strip()
    for a in allowed:
        if a.lower() == v:
            return a
    for a in allowed:
        if v in a.lower() or a.lower() in v:
            return a
    return None


def resolve_aspects(category_id, ai_aspects):
    """Map free-form AI aspects onto the category's aspect names/values.
    -> (aspects {name: value}, missing required names)."""
    defs = aspect_defs(category_id)
    by_norm = {_norm(k): v for k, v in ai_aspects.items()}
    exact = {_norm(d["name"]) for d in defs} & by_norm.keys()
    out, used = {}, set()
    for d in defs:
        key = _norm(d["name"])
        value = by_norm.get(key)
        if value is None:  # "Player/Athlete" vs "Player", "Year Manufactured" vs "Year"
            for k, v in by_norm.items():
                # Keys that exactly match another aspect ("Type") must not leak into "Vehicle Type".
                if k and k not in exact and k not in used and (k in key or key in k):
                    value, key = v, k
                    break
        if value is not None:
            used.add(key)
            if d["mode"] == "SELECTION_ONLY" and d["values"]:
                value = _match_value(value, d["values"])
            if value:
                out[d["name"]] = value
                continue
        if d["required"] and d["values"] and len(d["values"]) == 1:
            out[d["name"]] = d["values"][0]  # fallback default (e.g. Funko Type=Pop!)
    for k, v in ai_aspects.items():  # keep extra specifics the AI found (eBay allows custom ones)
        if _norm(k) not in used and k not in out and len(out) < 30:
            out[k] = v
    missing = [d["name"] for d in defs if d["required"] and not out.get(d["name"])]
    return out, missing


def missing_aspects(item):
    return [d["name"] for d in aspect_defs(item.get("category_id")) if d["required"] and not (item.get("aspects") or {}).get(d["name"])]


def _fill_missing_aspects(category_id, data, aspects, missing):
    defs = {d["name"]: d for d in aspect_defs(category_id)}
    lines = []
    for name in missing:
        vals = defs.get(name, {}).get("values") or []
        lines.append(f"- {name}" + (f" (choose from: {', '.join(vals[:60])})" if vals else ""))
    prompt = (f"Item: {data.get('title')}\n"
              f"Known specifics: {aspects}\n\nFill these required eBay item specifics. "
              "Use a listed choice when choices are given. Use \"Unknown\" only if it truly can't be inferred.\n"
              + "\n".join(lines))
    schema = {"type": "object", "properties": {"aspects": {"type": "array", "items": {
        "type": "object", "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
        "required": ["name", "value"], "additionalProperties": False}}},
        "required": ["aspects"], "additionalProperties": False}
    try:
        out = ai.ask_json([{"text": prompt}], schema, "You fill in eBay item specifics accurately and concisely.", 1500)
    except ai.AIError:
        return {}
    filled = {}
    for a in out.get("aspects") or []:
        d = defs.get(a["name"])
        if not d or not a["value"].strip() or a["value"].strip().lower() == "unknown":
            continue
        value = a["value"].strip()
        if d["mode"] == "SELECTION_ONLY" and d["values"]:
            value = _match_value(value, d["values"])
        if value:
            filled[a["name"]] = value
    return filled


# ------------------------------------------------------------------ price

def price_item(item_id, query=None, overwrite=False, fill_empty=True):
    conn = get_db()
    item = get_item(conn, item_id)
    query = query or (item["ai"] or {}).get("search_query") or item["title"]
    if not query:
        conn.close()
        raise ValueError("nothing to search for — identify the item first")
    result = comps.price_for(query, is_card=catalog.is_card(item["category_id"]))
    fields = {"comps": result}
    if result.get("suggested") and result.get("filtered") and (overwrite or (fill_empty and not item["price"])):
        fields["price"] = result["suggested"]
    update_item(conn, item_id, fields)
    conn.close()
    return result


# ------------------------------------------------------------------ readiness

def issues(item):
    out = []
    title = (item.get("title") or "").strip()
    if not title:
        out.append("no title")
    elif len(title) > 80:
        out.append(f"title {len(title)}/80")
    if not item.get("price") or item["price"] <= 0:
        out.append("no price")
    if not str(item.get("category_id") or "").isdigit():
        out.append("no category")
    if not item.get("condition_id"):
        out.append("no condition")
    if not item.get("images"):
        out.append("no photos")
    if str(item.get("category_id") or "").isdigit():
        for name in missing_aspects(item):
            out.append(f"needs {name}")
    return out


# ------------------------------------------------------------------ publish

CARD_DETAIL_FIELDS = [
    ("Year", ("Year Manufactured", "Season")),
    ("Set", ("Set",)),
    ("Player", ("Player/Athlete", "Character")),
    ("Card #", ("Card Number",)),
    ("Parallel", ("Parallel/Variety",)),
    ("Numbered", ("Print Run", "Serial Number")),
    ("Team", ("Team",)),
    ("Features", ("Features",)),
]

CONDITION_LINES = {
    "1000": "Brand new.",
    "1500": "New, open box.",
    "1750": "New with defects — see photos.",
    "2750": "Like new.",
    "3000": "Used.",
    "4000": "Used — very good condition.",
    "5000": "Used — good condition.",
    "6000": "Used — acceptable condition, shows wear.",
    "7000": "For parts or not working.",
}


def shipping_package(item):
    """Package size used for shipping — never an envelope for items over eBay's $20 envelope cap."""
    package = item.get("package") or "small"
    if package == "card" and float(item.get("price") or 0) > config.ENVELOPE_MAX_PRICE:
        return "small"
    return package


def full_description(item):
    """The listing description: item facts + condition + the seller's condition
    notes (item.description) + shipping. Pre-v2 items keep their stored text."""
    notes = (item.get("description") or "").strip()
    if not item.get("batch_id"):
        footer = config.CARD_SHIPPING_NOTE if catalog.is_card(item.get("category_id")) else config.SHIPPING_NOTE
        return f"{notes}\n\n{footer}" if notes else footer

    aspects = item.get("aspects") or {}
    parts = [item.get("title") or ""]
    if catalog.is_card(item.get("category_id")):
        details = []
        for label, keys in CARD_DETAIL_FIELDS:
            value = next((aspects[k] for k in keys if aspects.get(k)), "")
            if value and value.lower() not in ("no", "none", "n/a"):
                details.append(f"{label}: {value}")
        if details:
            parts.append("\n".join(details))
        if item.get("grade"):
            cert = f", cert #{item['cert_number']}" if item.get("cert_number") else ""
            parts.append(f"Graded {item.get('grader') or 'PSA'} {item['grade']}{cert}. "
                         "The slab pictured is the exact one you'll receive.")
        else:
            parts.append("Raw, ungraded card shown front and back. The card pictured is the exact card "
                         "you'll receive — please judge condition from the photos.")
    else:
        condition = CONDITION_LINES.get(str(item.get("condition_id") or ""), "")
        parts.append(f"Condition: {condition} The item pictured is the exact item you'll receive."
                     if condition else "The item pictured is the exact item you'll receive.")
    if notes:
        parts.append(notes)
    if not catalog.is_card(item.get("category_id")):
        parts.append(config.SHIPPING_NOTE)
    elif shipping_package(item) == "card":
        parts.append(config.CARD_SHIPPING_NOTE.replace("Card pictured is the actual card you'll receive. ", ""))
    else:
        parts.append(config.CARD_MAILER_NOTE)
    return "\n\n".join(p for p in parts if p)


def _description_html(text):
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in text.split("\n\n") if p.strip())


def publish_item(item_id):
    conn = get_db()
    item = get_item(conn, item_id)
    conn.close()
    problems = issues(item)
    if problems:
        raise ValueError("not ready: " + ", ".join(problems))
    s = settings.load()
    fulfillment = (s.get("ebay_fulfillment_policies") or {}).get(shipping_package(item))
    if not (fulfillment and s.get("ebay_payment_policy_id") and s.get("ebay_return_policy_id")):
        raise ebay.NotConfigured("Pick business policies in Settings (shipping for each package size, payment, returns)")

    urls = host_images(item_id)
    if not (s.get("seller_city") and s.get("seller_state") and s.get("seller_postal_code")):
        raise ebay.NotConfigured("Set your item location (city, state, ZIP) in Settings")
    location = ebay.ensure_location(s["seller_postal_code"], s["seller_city"], s["seller_state"])

    description = full_description(item)
    inventory = {
        "availability": {"shipToLocationAvailability": {"quantity": int(item.get("quantity") or 1)}},
        "condition": ebay.CONDITION_ENUMS.get(str(item["condition_id"]), "USED_EXCELLENT"),
        "product": {
            "title": item["title"],
            "description": description,
            "aspects": {k: [v] for k, v in (item.get("aspects") or {}).items() if v},
            "imageUrls": urls[:24],
        },
    }
    # eBay packs product.brand + product.mpn into one BrandMPN field: send both
    # or neither, or publish fails with "Input data for tag <BrandMPN> is invalid".
    brand = (item.get("aspects") or {}).get("Brand") or item.get("brand")
    if brand:
        inventory["product"]["brand"] = brand
        inventory["product"]["mpn"] = (item.get("aspects") or {}).get("MPN") or "Does not apply"
    descriptors = []
    for dname, dval in (item.get("condition_descriptors") or {}).items():
        if dname == "27503":
            descriptors.append({"name": dname, "additionalInfo": str(dval)})
        else:
            descriptors.append({"name": dname, "values": [str(dval)]})
    if descriptors:
        inventory["conditionDescriptors"] = descriptors
    ebay.put_inventory_item(item["sku"], inventory)

    offer = {
        "sku": item["sku"],
        "marketplaceId": ebay.MARKETPLACE,
        "format": "FIXED_PRICE",
        "availableQuantity": int(item.get("quantity") or 1),
        "categoryId": str(item["category_id"]),
        "listingDescription": _description_html(description),
        "listingDuration": "GTC",
        "listingPolicies": {
            "fulfillmentPolicyId": fulfillment,
            "paymentPolicyId": s["ebay_payment_policy_id"],
            "returnPolicyId": s["ebay_return_policy_id"],
            "bestOfferTerms": {"bestOfferEnabled": bool(s.get("best_offer"))},
        },
        "pricingSummary": {"price": {"value": f"{float(item['price']):.2f}", "currency": "USD"}},
        "merchantLocationKey": location,
    }
    offer_id = ebay.upsert_offer(item["sku"], offer)
    fields = {"ebay_offer_id": offer_id, "publish_error": ""}
    if item.get("status") == "listed" and item.get("ebay_listing_id"):
        listing_id = item["ebay_listing_id"]  # updating a published offer revises the live listing
    else:
        listing_id = ebay.publish_offer(offer_id)
        fields.update(ebay_listing_id=listing_id, status="listed", listed_at=now())
    conn = get_db()
    update_item(conn, item_id, fields)
    conn.close()
    return listing_id


def end_listing(item_id):
    conn = get_db()
    item = get_item(conn, item_id)
    if not item.get("ebay_offer_id"):
        conn.close()
        raise ValueError("this item wasn't published through the app — end it in Seller Hub")
    ebay.withdraw_offer(item["ebay_offer_id"])
    update_item(conn, item_id, {"status": "ended"})
    conn.close()
