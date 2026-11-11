"""Web Push sending (VAPID). Keys come from environment variables; see scripts/generate_vapid_keys.py."""
from __future__ import annotations

import json


class Gone(Exception):
    """The browser subscription no longer exists (user revoked permission or reinstalled)."""


def make_sender(private_key: str, subject: str):
    from pywebpush import WebPushException, webpush

    def send(sub, payload: dict) -> None:
        info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
        try:
            webpush(info, data=json.dumps(payload), vapid_private_key=private_key,
                    vapid_claims={"sub": subject}, ttl=4 * 3600, timeout=10)
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                raise Gone() from exc
            raise
    return send
