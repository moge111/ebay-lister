# eBay Lister — agent guide

Flask JSON API + single-page UI (Preact + htm, vendored in `static/vendor/`, no build step) that turns product photos into eBay listings. SQLite DB, runs locally on **http://localhost:8090**.

## Setting it up for a user

1. Python 3.10+. `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. `python3 app.py` (port 8090 is set in `config.PORT`; change it if taken). The DB (`products.db`) and folders are created on first run.
3. Walk the user through **Settings** in the UI. Everything lands in `settings.json` (gitignored, holds secrets; never commit or print it):
   - **AI backend**: Anthropic API key (fast, parallel, billed) or the local `claude` CLI logged in via `/login` (subscription). "Test AI" verifies it. A stale CLI login hangs ~3 min, then fails with a clear message.
   - **imgbb API key**: required to publish or export (photo hosting).
   - **Seller city / state / ZIP**: required to publish.
   - **eBay**: Production App ID + Cert ID + RuName, then Connect (OAuth; the accepted URL can be any https page — the user pastes back the `?code=` URL). Settings rejects Sandbox (`SBX`) keys. Then pick business policies: payment, return, and one shipping policy per package size.
   - Without eBay keys the app works in **CSV mode** (File Exchange). Fill "CSV export settings" with the user's policy *names*.
4. Optional sold comps: see README (`services/comps.py`, unofficial mobile API, token file). Without it pricing is skipped.

## Flow

1. **Photos**: upload or "Import folder" → `photos/batch_NNN/` + `thumbs/`. HEIC is converted, and the EXIF shoot time goes into `product_images.taken_at`. Loose photos have `product_id IS NULL`.
2. **Group**: `services/grouping.py`. AI mode sends numbered contact sheets of 30 captioned with seconds between shots; the last group carries into the next sheet. Also time-gap / every-N / manual.
3. **Identify + price**: `services/listing.identify_item()`. Claude reads up to 5 photos → title (trimmed to 80 on a word boundary), category (a key from `services/catalog.py` or an eBay Taxonomy suggestion), condition, graded info, item specifics mapped onto the category's real aspects (missing required ones get a second fill pass), package size, flaws, search query. Then `price_item()` → `services/comps.py` (relevance filter + IQR trim; no suggestion for loose matches).
4. **Review**: item drawer (`static/item.js`). `listing.issues()` decides "ready".
5. **Publish**: `services/ebay.py` (inventory item → offer → publish; editing a listed item revises in place; End = withdraw) or CSV (`services/csv_export.py`, `C:<aspect>` columns generated per item).

## Key files
- `app.py`: all routes (`/api/...`)
- `db.py`: schema + in-place migrations. JSON columns: `aspects`, `condition_descriptors`, `ai`, `comps`. `update_item()` also enforces the envelope price cap.
- `jobs.py`: background jobs the UI polls. `Job.map` parallelizes per item.
- `settings.py` / `config.py`: user settings (secrets) / static constants + defaults
- `services/ai.py`: Claude backends. API uses `claude-opus-5` with structured outputs; CLI is `claude -p` with images streamed in via stream-json and tools/MCP/settings disabled.
- `services/catalog.py`: known-good category ids, New/Used-only categories, fallback required specifics, grading descriptor ids
- `services/listing.py`: identify prompt, aspect/condition resolution, readiness, description builder, publish payload

## Behavior worth knowing (learned the hard way)
- **Descriptions are templated, not AI prose.** `listing.full_description()` builds: title → card details list (from aspects) or a condition line → "exact item pictured" → the item's `description` field (seller's condition notes/flaws only) → shipping note (`config.*_NOTE`). AI-written "what the card looks like" copy read badly.
- **Card year = copyright year on the item** (e.g. "© 2026 The Topps Company"), never stats rows or game dates. The prompt includes today's date.
- **eBay BrandMPN**: send `product.brand` and `product.mpn` together ("Does not apply") or neither. Otherwise publish fails.
- **Envelope cap**: eBay Standard Envelope only allows items ≤ $20. Items with `package='card'` over `config.ENVELOPE_MAX_PRICE` are auto-bumped to `small`.
- Some categories accept only New/Used (see `catalog.NEW_USED_ONLY`). With eBay keys, allowed conditions come live from the Metadata API.
- Hand-edited titles set `title_locked`; re-identify keeps them.
- `/photos/<path>` only serves `photos/`, `incoming/`, `new ebay photos/`. Never serve the project root (DB, settings).

## Customizing
- Shipping/description wording: `config.py` (`SHIPPING_NOTE`, `CARD_SHIPPING_NOTE`, `CARD_MAILER_NOTE`) and `listing.CONDITION_LINES`.
- Categories the AI picks from first: `services/catalog.CATEGORIES`.
- Title/identify rules: `IDENTIFY_SYSTEM` in `services/listing.py`.
- AI model / parallelism: Settings page.
