"""The desk's clock and user settings.

Negotiation runs on days and weeks. For demos the clock is simulated: 'Advance' moves it forward and
the carrier simulator, follow-up scheduler and bank feed react. In production the clock is the real date
and a scheduled job runs `lifecycle.tick()` once a day.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import db
from .config import settings

DEFAULTS = {
    "settle_pct": 85,          # agent may accept offers at or above this share of the demand
    "auto_send_limit": 25000,  # demands at or under this amount are sent without approval (0 = always ask)
}


def sim_day() -> int:
    return int(db.get_setting("sim_day", 0))


def today() -> date:
    return settings.today() + timedelta(days=sim_day())


def day_no() -> int:
    """Integer day used for scheduling."""
    return sim_day()


def get(key: str):
    return db.get_setting(key, DEFAULTS[key])


def all_settings() -> dict:
    return {k: get(k) for k in DEFAULTS} | {"desk_date": today().isoformat(), "sim_day": sim_day()}
