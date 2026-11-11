"""FastAPI app: serves the web page, the price advice API, push subscriptions and the solar calculator.

Environment variables:
  DATABASE_URL        default sqlite:///offpeak.db (use Postgres in production)
  VAPID_PUBLIC_KEY    from scripts/generate_vapid_keys.py
  VAPID_PRIVATE_KEY   from scripts/generate_vapid_keys.py
  VAPID_SUBJECT       mailto:you@example.com
  CRON_TOKEN          shared secret for /api/tasks/send-due
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from agileflex.fetch import http_get

from .advice import TZ, build_advice, notification
from .articles import article_page, index_page, load_all, not_found_page
from .db import Store
from .prices import REGIONS, PriceService
from .push import Gone, make_sender
from .solar import SolarInput, compute

STATIC = Path(__file__).parent / "static"
SEND_WINDOW = pd.Timedelta(hours=2)  # a notification more than 2h late is skipped, not sent stale


class Keys(BaseModel):
    p256dh: str
    auth: str


class PushSubscription(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)
    keys: Keys


class SubscribeRequest(BaseModel):
    subscription: PushSubscription
    region: str
    times: list[str] = Field(min_length=1, max_length=3)
    shift_kwh: float = Field(5.0, ge=0.5, le=100)

    @field_validator("region")
    @classmethod
    def known_region(cls, v):
        if v not in REGIONS:
            raise ValueError("unknown region")
        return v

    @field_validator("times")
    @classmethod
    def half_hours(cls, v):
        clean = sorted(set(v))
        for t in clean:
            hh, _, mm = t.partition(":")
            if not (hh.isdigit() and mm in ("00", "30") and 0 <= int(hh) <= 23 and len(hh) == 2):
                raise ValueError("times must be HH:00 or HH:30")
        return clean


class EndpointRequest(BaseModel):
    endpoint: str


class SolarRequest(BaseModel):
    region: str = "C"
    system_kwp: float = Field(4.0, ge=0, le=25)
    system_cost_gbp: float = Field(6500, ge=0, le=100000)
    battery_kwh: float = Field(5.0, ge=0, le=40)
    battery_cost_gbp: float = Field(3000, ge=0, le=100000)
    annual_usage_kwh: float = Field(3100, ge=300, le=50000)
    import_p: float = Field(25.0, ge=1, le=150)
    export_p: float = Field(15.0, ge=0, le=100)
    annual_price_change_pct: float = Field(0.0, ge=-10, le=15)
    yield_override: float | None = Field(None, ge=300, le=1500)


def create_app(database_url: str | None = None, get=http_get, clock=None, sender=None,
               vapid_public_key: str | None = None, cron_token: str | None = None) -> FastAPI:
    clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
    store = Store(database_url or os.environ.get("DATABASE_URL", "sqlite:///offpeak.db"))
    prices = PriceService(get, clock)
    public_key = vapid_public_key if vapid_public_key is not None else os.environ.get("VAPID_PUBLIC_KEY", "")
    token = cron_token if cron_token is not None else os.environ.get("CRON_TOKEN", "")
    if sender is None and os.environ.get("VAPID_PRIVATE_KEY"):
        sender = make_sender(os.environ["VAPID_PRIVATE_KEY"], os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com"))

    app = FastAPI(title="Off-peak", docs_url="/api/docs")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def advice_for(region: str, shift_kwh: float) -> dict:
        try:
            return build_advice(prices.upcoming(region), clock(), shift_kwh)
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():  # served from the root so it can control the whole site
        return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/api/config")
    def config():
        return {"vapid_public_key": public_key, "push_enabled": bool(public_key and sender),
                "regions": REGIONS}

    @app.get("/api/region")
    def region(postcode: str):
        try:
            code = prices.region_for_postcode(postcode)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"region": code, "name": REGIONS[code]}

    @app.get("/api/advice")
    def advice(region: str = "C", shift_kwh: float = 5.0):
        if region not in REGIONS:
            raise HTTPException(422, "Unknown region.")
        if not 0.5 <= shift_kwh <= 100:
            raise HTTPException(422, "shift_kwh must be between 0.5 and 100.")
        return advice_for(region, shift_kwh) | {"region": region, "region_name": REGIONS[region]}

    @app.post("/api/subscribe")
    def subscribe(req: SubscribeRequest):
        sub_id = store.upsert(req.subscription.endpoint, req.subscription.keys.p256dh, req.subscription.keys.auth,
                              req.region, req.times, req.shift_kwh)
        return {"ok": True, "id": sub_id, "times": req.times}

    @app.post("/api/unsubscribe")
    def unsubscribe(req: EndpointRequest):
        return {"ok": store.deactivate(req.endpoint)}

    @app.post("/api/test-push")
    def test_push(req: EndpointRequest):
        sub = store.get(req.endpoint)
        if sub is None or not sub.active:
            raise HTTPException(404, "No active subscription for this browser. Turn on notifications first.")
        if sender is None:
            raise HTTPException(503, "Notifications are not configured on this server.")
        msg = notification(advice_for(sub.region, sub.shift_kwh))
        try:
            sender(sub, msg)
        except Gone:
            store.deactivate(sub.endpoint)
            raise HTTPException(410, "This browser's subscription has expired. Turn notifications on again.")
        return {"ok": True, "sent": msg}

    @app.post("/api/solar")
    def solar(req: SolarRequest):
        if req.region not in REGIONS:
            raise HTTPException(422, "Unknown region.")
        return compute(SolarInput(**req.model_dump()))

    @app.post("/api/tasks/send-due")
    def send_due(x_cron_token: str = Header("")):
        if not token or not secrets.compare_digest(x_cron_token, token):
            raise HTTPException(403, "Bad or missing X-Cron-Token.")
        if sender is None:
            raise HTTPException(503, "Notifications are not configured on this server.")
        now = clock()
        local = now.tz_convert(TZ)
        today = local.strftime("%Y-%m-%d")
        sent = skipped = failed = expired = 0
        cache: dict[tuple, dict] = {}
        for sub in store.active():
            for label in json.loads(sub.times):
                due = pd.Timestamp(f"{today} {label}").tz_localize(TZ, nonexistent="shift_forward", ambiguous=True)
                if not (due <= local < due + SEND_WINDOW) or store.already_sent(sub.id, today, label):
                    continue
                key = (sub.region, sub.shift_kwh)
                try:
                    if key not in cache:
                        cache[key] = notification(advice_for(sub.region, sub.shift_kwh))
                    sender(sub, cache[key])
                    store.mark_sent(sub.id, today, label)
                    sent += 1
                except Gone:
                    store.deactivate(sub.endpoint)
                    expired += 1
                except HTTPException:
                    skipped += 1
                except Exception:
                    failed += 1
        return {"sent": sent, "skipped": skipped, "failed": failed, "expired": expired, "at": local.isoformat()}

    @app.get("/guides", response_class=HTMLResponse, include_in_schema=False)
    def guides():
        return index_page()

    @app.get("/guides/{slug}", response_class=HTMLResponse, include_in_schema=False)
    def guide(slug: str):
        page = article_page(slug)
        return HTMLResponse(page, status_code=200) if page else HTMLResponse(not_found_page(), status_code=404)

    @app.get("/api/articles")
    def articles():
        return [{"slug": a.slug, "title": a.title, "summary": a.summary, "updated": a.updated.isoformat(),
                 "reading_minutes": a.reading_minutes, "url": f"/guides/{a.slug}"} for a in load_all().values()]

    @app.get("/api/health")
    def health():
        return {"ok": True, "subscribers": store.count_active()}

    return app


app = create_app() if os.environ.get("OFFPEAK_NO_AUTOAPP") != "1" else None
