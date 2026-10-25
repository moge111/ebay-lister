# eBay Lister

Turn a pile of product photos into live eBay listings.

Drop in photos from your phone (HEIC is fine, no sorting needed). Claude groups them into items, identifies each one (title, category, condition, item specifics), prices it from sold comps, and you publish straight to eBay after a quick review.

Built for resellers of trading cards, toys, collectibles, video games and general stuff.

## What it does

1. **Photos**: drag photos in or import a folder (e.g. your AirDrop'd `~/Downloads`).
2. **Group**: AI sorts the photos into items (using shot order and timing). Or group by time gap, every N photos, or by hand.
3. **Identify**: for each item, Claude reads the photos and writes an 80-character keyword title, picks the eBay category, condition and package size, and fills the required item specifics from eBay's own category rules. It notes only visible flaws and never claims "sealed/complete/tested" unless it can see it.
4. **Price** (optional): median of recent sold listings, filtered to the exact card or item.
5. **Review**: a grid and a side editor with photo reordering, a comps panel with links to the sold listings, category search, and "ready to list" checks.
6. **Publish**: one click through the official eBay Sell API, or export a File Exchange CSV instead.

## Quick start

```bash
git clone <this repo> && cd ebay-lister
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Open **http://localhost:8090** and go to **Settings**:

| Setting | Needed for | How |
|---|---|---|
| **AI**: Anthropic API key *or* the `claude` CLI | grouping + identifying | API key from console.anthropic.com, or install Claude Code and run `claude` → `/login` (uses your Claude subscription) |
| **Photo hosting**: imgbb key | publishing/exporting | free at https://api.imgbb.com/ |
| **Seller**: city, state, ZIP | publishing | shown to buyers as item location |
| **eBay**: App ID, Cert ID, RuName → Connect | one-click publishing | free keys at https://developer.ebay.com (use the **Production** keyset). Without this the app exports File Exchange CSVs instead |
| **Business policies** | publishing | appear after connecting eBay: pick your payment, return, and a shipping policy per package size |

### Connecting eBay (the fiddly part)
1. developer.ebay.com → *Application Keys* → create a **Production** keyset.
2. Next to it, click **User Tokens** → *Get a Token from eBay via Your Application* → **Add eBay Redirect URL**. Any privacy-policy URL works. Set the *auth accepted URL* to any https page (e.g. `https://www.google.com`).
3. Copy the **RuName** it shows into Settings with the App ID and Cert ID, then save and **Connect eBay account**.
4. After you approve, eBay sends you to that https page with `?code=…` in the address bar. Paste that whole URL into Settings within ~5 minutes.

## Handing this to an AI agent

Point Claude Code (or any coding agent) at this repo and say *"set this up for me"*. **CLAUDE.md** has the setup checklist, architecture and the places you'd customize: categories, description/shipping wording, package sizes, prompts.

## Sold-comps pricing (optional, unofficial)

eBay's official APIs don't give regular developers sold prices. `services/comps.py` reads the eBay Android app's search endpoint. Once your eBay App ID + Cert ID are in Settings it authenticates with your own application token, so there's nothing else to set up. Without app keys it falls back to a captured app session token at `~/.ebay_mobile_auth.json` (override with `EBAY_MOBILE_AUTH`):

```json
{"iaf_token": "...", "fp4pp": "...", "iaf_expires_at": 1790000000}
```

Using this endpoint may be against eBay's terms. Without either, everything else works and you set prices yourself.

## Data & privacy

Everything stays on your machine: `products.db` (SQLite), `photos/`, `output/` (CSVs), and `settings.json` (your keys and eBay tokens). All of these are gitignored.

## License

MIT
