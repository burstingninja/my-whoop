"""
sleep_debt.py — Sleep need, rolling sleep debt, and sleep bank.

Ported from Goose's sleep v1 concepts (SleepInput.sleep_need_minutes,
rolling_sleep_debt_minutes, SleepBaseline) and adapted for the my-whoop
daily pipeline.

Concepts
--------
sleep_need_min
    How much sleep you needed last night. Starts at the user's target
    (default DEFAULT_TARGET_MIN = 480, i.e. 8 h) and adds a strain surcharge
    when the prior day's load was high — heavy exercise demands more recovery.

sleep_debt_min
    Rolling 14-day balance of (sleep_need − actual_sleep) per night, summed.
    Positive  → you are in deficit (sleeping less than you need).
    Negative  → you have banked surplus sleep.
    Clamped to [DEBT_FLOOR_MIN, DEBT_CEILING_MIN] so extreme values stay
    interpretable.

sleep_bank_min
    The surplus component: max(0, −sleep_debt_min). Zero when in deficit;
    positive when you've accumulated more sleep than needed over the window.
    Stored separately so the UI can show a "bank" concept without sign-flipping.

Rolling window
    ROLLING_DAYS = 14 nights (matches Goose's current_14_day baseline window
    and WHOOP's published debt-accumulation window).

Strain surcharge
    Mirrored from Goose's ``prior_day_strain`` input to sleep v1:
      strain ≥ STRAIN_HIGH  → +SURCHARGE_HIGH_MIN extra need
      strain ≥ STRAIN_MED   → +SURCHARGE_MED_MIN  extra need
    This is a coarse approximation — WHOOP's exact surcharge is proprietary.
"""
from __future__ import annotations

from typing import Any, Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default nightly sleep target when the user has not set one (8 hours).
DEFAULT_TARGET_MIN: float = 480.0

#: Rolling window length (nights) for debt accumulation.
ROLLING_DAYS: int = 14

#: Prior-strain threshold for a moderate surcharge (≈ moderate training day).
STRAIN_MED: float = 10.0
#: Prior-strain threshold for a high surcharge (≈ hard training day).
STRAIN_HIGH: float = 16.0

#: Extra sleep need added when prior strain ≥ STRAIN_MED (minutes).
SURCHARGE_MED_MIN: float = 15.0
#: Extra sleep need added when prior strain ≥ STRAIN_HIGH (minutes).
SURCHARGE_HIGH_MIN: float = 30.0

#: Maximum debt the rolling sum can report (caps extreme multi-week deficits).
DEBT_CEILING_MIN: float = 300.0   # 5 hours
#: Minimum debt (most surplus you can "bank" — sleeping 5 extra hours is the floor).
DEBT_FLOOR_MIN: float = -300.0


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def nightly_sleep_need(
    target_min: float | None,
    prior_strain: float | None,
) -> float:
    """Return tonight's sleep need in minutes.

    Parameters
    ----------
    target_min :
        User's target sleep duration (minutes).  ``None`` → DEFAULT_TARGET_MIN.
    prior_strain :
        Yesterday's strain score (0–21).  ``None`` → no surcharge applied.
    """
    base = float(target_min) if target_min is not None else DEFAULT_TARGET_MIN

    surcharge = 0.0
    if prior_strain is not None:
        s = float(prior_strain)
        if s >= STRAIN_HIGH:
            surcharge = SURCHARGE_HIGH_MIN
        elif s >= STRAIN_MED:
            surcharge = SURCHARGE_MED_MIN

    return base + surcharge


def rolling_sleep_debt(
    prior_rows: Sequence[dict[str, Any]],
    target_min: float | None,
) -> dict[str, float]:
    """Compute rolling sleep debt + bank over the trailing ROLLING_DAYS nights.

    Parameters
    ----------
    prior_rows :
        Rows from ``daily_metrics``, ordered oldest → newest, each with at
        least ``total_sleep_min`` (float | None) and optionally ``strain``
        (float | None, used as the prior-strain surcharge for THAT night's need,
        i.e. it is the strain from the day BEFORE that night — in practice the
        caller should pass the rows already shifted by one day, or this function
        uses each row's own strain as an approximation).
    target_min :
        User's target sleep duration (minutes).  ``None`` → DEFAULT_TARGET_MIN.

    Returns
    -------
    dict with keys:
        ``debt_min``  — rolling balance (positive = deficit, negative = surplus).
                        Clamped to [DEBT_FLOOR_MIN, DEBT_CEILING_MIN].
        ``bank_min``  — max(0, −debt_min): surplus only, zero when in deficit.
        ``nights``    — number of nights included in the rolling window.
    """
    rows = list(prior_rows)[-ROLLING_DAYS:]   # keep at most last 14 nights

    total_debt = 0.0
    nights = 0

    for row in rows:
        actual = row.get("total_sleep_min")
        if actual is None:
            continue                           # skip nights with no sleep data
        prior_strain = row.get("strain")
        need = nightly_sleep_need(target_min, prior_strain)
        total_debt += need - float(actual)
        nights += 1

    clamped = max(DEBT_FLOOR_MIN, min(DEBT_CEILING_MIN, total_debt))
    return {
        "debt_min": round(clamped, 1),
        "bank_min": round(max(0.0, -clamped), 1),
        "nights":   nights,
    }
