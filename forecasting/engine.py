"""Deterministic, low-memory forecasting primitives.

This module intentionally imports no NumPy, pandas, SciPy, or statsmodels at
module import time. The bounded ARIMA adapter loads its scientific dependencies
only while an explicitly requested experiment is running.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import Any

MINIMUM_OBSERVED_WEEKS = 64
INITIAL_TRAINING_WEEKS = 52
EVALUATION_FOLDS = 12
MINIMUM_COVERAGE = Decimal("0.90")
QUANTUM = Decimal("0.0001")


class ForecastingError(Exception):
    """Base error for an experiment that cannot produce a defensible forecast."""


class InsufficientDataError(ForecastingError):
    """Raised when a series does not satisfy the declared research protocol."""


class ModelUnavailableError(ForecastingError):
    """Raised when an optional model dependency cannot be loaded or fitted."""


@dataclass(frozen=True)
class WeeklyPoint:
    week_start: date
    value: Decimal
    observed: bool = True


@dataclass(frozen=True)
class PreparedSeries:
    points: tuple[WeeklyPoint, ...]
    observed_week_count: int
    span_week_count: int
    coverage_ratio: Decimal
    imputed_weeks: tuple[date, ...]


@dataclass(frozen=True)
class FoldResult:
    model_name: str
    fold_index: int
    train_through: date
    target_week: date
    actual: Decimal
    predicted: Decimal
    absolute_error: Decimal
    squared_error: Decimal
    absolute_percentage_error: Decimal | None
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ModelScore:
    model_name: str
    mae: Decimal
    rmse: Decimal
    mape: Decimal
    folds: int
    available: bool = True
    error: str = ""


@dataclass(frozen=True)
class ExperimentResult:
    prepared: PreparedSeries
    folds: tuple[FoldResult, ...]
    scores: tuple[ModelScore, ...]
    selected_model: str
    selected_parameters: dict[str, Any]
    point_forecast: Decimal
    interval_lower: Decimal
    interval_upper: Decimal
    residuals: tuple[Decimal, ...]
    target_week: date


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - normalize a public boundary
        raise InsufficientDataError(f"Invalid weekly price value: {value!r}") from exc
    if not result.is_finite() or result <= 0:
        raise InsufficientDataError("Weekly prices must be finite positive values.")
    return result


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise InsufficientDataError(f"Invalid week date: {value!r}") from exc


def week_monday(value: date) -> date:
    return value - timedelta(days=value.weekday())


def prepare_series(rows: Iterable[dict[str, Any] | Sequence[Any]]) -> PreparedSeries:
    """Normalize weekly rows, collapse duplicates, and interpolate isolated gaps.

    Accepted rows are dictionaries with ``week_start`` and ``price`` keys or
    two-item ``(date, price)`` sequences. Coverage is always based on observed,
    not imputed, weeks.
    """

    grouped: dict[date, list[Decimal]] = {}
    for row in rows:
        if isinstance(row, dict):
            raw_date = row.get("week_start")
            raw_value = row.get("price")
        else:
            try:
                raw_date, raw_value = row[0], row[1]
            except (IndexError, TypeError) as exc:
                raise InsufficientDataError("Each row needs a week and price.") from exc
        week = week_monday(_date(raw_date))
        grouped.setdefault(week, []).append(_decimal(raw_value))

    if not grouped:
        raise InsufficientDataError("No verified weekly observations are available.")

    collapsed = {week: Decimal(str(median(values))) for week, values in grouped.items()}
    first_week, last_week = min(collapsed), max(collapsed)
    span = ((last_week - first_week).days // 7) + 1
    observed_count = len(collapsed)
    coverage = Decimal(observed_count) / Decimal(span)

    if observed_count < MINIMUM_OBSERVED_WEEKS:
        raise InsufficientDataError(
            f"At least {MINIMUM_OBSERVED_WEEKS} observed weeks are required; "
            f"found {observed_count}."
        )
    if coverage < MINIMUM_COVERAGE:
        raise InsufficientDataError(
            f"Coverage must be at least 90%; found {(coverage * 100):.1f}%."
        )

    weeks = [first_week + timedelta(weeks=index) for index in range(span)]
    values: list[Decimal | None] = [collapsed.get(week) for week in weeks]
    imputed: list[date] = []
    for index in range(1, len(values) - 1):
        if (
            values[index] is None
            and values[index - 1] is not None
            and values[index + 1] is not None
        ):
            values[index] = (values[index - 1] + values[index + 1]) / Decimal(2)
            imputed.append(weeks[index])

    unresolved = [weeks[index] for index, value in enumerate(values) if value is None]
    if unresolved:
        preview = ", ".join(week.isoformat() for week in unresolved[:3])
        raise InsufficientDataError(
            "Only isolated one-week gaps can be interpolated; unresolved gaps include " + preview
        )

    points = tuple(
        WeeklyPoint(week, value, observed=week not in imputed)
        for week, value in zip(weeks, values, strict=True)
        if value is not None
    )
    return PreparedSeries(
        points=points,
        observed_week_count=observed_count,
        span_week_count=span,
        coverage_ratio=coverage,
        imputed_weeks=tuple(imputed),
    )


def _naive(values: Sequence[Decimal]) -> tuple[Decimal, dict[str, Any]]:
    return values[-1], {}


def _moving_average_4(values: Sequence[Decimal]) -> tuple[Decimal, dict[str, Any]]:
    if len(values) < 4:
        raise ModelUnavailableError("MA4 requires four training observations.")
    return sum(values[-4:]) / Decimal(4), {"window": 4}


def _ses_for_alpha(values: Sequence[Decimal], alpha: Decimal) -> tuple[Decimal, Decimal]:
    level = values[0]
    squared_error = Decimal(0)
    for actual in values[1:]:
        error = actual - level
        squared_error += error * error
        level = alpha * actual + (Decimal(1) - alpha) * level
    return level, squared_error


def _simple_exponential_smoothing(
    values: Sequence[Decimal],
) -> tuple[Decimal, dict[str, Any]]:
    candidates = [Decimal(index) / Decimal(10) for index in range(1, 10)]
    fitted = [(*_ses_for_alpha(values, alpha), alpha) for alpha in candidates]
    level, _sse, alpha = min(fitted, key=lambda item: (item[1], item[2]))
    return level, {"alpha": str(alpha)}


def _arima_predictor(
    order: tuple[int, int, int],
) -> Callable[[Sequence[Decimal]], tuple[Decimal, dict]]:
    def predict(values: Sequence[Decimal]) -> tuple[Decimal, dict[str, Any]]:
        try:
            import numpy as np  # type: ignore[import-not-found]
            from statsmodels.tsa.arima.model import ARIMA  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ModelUnavailableError(
                "ARIMA dependencies are unavailable; install the pinned research requirements."
            ) from exc

        try:
            numeric = np.asarray([float(value) for value in values], dtype=float)
            with warnings.catch_warnings():
                # Expected candidate-fit warnings are recorded as unavailability below;
                # they must not flood low-resource Windows logs.
                warnings.simplefilter("ignore")
                fitted = ARIMA(
                    numeric,
                    order=order,
                    trend="n",
                    enforce_stationarity=True,
                    enforce_invertibility=True,
                ).fit(method_kwargs={"warn_convergence": False})
            predicted = float(fitted.forecast(steps=1)[0])
        except Exception as exc:  # noqa: BLE001 - model fit errors are recorded per candidate
            raise ModelUnavailableError(f"ARIMA{order} could not be fitted: {exc}") from exc
        if not math.isfinite(predicted) or predicted <= 0:
            raise ModelUnavailableError(f"ARIMA{order} produced an invalid price forecast.")
        return Decimal(str(predicted)), {"order": list(order)}

    return predict


MODEL_SPECS: tuple[tuple[str, Callable[[Sequence[Decimal]], tuple[Decimal, dict]]], ...] = (
    ("naive", _naive),
    ("moving_average_4", _moving_average_4),
    ("simple_exponential_smoothing", _simple_exponential_smoothing),
    ("arima_1_1_0", _arima_predictor((1, 1, 0))),
    ("arima_0_1_1", _arima_predictor((0, 1, 1))),
    ("arima_1_1_1", _arima_predictor((1, 1, 1))),
)


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(QUANTUM, rounding=ROUND_HALF_UP)


def _score(model_name: str, folds: Sequence[FoldResult]) -> ModelScore:
    count = Decimal(len(folds))
    mae = sum(fold.absolute_error for fold in folds) / count
    rmse = Decimal(str(math.sqrt(float(sum(fold.squared_error for fold in folds) / count))))
    percentage_errors = [
        fold.absolute_percentage_error
        for fold in folds
        if fold.absolute_percentage_error is not None
    ]
    mape = sum(percentage_errors, Decimal(0)) / Decimal(len(percentage_errors))
    return ModelScore(model_name, _quantize(mae), _quantize(rmse), _quantize(mape), len(folds))


def run_experiment(rows: Iterable[dict[str, Any] | Sequence[Any]]) -> ExperimentResult:
    prepared = prepare_series(rows)
    points = prepared.points
    eligible_targets = [
        index
        for index in range(INITIAL_TRAINING_WEEKS, len(points))
        if points[index].observed
    ]
    target_indexes = eligible_targets[-EVALUATION_FOLDS:]
    if len(target_indexes) < EVALUATION_FOLDS:
        raise InsufficientDataError(
            f"The series needs {EVALUATION_FOLDS} observed rolling-origin evaluation targets."
        )

    all_folds: list[FoldResult] = []
    scores: list[ModelScore] = []
    successful_predictors: dict[str, Callable[[Sequence[Decimal]], tuple[Decimal, dict]]] = {}
    for model_name, predictor in MODEL_SPECS:
        model_folds: list[FoldResult] = []
        try:
            for fold_number, target_index in enumerate(target_indexes, start=1):
                training = [point.value for point in points[:target_index]]
                prediction, parameters = predictor(training)
                prediction = _quantize(prediction)
                actual = points[target_index].value
                absolute_error = abs(actual - prediction)
                squared_error = (actual - prediction) ** 2
                ape = (absolute_error / actual) * Decimal(100) if actual else None
                model_folds.append(
                    FoldResult(
                        model_name=model_name,
                        fold_index=fold_number,
                        train_through=points[target_index - 1].week_start,
                        target_week=points[target_index].week_start,
                        actual=_quantize(actual),
                        predicted=prediction,
                        absolute_error=_quantize(absolute_error),
                        squared_error=squared_error.quantize(
                            Decimal("0.000001"), rounding=ROUND_HALF_UP
                        ),
                        absolute_percentage_error=_quantize(ape) if ape is not None else None,
                        parameters=parameters,
                    )
                )
        except ModelUnavailableError as exc:
            scores.append(
                ModelScore(
                    model_name,
                    Decimal(0),
                    Decimal(0),
                    Decimal(0),
                    0,
                    available=False,
                    error=str(exc)[:500],
                )
            )
            continue
        all_folds.extend(model_folds)
        scores.append(_score(model_name, model_folds))
        successful_predictors[model_name] = predictor

    available_scores = [score for score in scores if score.available]
    if not available_scores:
        raise ModelUnavailableError("No candidate model completed all evaluation folds.")
    winner = min(
        available_scores,
        key=lambda item: (item.mae, item.rmse, item.mape, item.model_name),
    )
    winner_folds = [fold for fold in all_folds if fold.model_name == winner.model_name]
    residuals = tuple(_quantize(fold.actual - fold.predicted) for fold in winner_folds)
    absolute_residuals = sorted(abs(value) for value in residuals)
    rank = max(0, math.ceil(Decimal("0.80") * len(absolute_residuals)) - 1)
    half_width = absolute_residuals[rank]

    predictor = successful_predictors[winner.model_name]
    point_forecast, selected_parameters = predictor([point.value for point in points])
    point_forecast = _quantize(point_forecast)
    lower = max(QUANTUM, _quantize(point_forecast - half_width))
    upper = _quantize(point_forecast + half_width)

    return ExperimentResult(
        prepared=prepared,
        folds=tuple(all_folds),
        scores=tuple(scores),
        selected_model=winner.model_name,
        selected_parameters=selected_parameters,
        point_forecast=point_forecast,
        interval_lower=lower,
        interval_upper=upper,
        residuals=residuals,
        target_week=points[-1].week_start + timedelta(weeks=1),
    )
