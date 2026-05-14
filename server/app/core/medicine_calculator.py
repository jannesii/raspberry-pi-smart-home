from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import TYPE_CHECKING

from .models import MedicineRefillCalculation

if TYPE_CHECKING:
    from collections.abc import Sequence

VALID_WEEKDAYS = frozenset(range(7))


def normalize_medicine_name(name: str) -> str:
    """Return the grouping key used for medicine purchase history."""
    normalized = re.sub(r"\s+", " ", str(name or "").strip())
    if not normalized:
        raise ValueError("Medicine name is required")
    return normalized.casefold()


def normalize_dosing_weekdays(weekdays: Sequence[int]) -> list[int]:
    """Validate and normalize weekday integers where Monday is 0."""
    normalized: set[int] = set()
    for value in weekdays:
        if isinstance(value, bool):
            raise ValueError("Invalid dosing weekday")
        weekday = int(value)
        if weekday not in VALID_WEEKDAYS:
            raise ValueError("Dosing weekdays must be between 0 and 6")
        normalized.add(weekday)
    if not normalized:
        raise ValueError("At least one dosing weekday is required")
    return sorted(normalized)


def calculate_medicine_refill(
    *,
    purchase_date: str,
    pieces_bought: int,
    dose_per_dosing_day: int,
    dosing_weekdays: Sequence[int],
) -> MedicineRefillCalculation:
    """Calculate run-out and next eligible purchase dates for one purchase snapshot."""
    purchase_day = date.fromisoformat(str(purchase_date))
    pieces = int(pieces_bought)
    dose = int(dose_per_dosing_day)
    weekdays = normalize_dosing_weekdays(dosing_weekdays)

    if pieces < 1:
        raise ValueError("pieces_bought must be positive")
    if dose < 1:
        raise ValueError("dose_per_dosing_day must be positive")

    treatment_days = pieces / dose
    dosing_days_covered = max(1, math.ceil(treatment_days))
    if treatment_days >= 90:
        flex_days = 21
    elif treatment_days >= 60:
        flex_days = 14
    else:
        flex_days = 7

    weekday_set = set(weekdays)
    remaining = pieces
    current_day = purchase_day + timedelta(days=1)
    run_out_day = current_day
    while True:
        if current_day.weekday() in weekday_set:
            remaining -= dose
            if remaining <= 0:
                run_out_day = current_day
                break
        current_day += timedelta(days=1)

    next_purchase_day = run_out_day - timedelta(days=flex_days)
    if next_purchase_day < purchase_day:
        next_purchase_day = purchase_day

    return MedicineRefillCalculation(
        purchase_date=purchase_day.isoformat(),
        run_out_date=run_out_day.isoformat(),
        next_purchase_date=next_purchase_day.isoformat(),
        flex_days=flex_days,
        treatment_days=treatment_days,
        dosing_days_covered=dosing_days_covered,
        dosing_weekdays=weekdays,
    )
