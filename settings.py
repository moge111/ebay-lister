"""settings.json — API keys, OAuth tokens and choices from the Settings page."""
import json
import os
import threading

import config

_lock = threading.Lock()

SECRET_KEYS = {"anthropic_api_key", "ebay_cert_id", "ebay_refresh_token", "ebay_user_token", "imgbb_api_key"}


def load():
    data = dict(config.DEFAULT_SETTINGS)
    if os.path.exists(config.SETTINGS_FILE):
        with open(config.SETTINGS_FILE) as f:
            data.update(json.load(f))
    return data


def get(key):
    return load().get(key, config.DEFAULT_SETTINGS.get(key))


def update(**changes):
    with _lock:
        data = load()
        data.update(changes)
        tmp = config.SETTINGS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, config.SETTINGS_FILE)
        return data


def public():
    """Settings safe to send to the browser: secrets become a has-value flag."""
    out = {}
    for k, v in load().items():
        out[k] = bool(v) if k in SECRET_KEYS else v
    return out
