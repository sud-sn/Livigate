"""Business date in the pharmacy's time zone. Tests pin it with ``set_today``."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from levigate.config import get_settings

_fixed: date | None = None


def today() -> date:
    if _fixed is not None:
        return _fixed
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def set_today(value: date | None) -> None:
    global _fixed
    _fixed = value
