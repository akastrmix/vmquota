from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _month_anchor(year: int, month: int, anchor_day: int) -> date:
    last_day = monthrange(year, month)[1]
    return date(year, month, min(anchor_day, last_day))


def _next_year_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def local_midnight_to_utc(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)


def initial_cycle(now_utc: datetime, zone: ZoneInfo) -> tuple[int, datetime, datetime]:
    local_day = now_utc.astimezone(zone).date()
    anchor_day = local_day.day
    period_start = local_midnight_to_utc(local_day, zone)
    next_reset = next_anchor_after(period_start, anchor_day, zone)
    return anchor_day, period_start, next_reset


def next_anchor_after(period_start_utc: datetime, anchor_day: int, zone: ZoneInfo) -> datetime:
    local_start = period_start_utc.astimezone(zone).date()
    year, month = _next_year_month(local_start.year, local_start.month)
    return local_midnight_to_utc(_month_anchor(year, month, anchor_day), zone)


def manual_reanchor_cycle(now_utc: datetime, anchor_day: int, zone: ZoneInfo) -> tuple[datetime, datetime]:
    local_day = now_utc.astimezone(zone).date()
    period_start = local_midnight_to_utc(local_day, zone)
    target_year = local_day.year
    target_month = local_day.month
    candidate = _month_anchor(target_year, target_month, anchor_day)
    if candidate <= local_day:
        target_year, target_month = _next_year_month(target_year, target_month)
        candidate = _month_anchor(target_year, target_month, anchor_day)
    return period_start, local_midnight_to_utc(candidate, zone)
