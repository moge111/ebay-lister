"""Sold-comps pricing via eBay's mobile-app search API (OPTIONAL, UNOFFICIAL).

eBay's official APIs don't expose sold prices to regular developers, so this
reads the same search endpoint the eBay Android app uses. With eBay app keys in
Settings it authenticates with your own application token (client credentials,
auto-renewed). Without keys it falls back to a captured mobile session at
EBAY_MOBILE_AUTH (default ~/.ebay_mobile_auth.json: `iaf_token`, `fp4pp`,
`iaf_expires_at`). With neither, pricing is skipped and you price by hand.
Using it may be against eBay's terms — your call.
"""
import json
import os
import re
import statistics
import time

import requests

from services import ebay

AUTH_FILE = os.path.expanduser(os.environ.get("EBAY_MOBILE_AUTH", "~/.ebay_mobile_auth.json"))
SEARCH_URL = "https://apisd.ebay.com/experience/search/v1/search_results"

# Listings that bundle many things — their prices say nothing about one item.
LOT_MARKERS = ("lot of", " lot ", "lot,", "bundle", "you pick", "pick your", "pyc",
               "your choice", "choose your", "you choose", "complete set", "binder")
# Extra markers only for single cards (a card sold "in box" is a box, not a card).
CARD_ONLY_MARKERS = ("box", "build & battle", "build and battle", "living dex", "commons")

_STOPWORDS = {"the", "and", "with", "for", "of", "a", "in", "on", "new", "sealed", "edition"}


class CompsUnavailable(RuntimeError):
    pass


def token_status():
    if ebay.has_app_keys():
        return {"ok": True, "message": "Using your eBay app keys"}
    if not os.path.exists(AUTH_FILE):
        return {"ok": False, "message": f"{AUTH_FILE} not found"}
    with open(AUTH_FILE) as f:
        creds = json.load(f)
    hours_left = (float(creds.get("iaf_expires_at") or 0) - time.time()) / 3600
    if hours_left <= 0:
        return {"ok": False, "hours_left": round(hours_left, 1),
                "message": f"Comps token expired {abs(hours_left):.0f}h ago — capture a fresh one"}
    return {"ok": True, "hours_left": round(hours_left, 1), "message": f"Valid for {hours_left:.0f}h"}


def _headers(creds):
    headers = {
        "Authorization": f"Bearer {creds['iaf_token']}",
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "User-Agent": creds.get("captured_user_agent") or "eBayAndroid/6.254.0.2",
        "x-ebay-c-marketplace-id": "EBAY-US",
        "x-ebay-c-cultural-pref": "Units=<US>,Currency=USD,Timezone=America%2FDenver",
    }
    if creds.get("fp4pp"):
        headers["x-ebay-4pp"] = creds["fp4pp"]
    return headers


def _creds():
    if ebay.has_app_keys():
        try:
            return {"iaf_token": ebay.app_token()}
        except ebay.EbayError as e:
            raise CompsUnavailable(f"eBay app token failed: {e}") from e
    if not os.path.exists(AUTH_FILE):
        raise CompsUnavailable("no sold-comps source set up (add eBay app keys in Settings) — price by hand")
    with open(AUTH_FILE) as f:
        return json.load(f)


def search_sold(query, is_card=False, limit=40):
    """Recently sold listings for a query -> [{title, price}], lots filtered."""
    creds = _creds()
    params = {
        "_nkw": query, "_sop": "13", "LH_Sold": "1", "LH_Complete": "1",
        "_pgn": "1", "_ipg": "60", "answersVersion": "1",
        "requestedPageLayoutsForMultiLayoutRegion": "LIST_1_COLUMN|LARGE_1_COLUMN|GRID_2_COLUMN",
    }
    r = requests.get(SEARCH_URL, headers=_headers(creds), params=params, timeout=20)
    if r.status_code in (401, 403):
        raise CompsUnavailable("Comps token rejected (expired) — capture a fresh one")
    if r.status_code != 200:
        raise CompsUnavailable(f"eBay search HTTP {r.status_code}")

    markers = LOT_MARKERS + (CARD_ONLY_MARKERS if is_card else ())
    modules = r.json().get("modules") or {}
    keys = sorted((k for k in modules if k.startswith("listing")),
                  key=lambda k: int(k[7:]) if k[7:].isdigit() else 999)
    out = []
    for k in keys:
        m = modules[k]
        if not isinstance(m, dict) or m.get("_type") != "ITEM":
            continue
        spans = (m.get("title") or {}).get("textSpans") or []
        title = spans[0].get("text", "") if spans and isinstance(spans[0], dict) else ""
        if not title or any(mk in title.lower() for mk in markers):
            continue
        value = ((m.get("displayPrice") or {}).get("value") or {})
        try:
            price = float(value.get("value")) if isinstance(value, dict) else None
        except (TypeError, ValueError):
            price = None
        listing_id = m.get("listingId") or ((m.get("action") or {}).get("params") or {}).get("listingId")
        if price:
            out.append({"title": title, "price": price,
                        "url": f"https://www.ebay.com/itm/{listing_id}" if listing_id else ""})
        if len(out) >= limit:
            break
    return out


def _tokens(text):
    words = (t.strip("#.-") for t in re.findall(r"[a-z0-9/#.-]+", (text or "").lower()))
    return {t for t in words if t not in _STOPWORDS and len(t) > 1}


def _is_year(token):
    return bool(re.fullmatch(r"(19|20)\d\d(-\d\d)?", token))


def _relevant(query, comps):
    """Keep comps sharing most of the query's words — and every number in it
    (card numbers, grades, serials, set codes are what separate a $2 base card
    from a $200 parallel)."""
    q = _tokens(query)
    # Years are optional (sellers often omit them); card numbers, serials and grades are not.
    numbers = {t for t in q if any(c.isdigit() for c in t) and not _is_year(t)}
    keep = []
    for c in comps:
        t = _tokens(c["title"])
        if numbers - t:
            continue
        if q and len(q & t) / len(q) >= 0.6:
            keep.append(c)
    return keep


def _trim_outliers(prices):
    if len(prices) < 5:
        return prices
    q1, _, q3 = statistics.quantiles(prices, n=4)
    iqr = q3 - q1
    return [p for p in prices if q1 - 1.5 * iqr <= p <= q3 + 1.5 * iqr] or prices


def listing_price(median):
    """Round a sold median to an eBay-friendly price."""
    if median >= 10:
        return round(median) - 0.01
    if median >= 1:
        return round(median * 2) / 2 - 0.01
    return 0.99


def price_for(query, is_card=False):
    """Sold comps summary for a query. Raises CompsUnavailable on token/HTTP trouble."""
    raw = search_sold(query, is_card=is_card)
    relevant = _relevant(query, raw)
    used = relevant if len(relevant) >= 3 else raw
    prices = _trim_outliers([c["price"] for c in used])
    if not prices:
        return {"query": query, "n": 0, "items": raw[:15], "at": int(time.time())}
    median = statistics.median(prices)
    return {
        "query": query,
        "n": len(prices),
        "median": round(median, 2),
        "low": min(prices),
        "high": max(prices),
        # Loose matches are other cards/products — show them, but don't suggest a price.
        "suggested": listing_price(median) if len(relevant) >= 3 else None,
        "filtered": len(relevant) >= 3,
        "items": used[:15],
        "at": int(time.time()),
    }
