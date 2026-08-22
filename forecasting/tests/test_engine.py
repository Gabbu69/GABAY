from datetime import date, timedelta
from decimal import Decimal

import pytest

from forecasting.engine import (
    EVALUATION_FOLDS,
    InsufficientDataError,
    prepare_series,
    run_experiment,
)


def weekly_rows(count=64, *, start=date(2025, 1, 6)):
    return [
        {
            "week_start": start + timedelta(weeks=index),
            "price": Decimal("20") + Decimal(index % 7) / Decimal(10),
        }
        for index in range(count)
    ]


def test_prepare_series_requires_64_observed_weeks():
    with pytest.raises(InsufficientDataError, match="At least 64"):
        prepare_series(weekly_rows(63))


def test_prepare_series_interpolates_only_an_isolated_gap():
    rows = weekly_rows(65)
    del rows[10]
    prepared = prepare_series(rows)
    assert prepared.observed_week_count == 64
    assert prepared.span_week_count == 65
    assert prepared.imputed_weeks == (date(2025, 3, 17),)
    assert prepared.points[10].observed is False
    assert prepared.points[10].value == (prepared.points[9].value + prepared.points[11].value) / 2


def test_prepare_series_rejects_nonisolated_gaps():
    rows = weekly_rows(70)
    del rows[10:12]
    with pytest.raises(InsufficientDataError, match="Only isolated"):
        prepare_series(rows)


def test_prepare_series_rejects_coverage_below_90_percent():
    rows = weekly_rows(75)
    del rows[10:20]
    with pytest.raises(InsufficientDataError, match="Coverage"):
        prepare_series(rows)


def test_experiment_uses_same_12_targets_and_keeps_naive_eligible():
    result = run_experiment(weekly_rows())
    available = [score for score in result.scores if score.available]
    assert {"naive", "moving_average_4", "simple_exponential_smoothing"}.issubset(
        {score.model_name for score in available}
    )
    assert all(score.folds == EVALUATION_FOLDS for score in available)
    targets_by_model = {
        score.model_name: {
            fold.target_week for fold in result.folds if fold.model_name == score.model_name
        }
        for score in available
    }
    assert len({frozenset(targets) for targets in targets_by_model.values()}) == 1
    assert result.selected_model in {score.model_name for score in available}
    assert result.interval_lower <= result.point_forecast <= result.interval_upper
    assert result.target_week == date(2026, 3, 30)


def test_model_selection_follows_mae_then_rmse_then_mape():
    result = run_experiment(
        [
            {"week_start": date(2025, 1, 6) + timedelta(weeks=index), "price": "25.00"}
            for index in range(64)
        ]
    )
    winner = next(score for score in result.scores if score.model_name == result.selected_model)
    candidates = [score for score in result.scores if score.available]
    assert (winner.mae, winner.rmse, winner.mape, winner.model_name) == min(
        (score.mae, score.rmse, score.mape, score.model_name) for score in candidates
    )
