"""Subscriptions and send log. SQLite locally; any SQLAlchemy URL (e.g. Postgres) in production."""
from __future__ import annotations

import json

from sqlalchemy import (Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text, UniqueConstraint,
                        create_engine, func, insert, select, update)

metadata = MetaData()

subscriptions = Table(
    "subscriptions", metadata,
    Column("id", Integer, primary_key=True),
    Column("endpoint", Text, nullable=False, unique=True),
    Column("p256dh", Text, nullable=False),
    Column("auth", Text, nullable=False),
    Column("region", String(1), nullable=False),
    Column("times", Text, nullable=False),  # JSON list of "HH:MM"
    Column("shift_kwh", Float, nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

sent_log = Table(
    "sent_log", metadata,
    Column("id", Integer, primary_key=True),
    Column("subscription_id", Integer, nullable=False),
    Column("send_date", String(10), nullable=False),
    Column("time_label", String(5), nullable=False),
    Column("sent_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("subscription_id", "send_date", "time_label", name="one_send_per_slot"),
)


def normalise_db_url(url: str) -> str:
    """Hosts hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the psycopg driver named."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class Store:
    def __init__(self, url: str):
        self.engine = create_engine(normalise_db_url(url), pool_pre_ping=True)
        metadata.create_all(self.engine)

    def upsert(self, endpoint, p256dh, auth, region, times, shift_kwh) -> int:
        values = {"p256dh": p256dh, "auth": auth, "region": region, "times": json.dumps(times),
                  "shift_kwh": shift_kwh, "active": True}
        with self.engine.begin() as c:
            row = c.execute(select(subscriptions.c.id).where(subscriptions.c.endpoint == endpoint)).first()
            if row:
                c.execute(update(subscriptions).where(subscriptions.c.id == row.id).values(**values))
                return row.id
            return c.execute(insert(subscriptions).values(endpoint=endpoint, **values)).inserted_primary_key[0]

    def deactivate(self, endpoint: str) -> bool:
        with self.engine.begin() as c:
            return c.execute(update(subscriptions).where(subscriptions.c.endpoint == endpoint)
                             .values(active=False)).rowcount > 0

    def get(self, endpoint: str):
        with self.engine.connect() as c:
            return c.execute(select(subscriptions).where(subscriptions.c.endpoint == endpoint)).first()

    def active(self) -> list:
        with self.engine.connect() as c:
            return list(c.execute(select(subscriptions).where(subscriptions.c.active.is_(True))))

    def already_sent(self, sub_id: int, date: str, label: str) -> bool:
        with self.engine.connect() as c:
            return c.execute(select(sent_log.c.id).where(
                sent_log.c.subscription_id == sub_id, sent_log.c.send_date == date,
                sent_log.c.time_label == label)).first() is not None

    def mark_sent(self, sub_id: int, date: str, label: str) -> None:
        with self.engine.begin() as c:
            c.execute(insert(sent_log).values(subscription_id=sub_id, send_date=date, time_label=label))

    def count_active(self) -> int:
        return len(self.active())
