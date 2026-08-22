from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from forecasting.decisions import evaluate_decision

RULES = SimpleNamespace(
    forecast_threshold_pct=Decimal("3"),
    trend_threshold_pct=Decimal("1"),
    context_threshold_pct=Decimal("3"),
    stale_after_days=14,
    minimum_coverage_ratio=Decimal("0.90"),
    high_reliability_nmae_pct=Decimal("5"),
    medium_reliability_nmae_pct=Decimal("10"),
    maximum_interval_half_width_pct=Decimal("10"),
)


def decide(**overrides):
    values = {
        "current_price": Decimal("10"),
        "historical_prices": [
            Decimal(value) for value in (12, 12, 12, 12, 11, 11, 10, 10, 10.2, 10.2)
        ],
        "point_forecast": Decimal("10.3"),
        "interval_lower": Decimal("10.0"),
        "interval_upper": Decimal("10.6"),
        "mae": Decimal("0.4"),
        "coverage_ratio": Decimal("0.95"),
        "data_as_of": date(2026, 8, 20),
        "rules": RULES,
        "today": date(2026, 8, 22),
    }
    values.update(overrides)
    return evaluate_decision(**values)


def test_exact_forecast_threshold_can_emit_wait_when_evidence_agrees():
    result = decide()
    assert result.forecast_vote == "WAIT"
    assert result.trend_vote == "WAIT"
    assert result.context_vote == "WAIT"
    assert result.decision == "WAIT"
    assert result.reliability == "HIGH"


def test_exact_negative_forecast_threshold_can_emit_sell_when_evidence_agrees():
    result = decide(
        historical_prices=[Decimal(value) for value in (8, 8, 8, 8, 9, 9, 10, 10, 9.8, 9.8)],
        point_forecast=Decimal("9.7"),
    )
    assert result.forecast_vote == "SELL"
    assert result.trend_vote == "SELL"
    assert result.context_vote == "SELL"
    assert result.decision == "SELL"


def test_conflicting_factor_forces_monitor():
    result = decide(
        historical_prices=[Decimal(value) for value in (8, 8, 8, 8, 9, 9, 10, 10, 9.8, 9.8)],
    )
    assert result.forecast_vote == "WAIT"
    assert "SELL" in (result.trend_vote, result.context_vote)
    assert result.decision == "MONITOR"
    assert "CONFLICTING_SIGNALS" in result.reason_codes


def test_stale_data_forces_monitor_even_when_votes_agree():
    result = decide(data_as_of=date(2026, 8, 7))
    assert result.data_age_days == 15
    assert result.decision == "MONITOR"
    assert "STALE_DATA" in result.reason_codes


def test_low_coverage_low_reliability_and_wide_interval_are_explicit_gates():
    result = decide(
        coverage_ratio=Decimal("0.89"),
        mae=Decimal("1.01"),
        interval_lower=Decimal("8.9"),
        interval_upper=Decimal("11.4"),
    )
    assert result.reliability == "LOW"
    assert result.decision == "MONITOR"
    assert {"INSUFFICIENT_COVERAGE", "LOW_RELIABILITY", "WIDE_INTERVAL"}.issubset(
        result.reason_codes
    )


def test_reliability_cutoffs_are_inclusive():
    assert decide(mae=Decimal("0.5")).reliability == "HIGH"
    assert decide(mae=Decimal("1.0")).reliability == "MEDIUM"
    assert decide(mae=Decimal("1.0001")).reliability == "LOW"
