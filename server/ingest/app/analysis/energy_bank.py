"""
energy_bank.py — Rolling energy balance (0–100) from recovery, strain, and stress.

Concept (ported from Goose's energy_rollup):
  Each day has a charge component (driven by how well you recovered) and a drain
  component (driven by how hard you trained and how stressed you were).  The
  rolling balance carries forward day-to-day, capped at [0, 100].

  energy_score = 50 is "neutral" — moderate recovery, moderate load.
  energy_score > 50 means you have more reserves than average.
  energy_score < 50 means you are running below your baseline.

Formula
-------
  charge  = (recovery_score / 100) × MAX_CHARGE
            MAX_CHARGE = 40 → full recovery fills 40 units of the 100-unit tank.

  drain   = (strain / 21) × MAX_STRAIN_DRAIN
            + (stress_score / 100) × MAX_STRESS_DRAIN   [optional; drops if None]
            MAX_STRAIN_DRAIN = 25, MAX_STRESS_DRAIN = 15.

  net_delta = charge − drain   (typically −40 … +40 per day)

Rolling balance (ROLLING_DAYS = 7):
  energy_score = clamp(0, NEUTRAL + Σ(last 7 net_deltas), 100)

  NEUTRAL = 50.  Starting from neutral and summing 7 days of deltas means a
  week of high recovery + no strain lands at ~90, while a week of zero recovery
  + max strain lands at ~10.

If fewer than MIN_DAYS rows with enough data exist, returns None.
"""
from __future__ import annotations

from typing import Any, Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROLLING_DAYS: int = 7
NEUTRAL: float = 50.0
MIN_DAYS: int = 2          # minimum populated days before returning a score

MAX_CHARGE: float = 40.0        # units charged at recovery = 100
MAX_STRAIN_DRAIN: float = 25.0  # units drained at strain = 21
MAX_STRESS_DRAIN: float = 15.0  # units drained at stress_score = 100


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def daily_energy_delta(
    recovery_score: float | None,
    strain: float | None,
    stress_score: float | None = None,
) -> float | None:
    """Net energy change for a single day.

    Returns None when recovery or strain is unavailable (can't compute a
    meaningful delta without the two primary drivers).
    """
    if recovery_score is None or strain is None:
        return None

    charge = (float(recovery_score) / 100.0) * MAX_CHARGE

    strain_drain = (float(strain) / 21.0) * MAX_STRAIN_DRAIN
    stress_drain = 0.0
    if stress_score is not None:
        stress_drain = (float(stress_score) / 100.0) * MAX_STRESS_DRAIN

    return charge - strain_drain - stress_drain


def rolling_energy(
    prior_rows: Sequence[dict[str, Any]],
) -> float | None:
    """Rolling energy balance (0–100) over the trailing ROLLING_DAYS days.

    Parameters
    ----------
    prior_rows :
        Rows from ``daily_metrics``, oldest → newest, each with at least
        ``recovery``, ``strain``, and optionally ``stress_score``.

    Returns
    -------
    float | None
        Energy score in [0, 100], rounded to 1 dp.
        ``None`` when fewer than MIN_DAYS rows have the required data.
    """
    rows = list(prior_rows)[-ROLLING_DAYS:]

    total_delta = 0.0
    days_with_data = 0

    for row in rows:
        delta = daily_energy_delta(
            recovery_score=row.get("recovery"),
            strain=row.get("strain"),
            stress_score=row.get("stress_score"),
        )
        if delta is not None:
            total_delta += delta
            days_with_data += 1

    if days_with_data < MIN_DAYS:
        return None

    score = max(0.0, min(100.0, NEUTRAL + total_delta))
    return round(score, 1)
