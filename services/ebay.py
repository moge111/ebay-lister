"""eBay REST APIs: OAuth, Taxonomy/Metadata (what a category requires), Account
(business policies) and Inventory (create + publish + end listings).

Needs a free developer keyset (App ID + Cert ID) and a RuName; the seller
connects once via OAuth consent and the refresh token (~18 months) is kept in
settings.json.
"""
import base64
import json
import threading
import time
import urllib.parse

import requests

import settings
from db import get_db

API = "https://api.ebay.com"
AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
TOKEN_URL = f"{API}/identity/v1/oauth2/token"
MARKETPLACE = "EBAY_US"
CATEGORY_TREE = "0"  # EBAY_US
CACHE_TTL = 7 * 24 * 3600

MARKETING_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.marketing"
USER_SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    MARKETING_SCOPE,
]

# Numeric condition id -> Inventory API enum.
CONDITION_ENUMS = {
    "1000": "NEW", "1500": "NEW_OTHER", "1750": "NEW_WITH_DEFECTS",
    "2000": "CERTIFIED_REFURBISHED", "2010": "EXCELLENT_REFURBISHED",
    "2020": "VERY_GOOD_REFURBISHED", "2030": "GOOD_REFURBISHED",
    "2500": "SELLER_REFURBISHED", "2750": "LIKE_NEW", "2990": "PRE_OWNED_EXCELLENT",
    "3000": "USED_EXCELLENT", "3010": "PRE_OWNED_FAIR", "4000": "USED_VERY_GOOD",
    "5000": "USED_GOOD", "6000": "USED_ACCEPTABLE", "7000": "FOR_PARTS_OR_NOT_WORKING",
}


class EbayError(RuntimeError):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors or []


class NotConfigured(EbayError):
    pass


# ------------------------------------------------------------------ OAuth

def has_app_keys():
    return bool(settings.get("ebay_app_id") and settings.get("ebay_cert_id"))


def is_connected():
    return has_app_keys() and bool(settings.get("ebay_refresh_token"))


def _basic_auth():
    raw = f"{settings.get('ebay_app_id')}:{settings.get('ebay_cert_id')}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


def _token_request(data):
    r = requests.post(TOKEN_URL, headers={"Authorization": _basic_auth(),
                                          "Content-Type": "application/x-www-form-urlencoded"},
                      data=data, timeout=20)
    body = r.json() if r.content else {}
    if r.status_code != 200:
        raise EbayError(f"eBay OAuth {r.status_code}: {body.get('error_description') or body}")
    return body


_app_token = {"token": "", "exp": 0}


def app_token():
    """Client-credentials token — enough for Taxonomy and Metadata."""
    if not has_app_keys():
        raise NotConfigured("eBay App ID / Cert ID not set (Settings)")
    if _app_token["exp"] - 60 > time.time():
        return _app_token["token"]
    body = _token_request({"grant_type": "client_credentials", "scope": USER_SCOPES[0]})
    _app_token.update(token=body["access_token"], exp=time.time() + body["expires_in"])
    return _app_token["token"]


def consent_url(state="lister"):
    if not (has_app_keys() and settings.get("ebay_ru_name")):
        raise NotConfigured("Set App ID, Cert ID and RuName first")
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": settings.get("ebay_app_id"),
        "redirect_uri": settings.get("ebay_ru_name"),
        "response_type": "code",
        "scope": " ".join(USER_SCOPES),
        "state": state,
    })


def exchange_code(code_or_url):
    """Accepts the bare code or the whole redirected URL (paste-back flow)."""
    code = code_or_url.strip()
    if "code=" in code:
        code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query).get("code", [""])[0]
    code = urllib.parse.unquote(code)
    body = _token_request({"grant_type": "authorization_code", "code": code,
                           "redirect_uri": settings.get("ebay_ru_name")})
    settings.update(
        ebay_refresh_token=body["refresh_token"],
        ebay_refresh_expires_at=int(time.time() + body.get("refresh_token_expires_in", 0)),
        ebay_user_token=body["access_token"],
        ebay_user_token_expires_at=int(time.time() + body["expires_in"]),
        ebay_scopes=USER_SCOPES,
    )


def granted_scopes():
    # Connections made before Promoted Listings support didn't grant sell.marketing.
    return settings.get("ebay_scopes") or [sc for sc in USER_SCOPES if sc != MARKETING_SCOPE]


def can_promote():
    return is_connected() and MARKETING_SCOPE in granted_scopes()


def user_token():
    if not is_connected():
        raise NotConfigured("eBay account not connected (Settings → Connect eBay)")
    if settings.get("ebay_user_token_expires_at") - 60 > time.time():
        return settings.get("ebay_user_token")
    body = _token_request({"grant_type": "refresh_token", "refresh_token": settings.get("ebay_refresh_token"),
                           "scope": " ".join(granted_scopes())})
    settings.update(ebay_user_token=body["access_token"],
                    ebay_user_token_expires_at=int(time.time() + body["expires_in"]))
    return body["access_token"]


def _call(method, path, token, body=None, params=None, ok=(200, 201, 204)):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
               "Content-Language": "en-US", "Accept-Language": "en-US",
               "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE}
    if body is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(3):  # eBay's Inventory service throws occasional 500s
        r = requests.request(method, API + path, headers=headers, params=params,
                             data=json.dumps(body) if body is not None else None, timeout=30)
        if r.status_code < 500:
            break
        time.sleep(2 * (attempt + 1))
    data = r.json() if r.content else {}
    if r.status_code not in ok:
        errors = data.get("errors") or []
        msg = "; ".join(e.get("longMessage") or e.get("message", "") for e in errors) or r.text[:300]
        raise EbayError(f"{method} {path.split('?')[0]} → {r.status_code}: {msg}", errors)
    return data


# ------------------------------------------------------------------ cache

def _cached(key, fetch):
    conn = get_db()
    row = conn.execute("SELECT data, fetched_at FROM ebay_cache WHERE key = ?", (key,)).fetchone()
    if row and row["fetched_at"] + CACHE_TTL > time.time():
        conn.close()
        return json.loads(row["data"])
    data = fetch()
    conn.execute("INSERT OR REPLACE INTO ebay_cache (key, data, fetched_at) VALUES (?, ?, ?)",
                 (key, json.dumps(data), int(time.time())))
    conn.commit()
    conn.close()
    return data


# ------------------------------------------------------------------ taxonomy

def suggest_categories(query):
    def fetch():
        data = _call("GET", f"/commerce/taxonomy/v1/category_tree/{CATEGORY_TREE}/get_category_suggestions",
                     app_token(), params={"q": query[:350]})
        out = []
        for s in data.get("categorySuggestions") or []:
            cat = s.get("category") or {}
            path = [a.get("categoryName") for a in reversed(s.get("categoryTreeNodeAncestors") or [])]
            out.append({"id": cat.get("categoryId"), "name": cat.get("categoryName"),
                        "path": " › ".join([*path, cat.get("categoryName") or ""])})
        return out
    return _cached(f"suggest:{query.lower().strip()}", fetch)


def category_aspects(category_id):
    """-> [{name, required, recommended, mode, multi, values[]}] required first."""
    def fetch():
        data = _call("GET", f"/commerce/taxonomy/v1/category_tree/{CATEGORY_TREE}/get_item_aspects_for_category",
                     app_token(), params={"category_id": category_id})
        out = []
        for a in data.get("aspects") or []:
            c = a.get("aspectConstraint") or {}
            out.append({
                "name": a.get("localizedAspectName"),
                "required": bool(c.get("aspectRequired")),
                "recommended": c.get("aspectUsage") == "RECOMMENDED",
                "mode": c.get("aspectMode", "FREE_TEXT"),
                "multi": c.get("itemToAspectCardinality") == "MULTI",
                "values": [v.get("localizedValue") for v in (a.get("aspectValues") or [])][:400],
            })
        out.sort(key=lambda a: (not a["required"], not a["recommended"]))
        return out
    return _cached(f"aspects:{category_id}", fetch)


def category_conditions(category_id):
    """-> {required, conditions: [{id, name, descriptors: [{id, name, values:[{id,name}], free_text}]}]}"""
    def fetch():
        data = _call("GET", f"/sell/metadata/v1/marketplace/{MARKETPLACE}/get_item_condition_policies",
                     app_token(), params={"filter": f"categoryIds:{{{category_id}}}"})
        policies = data.get("itemConditionPolicies") or [{}]
        p = policies[0]
        conditions = []
        for c in p.get("itemConditions") or []:
            descriptors = []
            for d in c.get("conditionDescriptors") or []:
                descriptors.append({
                    "id": str(d.get("conditionDescriptorId")),
                    "name": d.get("conditionDescriptorName"),
                    "values": [{"id": str(v.get("conditionDescriptorValueId")), "name": v.get("conditionDescriptorValueName")}
                               for v in d.get("conditionDescriptorValues") or []],
                    "free_text": bool(d.get("conditionDescriptorConstraint", {}).get("usage") == "OPTIONAL"
                                      and not d.get("conditionDescriptorValues")),
                })
            conditions.append({"id": str(c.get("conditionId")), "name": c.get("conditionDescription"),
                               "descriptors": descriptors})
        return {"required": bool(p.get("itemConditionRequired")), "conditions": conditions}
    return _cached(f"conditions:{category_id}", fetch)


# ------------------------------------------------------------------ account

def policies():
    tok = user_token()
    q = {"marketplace_id": MARKETPLACE}
    return {
        "fulfillment": [{"id": p["fulfillmentPolicyId"], "name": p["name"]}
                        for p in _call("GET", "/sell/account/v1/fulfillment_policy", tok, params=q).get("fulfillmentPolicies", [])],
        "payment": [{"id": p["paymentPolicyId"], "name": p["name"]}
                    for p in _call("GET", "/sell/account/v1/payment_policy", tok, params=q).get("paymentPolicies", [])],
        "return": [{"id": p["returnPolicyId"], "name": p["name"]}
                   for p in _call("GET", "/sell/account/v1/return_policy", tok, params=q).get("returnPolicies", [])],
    }


_location_lock = threading.Lock()


def ensure_location(postal_code, city, state):
    """Inventory API needs a merchant location; create one once. Locked so
    parallel publishes don't all race to create it."""
    with _location_lock:
        key = settings.get("ebay_location_key")
        if key:
            return key
        key = "home"
        tok = user_token()
        existing = _call("GET", "/sell/inventory/v1/location", tok).get("locations") or []
        if not any(l.get("merchantLocationKey") == key for l in existing):
            try:
                _call("POST", f"/sell/inventory/v1/location/{key}", tok, body={
                    "location": {"address": {"postalCode": postal_code, "city": city,
                                             "stateOrProvince": state, "country": "US"}},
                    "locationTypes": ["WAREHOUSE"],
                    "name": "Home",
                    "merchantLocationStatus": "ENABLED",
                })
            except EbayError as e:
                if "already exists" not in str(e):
                    raise
        settings.update(ebay_location_key=key)
        return key


# ------------------------------------------------------------------ inventory

def put_inventory_item(sku, payload):
    _call("PUT", f"/sell/inventory/v1/inventory_item/{urllib.parse.quote(sku, safe='')}", user_token(), body=payload)


def upsert_offer(sku, payload):
    """Create the offer for a SKU, or update the existing one. Returns offerId."""
    tok = user_token()
    existing = _call("GET", "/sell/inventory/v1/offer", tok, params={"sku": sku, "marketplace_id": MARKETPLACE},
                     ok=(200, 404)).get("offers") or []
    if existing:
        offer_id = existing[0]["offerId"]
        _call("PUT", f"/sell/inventory/v1/offer/{offer_id}", tok, body=payload)
        return offer_id
    return _call("POST", "/sell/inventory/v1/offer", tok, body=payload)["offerId"]


def publish_offer(offer_id):
    return _call("POST", f"/sell/inventory/v1/offer/{offer_id}/publish", user_token(), body={})["listingId"]


def withdraw_offer(offer_id):
    _call("POST", f"/sell/inventory/v1/offer/{offer_id}/withdraw", user_token(), body={})


# ------------------------------------------------------------------ promoted listings

_campaign_lock = threading.Lock()


def ensure_campaign(rate):
    """A running Promoted Listings Standard (cost-per-sale) campaign at `rate`%."""
    if not can_promote():
        raise NotConfigured("Reconnect eBay in Settings to allow Promoted Listings")
    name = f"Lister Standard {float(rate):g}%"
    tok = user_token()
    with _campaign_lock:
        found = _call("GET", "/sell/marketing/v1/ad_campaign/get_campaign_by_name", tok,
                      params={"campaign_name": name}, ok=(200, 404))
        if found.get("campaignId") and found.get("campaignStatus") in ("RUNNING", "SCHEDULED"):
            return found["campaignId"]
        _call("POST", "/sell/marketing/v1/ad_campaign", tok, body={
            "campaignName": name,
            "marketplaceId": MARKETPLACE,
            "startDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 60)),
            "fundingStrategy": {"fundingModel": "COST_PER_SALE", "bidPercentage": f"{float(rate):.1f}"},
        })
        return _call("GET", "/sell/marketing/v1/ad_campaign/get_campaign_by_name", tok,
                     params={"campaign_name": name})["campaignId"]


def promote(listing_ids, rate):
    """Add listings to the campaign. Returns {listing_id: "ok" | error text}."""
    campaign = ensure_campaign(rate)
    out = {}
    for i in range(0, len(listing_ids), 500):
        chunk = [str(x) for x in listing_ids[i:i + 500]]
        resp = _call("POST", f"/sell/marketing/v1/ad_campaign/{campaign}/bulk_create_ads_by_listing_id",
                     user_token(), body={"requests": [{"listingId": x, "bidPercentage": f"{float(rate):.1f}"} for x in chunk]},
                     ok=(200, 201, 207))
        for r in resp.get("responses", []):
            errs = r.get("errors") or []
            out[str(r.get("listingId"))] = "ok" if r.get("statusCode") in (200, 201) and not errs else \
                "; ".join(e.get("longMessage") or e.get("message", "") for e in errs) or f"HTTP {r.get('statusCode')}"
    return out
