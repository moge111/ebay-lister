# Moving to an always-on Mac (e.g. a Mac mini)

The code comes from git; everything else (DB, photos, settings/secrets, CSVs, Claude memory) travels in one bundle.

## On the old Mac
1. Stop the app (so nothing changes after the snapshot).
2. `git push` any local commits.
3. `scripts/export_bundle.sh` → `~/Desktop/ebay-lister-bundle-<date>.tar` (~2.7 GB). It contains your API keys and eBay tokens: copy it by USB drive or AirDrop, not a cloud folder.

## On the new Mac
1. **Python 3.10+** (`brew install python`, or python.org). The system `/usr/bin/python3` is too old.
2. **Claude Code**: install it, run `claude`, then `/login`. The AI backend uses this login (or put an Anthropic API key in Settings instead).
3. Clone to the same path as before so Claude Code finds its project memory, e.g.
   `git clone https://github.com/<you>/ebay-lister.git "$HOME/ebay lister"`
4. `cd "$HOME/ebay lister" && scripts/install_macmini.sh ~/Downloads/ebay-lister-bundle-<date>.tar`
   Add `--lan` to reach it from your laptop/phone at `http://<mini-name>.local:8090`. There's no login screen, so only use `--lan` on your home network.
5. Open http://localhost:8090 → **Settings** → **Test AI**. Check that eBay shows as connected (the refresh token carries over; if it doesn't, reconnect).
6. Delete the bundle tar from both Macs.

## Keep it up
System Settings on the mini:
- **Energy**: turn on "Prevent automatic sleeping when the display is off" and "Start up automatically after a power failure".
- **Users & Groups → Automatically log in as** your user. The service is a login agent (it needs your login session for the `claude` keychain login), so it starts once you're logged in. Auto-login isn't available while FileVault is on.

## Day to day
| | |
|---|---|
| Restart | `launchctl kickstart -k gui/$(id -u)/com.ebaylister.app` |
| Logs | `tail -f logs/app.log` |
| Update code | `git pull && scripts/install_macmini.sh` (add `--lan` again if you used it) |
| Stop for good | `launchctl bootout gui/$(id -u)/com.ebaylister.app && rm ~/Library/LaunchAgents/com.ebaylister.app.plist` |

Run only one copy at a time. Two machines with separate DBs would drift and could publish the same item twice.
