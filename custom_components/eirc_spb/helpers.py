"""Shared helpers for IKUS meter entities."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def meter_utility(meter: dict[str, Any]) -> str:
    """Return a normalized utility type for a meter."""
    utility = (
        (meter.get("subservice") or {}).get("utility") or meter.get("utility") or ""
    ).upper()
    if not utility:
        utility = {
            54179: "ELECTRICITY",
            4599: "WATER",
            4600: "WATER",
        }.get(meter.get("subserviceId"), "")
    return utility


def meter_label(meter: dict[str, Any], indication: dict[str, Any]) -> str:
    """Build a concise, stable Russian label for a meter."""
    subservice_id = meter.get("subserviceId")
    utility = meter_utility(meter)
    if subservice_id == 4600:
        return "ХВС"
    if subservice_id == 4599:
        return "ГВС"
    if utility == "ELECTRICITY" or subservice_id == 54179:
        scale_name = indication.get("scaleName")
        return f"Энергия ({scale_name})" if scale_name else "Энергия"
    return (
        meter.get("name")
        or (meter.get("subservice") or {}).get("name")
        or {"WATER": "Водоснабжение", "GAS": "Газ"}.get(utility, "Счётчик")
    )


def format_bill_period(value: Any) -> str:
    """Format a bill period as a date without a time component."""
    text = str(value).strip()
    if not text:
        return text

    for date_format in (
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, date_format).strftime("%d.%m.%Y")
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime(
            "%d.%m.%Y"
        )
    except ValueError:
        return text
