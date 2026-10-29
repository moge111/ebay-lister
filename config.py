import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PHOTOS_DIR = os.path.join(BASE_DIR, "photos")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
PORT = 8090  # 8080 is often held by mitmdump
# 127.0.0.1 = this machine only. Set LISTER_HOST=0.0.0.0 to reach it from other
# devices on the network (no login screen — only do that on a trusted network).
HOST = os.environ.get("LISTER_HOST", "127.0.0.1")

# Folders the /photos route may serve from (never BASE_DIR itself — it holds
# products.db, settings.json and Google credentials).
SERVABLE_PREFIXES = ("photos/", "incoming/", "new ebay photos/")

# Where the "Import from folder" box starts (AirDropped photos land here).
DEFAULT_IMPORT_DIR = os.path.expanduser("~/Downloads")

# Package sizes the AI picks from. Each maps to a shipping policy chosen on the
# Settings page (Sell API) or a File Exchange shipping profile name (CSV).
PACKAGE_SIZES = ["card", "small", "medium", "large"]
# eBay Standard Envelope is only allowed for items worth $20 or less; pricier
# "card" items are bumped to the tracked small-box policy automatically.
ENVELOPE_MAX_PRICE = 20.0

SHIPPING_NOTE = (
    "Ships securely packaged with tracking from a smoke-free home. "
    "Buying multiple? Just buy, message me, and I'll refund the extra shipping. "
    "Thanks for looking!"
)
CARD_MAILER_NOTE = (
    "Ships in a penny sleeve and top loader inside a bubble mailer with tracking. "
    "Buying multiple? Just buy, message me, and I'll refund the extra shipping. "
    "Thanks for looking!"
)
CARD_SHIPPING_NOTE = (
    "Card pictured is the actual card you'll receive. "
    "Ships in a penny sleeve and top loader with tracking. "
    "Buying multiple? Just buy, message me, and I'll refund the extra shipping. "
    "Thanks for looking!"
)

# Defaults for settings.json (secrets + choices made in the Settings page).
DEFAULT_SETTINGS = {
    "ai_backend": "auto",            # auto | api | cli
    "ai_model": "claude-opus-5",     # API model id
    "ai_cli_model": "opus",          # alias passed to `claude --model`
    "ai_workers": 4,
    "anthropic_api_key": "",
    "imgbb_api_key": "",
    # Seller location (required to publish) — shown to buyers as item location.
    "seller_city": "",
    "seller_state": "",
    "seller_postal_code": "",
    # CSV / File Exchange fallback only: your eBay business-policy NAMES.
    "csv_payment_profile": "",
    "csv_return_profile": "",
    "csv_shipping_profiles": {"card": "", "small": "", "medium": "", "large": ""},
    "promoted_ad_rate": "",  # Promoted Listings Standard % for every new listing, blank = off
    "ebay_app_id": "",
    "ebay_cert_id": "",
    "ebay_ru_name": "",
    "ebay_refresh_token": "",
    "ebay_refresh_expires_at": "",
    "ebay_user_token": "",
    "ebay_user_token_expires_at": 0,
    "ebay_location_key": "",
    "ebay_payment_policy_id": "",
    "ebay_return_policy_id": "",
    "ebay_fulfillment_policies": {},  # package size -> fulfillment policy id
    "best_offer": True,
}
