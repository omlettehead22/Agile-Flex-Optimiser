import base64
import os

import pandas as pd
import pytest

os.environ["OFFPEAK_NO_AUTOAPP"] = "1"
from fastapi.testclient import TestClient  # noqa: E402

from webapp.advice import build_advice, notification  # noqa: E402
from webapp.main import create_app  # noqa: E402
from webapp.push import Gone, make_sender  # noqa: E402
from webapp.solar import YIELD_KWH_PER_KWP  # noqa: E402

from fakes import synthetic_market  # noqa: E402

MARKET = synthetic_market("2025-11-01", "2026-02-01", seed=5)["price_p_kwh"]


def fake_get(url, params=None):
    if url.endswith("/v1/products/"):
        return {"results": [{"code": "AGILE-24-10-01", "direction": "IMPORT", "available_from": "2024-10-01"}], "next": None}
    if url.endswith("/products/AGILE-24-10-01/"):
        return {"single_register_electricity_tariffs": {}}  # falls back to the E-1R-...-<region> pattern
    if "standard-unit-rates" in url:
        lo, hi = pd.Timestamp(params["period_from"]), pd.Timestamp(params["period_to"])
        sel = MARKET[(MARKET.index >= lo) & (MARKET.index < hi)]
        return {"results": [{"valid_from": t.isoformat(), "value_inc_vat": v} for t, v in sel.items()], "next": None}
    if "grid-supply-points" in url:
        return {"results": [{"group_id": "_C"}]} if params["postcode"] == "SW1A1AA" else {"results": []}
    raise AssertionError(url)


class Clock:
    def __init__(self, ts):
        self.now = pd.Timestamp(ts, tz="UTC")

    def __call__(self):
        return self.now


class Outbox:
    def __init__(self):
        self.sent, self.gone = [], set()

    def __call__(self, sub, payload):
        if sub.endpoint in self.gone:
            raise Gone()
        self.sent.append((sub.endpoint, payload))


@pytest.fixture
def env(tmp_path):
    clock, outbox = Clock("2025-12-03 08:10"), Outbox()
    app = create_app(f"sqlite:///{tmp_path / 't.db'}", get=fake_get, clock=clock, sender=outbox,
                     vapid_public_key="PUBKEY", cron_token="secret")
    return TestClient(app), clock, outbox


def sub_payload(endpoint="https://push.example/abc123", times=("08:00", "18:00"), region="C"):
    return {"subscription": {"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}},
            "region": region, "times": list(times), "shift_kwh": 5}


def test_pages_and_static_files_are_served(env):
    client, _, _ = env
    assert "Off-peak" in client.get("/").text
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"] and "showNotification" in sw.text
    assert client.get("/manifest.webmanifest").json()["start_url"] == "/"
    for f in ("app.js", "style.css", "icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        assert client.get(f"/static/{f}").status_code == 200
    cfg = client.get("/api/config").json()
    assert cfg["push_enabled"] and cfg["regions"]["C"] == "London"


def test_advice_endpoint(env):
    client, _, _ = env
    a = client.get("/api/advice", params={"region": "C", "shift_kwh": 10}).json()
    assert a["region_name"] == "London" and 40 <= len(a["slots"]) <= 48
    assert a["cheapest"][0]["avg_p"] <= a["average_p"]
    assert a["saving_vs_average_gbp"] >= 0 and a["tip"]
    assert client.get("/api/advice", params={"region": "Z"}).status_code == 422


def test_postcode_lookup(env):
    client, _, _ = env
    assert client.get("/api/region", params={"postcode": "sw1a 1aa"}).json() == {"region": "C", "name": "London"}
    assert client.get("/api/region", params={"postcode": "not a postcode"}).status_code == 422
    assert client.get("/api/region", params={"postcode": "ZZ1 1ZZ"}).status_code == 422


@pytest.mark.parametrize("bad", [
    {"times": ["08:15"]}, {"times": ["8:00"]}, {"times": ["01:00", "02:00", "03:00", "04:00"]},
    {"times": []}, {"region": "Z"}, {"shift_kwh": 0}])
def test_subscribe_validation(env, bad):
    client, _, _ = env
    assert client.post("/api/subscribe", json=sub_payload() | bad).status_code == 422


def test_send_due_respects_times_and_never_repeats(env):
    client, clock, outbox = env
    assert client.post("/api/subscribe", json=sub_payload()).json()["times"] == ["08:00", "18:00"]
    client.post("/api/subscribe", json=sub_payload())  # same browser again: updates, no duplicate
    hdr = {"X-Cron-Token": "secret"}
    assert client.post("/api/tasks/send-due").status_code == 403
    assert client.post("/api/tasks/send-due", headers=hdr).json()["sent"] == 1     # 08:10
    assert client.post("/api/tasks/send-due", headers=hdr).json()["sent"] == 0     # already sent
    clock.now = pd.Timestamp("2025-12-03 11:00", tz="UTC")                          # 08:00 too stale, 18:00 not due
    assert client.post("/api/tasks/send-due", headers=hdr).json()["sent"] == 0
    clock.now = pd.Timestamp("2025-12-03 18:05", tz="UTC")
    assert client.post("/api/tasks/send-due", headers=hdr).json()["sent"] == 1
    title = outbox.sent[-1][1]["title"]
    assert title.startswith("Cheapest: ") and len(outbox.sent) == 2


def test_summer_time_uses_uk_clock(env):
    client, clock, outbox = env
    client.post("/api/subscribe", json=sub_payload(times=["08:00"]))
    clock.now = pd.Timestamp("2025-06-03 06:30", tz="UTC")  # 07:30 BST: not yet due
    assert client.post("/api/tasks/send-due", headers={"X-Cron-Token": "secret"}).json()["sent"] == 0


def test_expired_browser_subscription_is_deactivated(env):
    client, _, outbox = env
    client.post("/api/subscribe", json=sub_payload(endpoint="https://push.example/old"))
    outbox.gone.add("https://push.example/old")
    r = client.post("/api/tasks/send-due", headers={"X-Cron-Token": "secret"}).json()
    assert r["expired"] == 1 and client.get("/api/health").json()["subscribers"] == 0


def test_test_push_and_unsubscribe(env):
    client, _, outbox = env
    ep = "https://push.example/me"
    assert client.post("/api/test-push", json={"endpoint": ep}).status_code == 404
    client.post("/api/subscribe", json=sub_payload(endpoint=ep))
    assert client.post("/api/test-push", json={"endpoint": ep}).json()["ok"]
    assert outbox.sent[-1][0] == ep
    assert client.post("/api/unsubscribe", json={"endpoint": ep}).json()["ok"]
    assert client.post("/api/test-push", json={"endpoint": ep}).status_code == 404


def test_notification_text_is_short_and_specific():
    idx = pd.date_range("2025-12-03 08:00", periods=48, freq="30min", tz="UTC")
    prices = pd.Series([20.0] * 48, index=idx)
    prices.iloc[34:38] = -2.0   # 01:00-03:00 negative
    prices.iloc[16:22] = 35.0   # 16:00-19:00 peak
    advice = build_advice(prices, pd.Timestamp("2025-12-03 08:10", tz="UTC"), shift_kwh=10)
    msg = notification(advice)
    assert msg["title"] == "Cheapest: tomorrow 01:00–03:00 (-2.0p)"  # 01:00 is after midnight
    assert "Negative prices tomorrow 01:00–03:00" in msg["body"] and "Avoid 18:00–19:00 (35p)" in msg["body"]
    assert advice["saving_vs_peak_gbp"] == pytest.approx(10 * (35 + 2) / 100, abs=0.01)
    assert len(msg["body"]) < 260


def test_solar_calculator(env):
    client, _, _ = env
    base = client.post("/api/solar", json={}).json()
    assert base["generation_kwh"] == pytest.approx(4 * YIELD_KWH_PER_KWP["C"], rel=0.01)
    assert base["annual_saving_gbp"] > 0 and base["payback_years"] > 0 and len(base["monthly"]) == 12
    no_batt = client.post("/api/solar", json={"battery_kwh": 0}).json()
    assert base["self_use_pct"] > no_batt["self_use_pct"]  # a battery keeps more solar at home
    assert no_batt["upfront_cost_gbp"] == 6500
    none = client.post("/api/solar", json={"system_kwp": 0, "battery_kwh": 0}).json()
    assert none["annual_saving_gbp"] == 0 and none["payback_years"] is None
    sunny = client.post("/api/solar", json={"region": "L"}).json()
    assert sunny["generation_kwh"] > base["generation_kwh"]
    assert client.post("/api/solar", json={"system_kwp": -1}).status_code == 422


def test_real_web_push_encryption(monkeypatch):
    """Sign and encrypt with real VAPID keys; only the network call is faked."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from py_vapid import Vapid
    import requests

    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    v = Vapid(); v.generate_keys()
    private = b64(v.private_key.private_numbers().private_value.to_bytes(32, "big"))
    browser = ec.generate_private_key(ec.SECP256R1())
    p256dh = b64(browser.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))

    class Sub:
        endpoint, auth = "https://fcm.googleapis.com/fcm/send/xyz", b64(os.urandom(16))
    Sub.p256dh = p256dh
    calls = []

    class Resp:
        def __init__(self, code): self.status_code, self.text, self.headers, self.reason = code, "", {}, "Gone"

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        calls.append(headers)
        return Resp(201 if len(calls) == 1 else 410)
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests.Session, "post", lambda self, *a, **k: fake_post(*a, **k))

    send = make_sender(private, "mailto:test@example.com")
    send(Sub, {"title": "t", "body": "b"})
    assert calls[0]["authorization"].startswith("vapid t=") and calls[0]["content-encoding"] == "aes128gcm"
    with pytest.raises(Gone):
        send(Sub, {"title": "t", "body": "b"})


def test_database_url_normalisation():
    from sqlalchemy import create_engine
    from webapp.db import normalise_db_url
    for raw in ("postgres://u:p@host:5432/db", "postgresql://u:p@host:5432/db"):
        url = normalise_db_url(raw)
        assert url == "postgresql+psycopg://u:p@host:5432/db"
        assert create_engine(url).dialect.driver == "psycopg"  # driver installed and importable
    assert normalise_db_url("sqlite:///x.db") == "sqlite:///x.db"


def test_guides_load_and_link_correctly():
    import re
    from webapp.articles import load_all
    articles = load_all()
    assert list(articles)[0] == "how-agile-works" and len(articles) >= 6
    for a in articles.values():
        assert a.title and a.summary and a.body_html.count("<h2>") >= 2
        for href in re.findall(r'href="([^"]+)"', a.body_html):
            if href.startswith("/guides/"):
                assert href.split("/")[-1] in articles, f"{a.slug} links to missing guide {href}"
            else:
                assert href in ("/", "/#solar") or href.startswith("https://"), f"{a.slug}: odd link {href}"


def test_guide_pages(env):
    client, _, _ = env
    index = client.get("/guides")
    assert index.status_code == 200 and "Heat pumps: the grant" in index.text
    page = client.get("/guides/negative-prices")
    assert page.status_code == 200
    assert "<h1>Negative prices: getting paid to use electricity</h1>" in page.text
    assert "Last checked 28 October 2026" in page.text and 'href="/guides/home-batteries"' in page.text
    missing = client.get("/guides/no-such-guide")
    assert missing.status_code == 404 and "Guide not found" in missing.text
    listing = client.get("/api/articles").json()
    assert listing[0]["url"] == "/guides/how-agile-works" and all(i["reading_minutes"] >= 1 for i in listing)
    home = client.get("/").text
    assert 'href="/guides"' in home and 'href="/guides/is-solar-worth-it"' in home


def test_guide_header_validation(tmp_path):
    from webapp.articles import parse
    bad = tmp_path / "Bad Name.md"
    bad.write_text("---\ntitle: x\nsummary: y\nupdated: 2026-01-01\n---\nbody")
    with pytest.raises(ValueError, match="lowercase"):
        parse(bad)
    nohead = tmp_path / "ok-name.md"
    nohead.write_text("no header")
    with pytest.raises(ValueError, match="header"):
        parse(nohead)


def test_octopus_outage_gives_clear_503_then_serves_cached_prices(tmp_path, monkeypatch):
    import requests
    import webapp.prices as prices_mod

    state = {"down": True}

    def flaky_get(url, params=None):
        if state["down"]:
            raise requests.ConnectionError("Octopus unreachable")
        return fake_get(url, params)

    app = create_app(f"sqlite:///{tmp_path / 'o.db'}", get=flaky_get, clock=Clock("2025-12-03 08:10"),
                     sender=Outbox(), vapid_public_key="PUBKEY", cron_token="secret")
    client = TestClient(app)
    r = client.get("/api/advice?region=C")
    assert r.status_code == 503 and "try again" in r.json()["detail"]  # friendly, not a bare 500

    state["down"] = False
    assert client.get("/api/advice?region=C").status_code == 200    # fills the cache
    state["down"] = True
    real_time = prices_mod.time.time
    monkeypatch.setattr(prices_mod.time, "time", lambda: real_time() + 3600)  # cache now stale
    r = client.get("/api/advice?region=C")
    assert r.status_code == 200 and r.json()["slots"]                # stale prices beat an error


def test_ranges_starting_tomorrow_are_labelled():
    now = pd.Timestamp("2025-12-03 17:20", tz="UTC")  # late afternoon: some best/avoid times fall tomorrow
    a = build_advice(MARKET, now)
    blocks = a["cheapest"] + a["avoid"]
    assert {b["day"] for b in blocks} <= {"today", "tomorrow"}
    for b in blocks:  # a range is "tomorrow" exactly when its start is earlier in the day than now
        assert (b["day"] == "tomorrow") == (b["start"] < "17:30" and b["start"] != "17:00")
    msg = notification(a)
    for b in blocks:
        if b["day"] == "tomorrow":
            assert f"tomorrow {b['start']}–{b['end']}" in msg["title"] + msg["body"]


def test_head_requests_succeed(env):
    client, _, _ = env
    assert client.head("/").status_code == 200
    assert client.head("/api/health").status_code == 200


def test_pages_and_assets_are_revalidated_after_deploys(env):
    client, _, _ = env
    for path in ("/", "/static/app.js", "/static/style.css", "/guides"):
        assert client.get(path).headers.get("cache-control") == "no-cache", path
    assert "no-cache" not in client.get("/api/health").headers.get("cache-control", "")
