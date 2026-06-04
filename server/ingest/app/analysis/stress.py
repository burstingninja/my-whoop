"""
stress.py — Daily physiological stress score (0–100) from waking HR + HRV.

Ported from Goose's ``goose_stress_v0`` (Rust) and adapted for daily time-series
rather than point-in-time use.

Algorithm
---------
1. Slice the waking window (strain_lo → strain_hi) into non-overlapping
   WINDOW_S-second bins.
2. Per bin compute:
     a. HR elevation   = clamp(0, (mean_hr - resting_hr) / HR_CEILING × 100, 100)
     b. HRV suppression = clamp(0, (1 - bin_rmssd / hrv_baseline) × 100, 100)
     c. Motion intensity = clamp(0, mean_gravity_delta / MOTION_CEILING, 1)
        where gravity_delta = ||(g[i+1] - g[i])|| (vector magnitude of change).
     d. Motion-adjusted HR = HR_elevation × (1 - motion × MOTION_DISCOUNT)
        (intense exercise discounts the HR score so a workout doesn't read as stress)
     e. bin_stress = W_HR × motion_adjusted_hr + W_HRV × HRV_suppression
3. daily_stress = mean of bins with ≥ MIN_HR_SAMPLES HR readings.
4. Breakdown: minutes spent in high (≥ HIGH_THRESHOLD) / medium / low stress.

Returns None when there is not enough waking data to produce a trustworthy score
(< MIN_WINDOWS populated bins, or HRV baseline unavailable).

References
----------
- Goose project, Rust/core/src/metrics.rs ``goose_stress_v0`` (HR + HRV approach).
- Task Force (1996) RMSSD for short-window HRV (hrv.py, already imported).
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from .hrv import rmssd_ms, clean_rr

# ---------------------------------------------------------------------------
# Tuneable constants
# ---------------------------------------------------------------------------

#: Non-overlapping bin width (seconds). 5 minutes balances resolution vs noise.
WINDOW_S: float = 300.0

#: HR elevation ceiling: this many bpm above resting = max elevation score (100).
HR_CEILING: float = 60.0

#: Motion ceiling: gravity-vector change rate (g) that maps to motion intensity 1.
#: 0.3 g/s is vigorous movement; above this the bin is exercise-dominated.
MOTION_CEILING: float = 0.3

#: Maximum HR score discount applied at full motion intensity.
#: 0.50 = exercise can halve the HR elevation score.
MOTION_DISCOUNT: float = 0.50

#: Weight of motion-adjusted HR elevation in the composite.
W_HR: float = 0.60

#: Weight of HRV suppression in the composite.
W_HRV: float = 0.40

#: Minimum HR samples in a bin for it to count toward the daily score.
MIN_HR_SAMPLES: int = 3

#: Minimum populated bins required to return a daily score (not None).
MIN_WINDOWS: int = 3

#: Stress band thresholds (same semantics as Goose's high / medium / low).
HIGH_THRESHOLD: float = 66.0
LOW_THRESHOLD: float = 33.0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def daily_stress(
    hr: Sequence[dict[str, Any]],
    rr: Sequence[dict[str, Any]],
    gravity: Sequence[dict[str, Any]],
    resting_hr: float,
    hrv_baseline_ms: float | None,
    window_start: float,
    window_end: float,
) -> dict[str, Any] | None:
    """Compute a daily stress score over the waking window.

    Parameters
    ----------
    hr :
        List of ``{"ts": float, "bpm": int|float}`` from the HR stream.
    rr :
        List of ``{"ts": float, "rr_ms": int|float}`` from the R-R stream.
    gravity :
        List of ``{"ts": float, "x": float, "y": float, "z": float}`` accel in g.
    resting_hr :
        Personal resting HR (bpm) — the nightly floor from the recovery pipeline.
    hrv_baseline_ms :
        Personal HRV baseline (RMSSD, ms) from the Winsorized EWMA.
        ``None`` → HRV suppression term is dropped; W_HR renormalized to 1.0.
    window_start, window_end :
        Epoch seconds for the waking window (matches strain_lo / strain_hi).

    Returns
    -------
    dict | None
        ``{"score": float, "high_min": float, "mid_min": float, "low_min": float,
           "window_count": int}``
        or ``None`` when there is insufficient data.
    """
    if window_end <= window_start:
        return None

    # Pre-filter all streams to the waking window once.
    hr_in   = [r for r in hr      if window_start <= r["ts"] < window_end]
    rr_in   = [r for r in rr      if window_start <= r["ts"] < window_end]
    grav_in = [r for r in gravity  if window_start <= r["ts"] < window_end]

    # Pre-compute per-sample gravity-change magnitude (Δg between consecutive samples).
    grav_deltas = _gravity_deltas(grav_in)

    bin_scores: list[float] = []
    high_s = mid_s = low_s = 0.0

    t = window_start
    while t < window_end:
        t_next = t + WINDOW_S
        score = _bin_stress(t, t_next, hr_in, rr_in, grav_deltas,
                            resting_hr, hrv_baseline_ms)
        if score is not None:
            bin_scores.append(score)
            bin_s = min(WINDOW_S, window_end - t)
            if score >= HIGH_THRESHOLD:
                high_s += bin_s
            elif score >= LOW_THRESHOLD:
                mid_s += bin_s
            else:
                low_s += bin_s
        t = t_next

    if len(bin_scores) < MIN_WINDOWS:
        return None

    return {
        "score":       round(sum(bin_scores) / len(bin_scores), 1),
        "high_min":    round(high_s / 60.0, 1),
        "mid_min":     round(mid_s  / 60.0, 1),
        "low_min":     round(low_s  / 60.0, 1),
        "window_count": len(bin_scores),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _bin_stress(
    t0: float,
    t1: float,
    hr: Sequence[dict[str, Any]],
    rr: Sequence[dict[str, Any]],
    grav_deltas: list[tuple[float, float]],
    resting_hr: float,
    hrv_baseline_ms: float | None,
) -> float | None:
    """Stress score for a single time bin [t0, t1). Returns None if too little data."""
    bin_hr = [float(r["bpm"]) for r in hr
              if t0 <= r["ts"] < t1 and r.get("bpm") is not None]
    if len(bin_hr) < MIN_HR_SAMPLES:
        return None

    mean_hr = sum(bin_hr) / len(bin_hr)

    # HR elevation score (higher HR above resting = more stress)
    hr_elevation = _clamp(0.0, (mean_hr - resting_hr) / HR_CEILING * 100.0, 100.0)

    # Motion intensity (suppress HR score during exercise)
    bin_deltas = [d for ts, d in grav_deltas if t0 <= ts < t1]
    motion = _clamp(0.0,
                    (sum(bin_deltas) / len(bin_deltas) / MOTION_CEILING) if bin_deltas else 0.0,
                    1.0)

    motion_adjusted_hr = hr_elevation * (1.0 - motion * MOTION_DISCOUNT)

    # HRV suppression score (lower HRV vs baseline = more stress)
    hrv_suppression = 0.0
    if hrv_baseline_ms is not None and hrv_baseline_ms > 0:
        bin_rr = [float(r["rr_ms"]) for r in rr
                  if t0 <= r["ts"] < t1 and r.get("rr_ms") is not None]
        if len(bin_rr) >= 4:
            nn_clean, _, _, _ = clean_rr(bin_rr)
            if nn_clean.size >= 2:
                bin_rmssd = rmssd_ms(nn_clean)
                hrv_suppression = _clamp(
                    0.0,
                    (1.0 - bin_rmssd / hrv_baseline_ms) * 100.0,
                    100.0,
                )

    # Composite — renormalize if HRV baseline absent
    if hrv_baseline_ms is None or hrv_baseline_ms <= 0:
        return motion_adjusted_hr  # W_HR = 1.0
    return W_HR * motion_adjusted_hr + W_HRV * hrv_suppression


def _gravity_deltas(grav: Sequence[dict[str, Any]]) -> list[tuple[float, float]]:
    """Return (timestamp, delta_magnitude) pairs for consecutive gravity samples.

    delta = ||g[i+1] - g[i]||  — magnitude of the change vector between adjacent
    samples.  Timestamps are assigned to the later sample so the result can be
    binned by sample time.
    """
    out: list[tuple[float, float]] = []
    prev = None
    for r in sorted(grav, key=lambda x: x["ts"]):
        if prev is not None:
            dx = r.get("x", 0.0) - prev.get("x", 0.0)
            dy = r.get("y", 0.0) - prev.get("y", 0.0)
            dz = r.get("z", 0.0) - prev.get("z", 0.0)
            out.append((r["ts"], math.sqrt(dx*dx + dy*dy + dz*dz)))
        prev = r
    return out


def _clamp(lo: float, val: float, hi: float) -> float:
    return max(lo, min(hi, val))
