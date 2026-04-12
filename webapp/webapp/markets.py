from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import WORLD_MARKETS


def _format_clock(moment: datetime, tz_label: str) -> str:
    hour_fmt = "%#I:%M %p" if __import__("os").name == "nt" else "%-I:%M %p"
    return f"{moment.strftime(hour_fmt)} {tz_label}"


def _forex_open(now_et: datetime) -> bool:
    weekday = now_et.weekday()
    minute = now_et.hour * 60 + now_et.minute
    if weekday in {0, 1, 2, 3}:
        return True
    if weekday == 6:
        return minute >= 17 * 60
    if weekday == 4:
        return minute < 17 * 60
    return False


def _forex_session(now_utc: datetime) -> str:
    hour = now_utc.hour
    active: list[str] = []
    if hour >= 21 or hour < 6:
        active.append("Sydney")
    if 0 <= hour < 9:
        active.append("Tokyo")
    if 7 <= hour < 16:
        active.append("London")
    if 13 <= hour < 22:
        active.append("New York")
    return " / ".join(active) if active else "Interbank"


def _crypto_session(now_utc: datetime) -> str:
    hour = now_utc.hour
    if 0 <= hour < 8:
        return "Asia Flow"
    if 8 <= hour < 16:
        return "Europe Flow"
    return "US Flow"


def market_status(market_id: str) -> dict[str, object]:
    market = WORLD_MARKETS[market_id]
    tz = ZoneInfo(market["tz"])
    now_local = datetime.now(tz)
    now_utc = datetime.now(timezone.utc)
    local_time = _format_clock(now_local, market["tz_label"])
    market_type = market.get("type", "equity")

    if market_type == "forex":
        et_now = datetime.now(ZoneInfo("America/New_York"))
        is_open = _forex_open(et_now)
        return {
            "status": "open" if is_open else "closed",
            "local_time": local_time,
            "session": _forex_session(now_utc) if is_open else "Weekend Gap",
            "schedule_label": "24h / 5d",
            "is_24h": True,
        }

    if market_type == "crypto":
        return {
            "status": "open",
            "local_time": local_time,
            "session": _crypto_session(now_utc),
            "schedule_label": "24h / 7d",
            "is_24h": True,
        }

    t = now_local.hour * 60 + now_local.minute
    open_t = market["open"][0] * 60 + market["open"][1]
    close_t = market["close"][0] * 60 + market["close"][1]
    if now_local.weekday() >= 5:
        return {
            "status": "closed",
            "local_time": local_time,
            "session": "Weekend",
            "schedule_label": "Cash Session",
            "is_24h": False,
        }

    lunch = market.get("lunch")
    if lunch:
        lunch_start = lunch[0][0] * 60 + lunch[0][1]
        lunch_end = lunch[1][0] * 60 + lunch[1][1]
        if lunch_start <= t < lunch_end:
            return {
                "status": "lunch",
                "local_time": local_time,
                "session": "Lunch Break",
                "schedule_label": "Cash Session",
                "is_24h": False,
            }

    status = "open" if open_t <= t < close_t else "closed"
    session = "Cash Session" if status == "open" else "After Hours"
    return {
        "status": status,
        "local_time": local_time,
        "session": session,
        "schedule_label": "Cash Session",
        "is_24h": False,
    }


def all_market_snapshots() -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    for market_id, market in WORLD_MARKETS.items():
        status = market_status(market_id)
        snapshots.append(
            {
                "id": market_id,
                "name": market["name"],
                "flag": market["flag"],
                "index": market["index"],
                "currency": market["currency"],
                "locale": market["locale"],
                "tz": market["tz"],
                "tz_label": market["tz_label"],
                "type": market["type"],
                "instrument_count": len(market["instruments"]),
                "instruments": [instrument["s"] for instrument in market["instruments"]],
                **status,
            }
        )
    return snapshots