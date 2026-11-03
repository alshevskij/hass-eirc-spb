"""Shared helpers for IKUS meter entities."""
from __future__ import annotations

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
