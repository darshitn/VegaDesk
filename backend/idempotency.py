"""Stable launch target identities for safe replay of action receipts."""

import hashlib
import json


def launch_target_key(target):
    """Hash a normalized requested target; never store private target text in the key."""
    normalized = " ".join(str(target or "").strip().casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def request_target_key(action, params):
    """Bind a receipt to the requested payload, excluding injected test launchers."""
    params = params or {}
    if action in ("open_website", "system.open_url"):
        return launch_target_key(params.get("site_or_url") or params.get("url"))
    if action == "open_app":
        return launch_target_key(params.get("name"))
    safe_params = {key: value for key, value in params.items()
                   if key not in ("browser_launcher", "app_launcher")}
    payload = json.dumps(safe_params, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def receipt_conflict(existing, action, target_key=None):
    """Old launch receipts without a target key cannot safely authorize replay."""
    if existing.action != action:
        return True
    if target_key is not None and existing.target_key != target_key:
        return True
    return False
