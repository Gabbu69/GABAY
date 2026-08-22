from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import Any

QUANTUM = Decimal("0.0001")


@dataclass(frozen=True)
class DecisionResult:
    decision: str
    reliability: str
    forecast_vote: str
    trend_vote: str
    context_vote: str
    normalized_mae_pct: Decimal
    interval_half_width_pct: Decimal
    data_age_days: int
    reason_codes: tuple[str, ...]
    explanations: dict[str, str]


def _pct(value: Decimal) -> Decimal:
    return value.quantize(QUANTUM, rounding=ROUND_HALF_UP)


def _vote(change_pct: Decimal, threshold: Decimal) -> str:
    if change_pct >= threshold:
        return "WAIT"
    if change_pct <= -threshold:
        return "SELL"
    return "NEUTRAL"


def _context_vote(current: Decimal, baseline: Decimal, threshold: Decimal) -> str:
    difference_pct = ((current - baseline) / baseline) * Decimal(100)
    if difference_pct <= -threshold:
        return "WAIT"
    if difference_pct >= threshold:
        return "SELL"
    return "NEUTRAL"


def evaluate_decision(
    *,
    current_price: Decimal,
    historical_prices: Sequence[Decimal],
    point_forecast: Decimal,
    interval_lower: Decimal,
    interval_upper: Decimal,
    mae: Decimal,
    coverage_ratio: Decimal,
    data_as_of: date,
    rules: Any,
    today: date | None = None,
    trend_prices: Sequence[Decimal] | None = None,
    context_prices: Sequence[Decimal] | None = None,
) -> DecisionResult:
    """Apply the versioned, deterministic GABAY decision table."""

    if current_price <= 0 or point_forecast <= 0:
        raise ValueError("Current and forecast prices must be positive.")
    trend_history = trend_prices if trend_prices is not None else historical_prices
    context_history = context_prices if context_prices is not None else historical_prices
    if len(context_history) < 8:
        raise ValueError("At least eight historical weekly prices are required.")

    current_day = today or date.today()
    data_age_days = max(0, (current_day - data_as_of).days)
    normalized_mae = _pct((mae / current_price) * Decimal(100))
    half_width = max(point_forecast - interval_lower, interval_upper - point_forecast)
    interval_half_width = _pct((half_width / current_price) * Decimal(100))

    if normalized_mae <= rules.high_reliability_nmae_pct:
        reliability = "HIGH"
    elif normalized_mae <= rules.medium_reliability_nmae_pct:
        reliability = "MEDIUM"
    else:
        reliability = "LOW"

    forecast_change = ((point_forecast - current_price) / current_price) * Decimal(100)
    forecast_vote = _vote(forecast_change, rules.forecast_threshold_pct)

    trend_vote = "NEUTRAL"
    if len(trend_history) >= 4:
        latest_two = Decimal(str(median(trend_history[-2:])))
        preceding_two = Decimal(str(median(trend_history[-4:-2])))
        trend_change = ((latest_two - preceding_two) / preceding_two) * Decimal(100)
        trend_vote = _vote(trend_change, rules.trend_threshold_pct)

    context_baseline = Decimal(str(median(context_history[-8:])))
    context_vote = _context_vote(current_price, context_baseline, rules.context_threshold_pct)

    reasons: list[str] = [
        {"WAIT": "FORECAST_UP", "SELL": "FORECAST_DOWN"}.get(
            forecast_vote, "FORECAST_FLAT"
        )
    ]
    if trend_vote == "WAIT":
        reasons.append("TREND_UP")
    elif trend_vote == "SELL":
        reasons.append("TREND_DOWN")
    if context_vote == "WAIT":
        reasons.append("PRICE_LOW")
    elif context_vote == "SELL":
        reasons.append("PRICE_HIGH")
    gates: list[str] = []
    if data_age_days > rules.stale_after_days:
        gates.append("STALE_DATA")
    if coverage_ratio < rules.minimum_coverage_ratio:
        gates.extend(("INSUFFICIENT_DATA", "INSUFFICIENT_COVERAGE"))
    if reliability == "LOW":
        gates.append("LOW_RELIABILITY")
    if interval_half_width > rules.maximum_interval_half_width_pct:
        gates.append("WIDE_INTERVAL")

    opposing = "SELL" if forecast_vote == "WAIT" else "WAIT"
    supporting_votes = sum(
        vote == forecast_vote for vote in (trend_vote, context_vote)
    )
    has_opposition = opposing in (trend_vote, context_vote)

    if gates:
        decision = "MONITOR"
        reasons.extend(gates)
    elif forecast_vote in {"SELL", "WAIT"} and supporting_votes >= 1 and not has_opposition:
        decision = forecast_vote
    else:
        decision = "MONITOR"
        if has_opposition:
            reasons.append("CONFLICTING_SIGNALS")
        elif supporting_votes == 0:
            reasons.append("INSUFFICIENT_AGREEMENT")

    if decision == "SELL":
        en = "The forecast and recent evidence agree that selling now may be considered."
        tl = (
            "Magkatugma ang forecast at kamakailang datos na maaaring ikonsidera "
            "ang pagbebenta ngayon."
        )
    elif decision == "WAIT":
        en = "The forecast and recent evidence agree that waiting may be considered."
        tl = "Magkatugma ang forecast at kamakailang datos na maaaring ikonsidera ang paghihintay."
    else:
        en = "Evidence is insufficient or mixed. Monitor prices before deciding."
        tl = "Kulang o hindi magkakatugma ang ebidensya. Subaybayan muna ang presyo bago magpasya."

    return DecisionResult(
        decision=decision,
        reliability=reliability,
        forecast_vote=forecast_vote,
        trend_vote=trend_vote,
        context_vote=context_vote,
        normalized_mae_pct=normalized_mae,
        interval_half_width_pct=interval_half_width,
        data_age_days=data_age_days,
        reason_codes=tuple(reasons),
        explanations={"en": en, "tl": tl},
    )
