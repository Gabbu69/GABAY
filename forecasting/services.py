from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connections, transaction
from django.utils import timezone

from .decisions import DecisionResult, evaluate_decision
from .engine import ExperimentResult, ForecastingError
from .engine import run_experiment as calculate_experiment
from .models import (
    DatasetSnapshot,
    DecisionRuleSet,
    FoldMetric,
    Forecast,
    ModelRun,
    Recommendation,
)


class ExperimentAlreadyRunningError(RuntimeError):
    pass


class ExperimentWorkerError(RuntimeError):
    pass


class ForecastObservationUnavailableError(RuntimeError):
    pass


def _iso_date(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)).isoformat()


def _canonical_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    for row in rows:
        sources = sorted(
            (
                {
                    "id": source.pk,
                    "slug": source.slug,
                    "name": source.name,
                    "source_type": source.source_type,
                    "publication_allowed": source.publication_allowed,
                }
                for source in row.get("sources", [])
            ),
            key=lambda source: (source["id"], source["slug"]),
        )
        canonical.append(
            {
                "week_start": _iso_date(row["week_start"]),
                "price": format(Decimal(str(row["price"])), "f"),
                "low": format(Decimal(str(row.get("low", row["price"]))), "f"),
                "high": format(Decimal(str(row.get("high", row["price"]))), "f"),
                "count": int(row.get("count", 1)),
                "source_count": int(row.get("source_count", len(sources))),
                "sources": sources,
                "observed_on": _iso_date(
                    row.get("latest_observed_on", row.get("observed_on", row["week_start"]))
                ),
                "is_demo": bool(row.get("is_demo", row.get("scope") == "demo")),
            }
        )
    return sorted(canonical, key=lambda item: item["week_start"])


def _dataset_fingerprint(
    series,
    canonical_rows: list[dict[str, Any]],
    *,
    public_inputs_only: bool,
) -> str:
    payload = {
        "series": {
            "id": series.pk,
            "slug": series.slug,
            "scope": series.scope,
            "location_name": series.location_name,
            "classification": series.classification,
            "condition": series.condition,
            "is_primary_public": series.is_primary_public,
            "moisture_basis": (
                format(series.moisture_basis, "f") if series.moisture_basis is not None else None
            ),
        },
        "public_inputs_only": public_inputs_only,
        "rows": canonical_rows,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _snapshot(
    series,
    rows: list[dict[str, Any]],
    user=None,
    *,
    public_inputs_only: bool,
) -> DatasetSnapshot:
    canonical = _canonical_rows(rows)
    if not canonical:
        raise ForecastingError("No verified weekly observations are available for this series.")
    fingerprint = _dataset_fingerprint(
        series,
        canonical,
        public_inputs_only=public_inputs_only,
    )
    first = date.fromisoformat(canonical[0]["week_start"])
    last = date.fromisoformat(canonical[-1]["week_start"])
    span = ((last - first).days // 7) + 1
    observed_weeks = len({item["week_start"] for item in canonical})
    coverage = Decimal(observed_weeks) / Decimal(span)
    snapshot, _created = DatasetSnapshot.objects.get_or_create(
        fingerprint=fingerprint,
        defaults={
            "series": series,
            "cutoff_date": max(date.fromisoformat(item["observed_on"]) for item in canonical),
            "first_week": first,
            "last_week": last,
            "observed_week_count": observed_weeks,
            "span_week_count": span,
            "coverage_ratio": coverage,
            "public_inputs_only": public_inputs_only,
            "weekly_values": canonical,
            "preprocessing_report": {
                "aggregation": "ISO week median",
                "duplicate_policy": "weekly rows canonicalized by the price service",
                "gap_policy": "isolated one-week interpolation only during model fitting",
                "observed_weeks": observed_weeks,
                "span_weeks": span,
                "publication_filter": (
                    "publication-approved sources only"
                    if public_inputs_only
                    else "internal verified evidence allowed; simulation use only"
                ),
            },
            "created_by": user if getattr(user, "is_authenticated", False) else None,
        },
    )
    return snapshot


def get_or_create_draft_rules(user=None) -> DecisionRuleSet:
    rules, _created = DecisionRuleSet.objects.get_or_create(
        version="DRAFT-v1",
        defaults={
            "label": "Thesis protocol defaults — adviser approval pending",
            "created_by": user if getattr(user, "is_authenticated", False) else None,
        },
    )
    return rules


def get_active_rules(user=None) -> DecisionRuleSet:
    return (
        DecisionRuleSet.objects.filter(status=DecisionRuleSet.Status.FROZEN)
        .order_by("-frozen_at")
        .first()
        or get_or_create_draft_rules(user)
    )


def user_can_run_experiments(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(
        user.is_superuser
        or user.has_perm("forecasting.run_model_experiment")
        or user.groups.filter(name="research_admin").exists()
    )


def _score_payload(result: ExperimentResult) -> dict[str, dict[str, Any]]:
    return {
        score.model_name: {
            "available": score.available,
            "mae": str(score.mae) if score.available else None,
            "rmse": str(score.rmse) if score.available else None,
            "mape": str(score.mape) if score.available else None,
            "folds": score.folds,
            "error": score.error,
        }
        for score in result.scores
    }


def _selected_mae(result: ExperimentResult) -> Decimal:
    return next(score.mae for score in result.scores if score.model_name == result.selected_model)


def _latest_observed_date(rows: list[dict[str, Any]]) -> date:
    return max(date.fromisoformat(item["observed_on"]) for item in rows)


def _persist_success(
    *,
    model_run: ModelRun,
    result: ExperimentResult,
    source_rows: list[dict[str, Any]],
    elapsed: float,
    user=None,
    publish_live: bool,
) -> Forecast:
    score_payload = _score_payload(result)
    with transaction.atomic():
        locked_run = ModelRun.objects.select_for_update().get(pk=model_run.pk)
        report = dict(locked_run.snapshot.preprocessing_report)
        report.update(
            {
                "imputed_weeks": [week.isoformat() for week in result.prepared.imputed_weeks],
                "imputed_week_count": len(result.prepared.imputed_weeks),
                "coverage_ratio_before_imputation": str(result.prepared.coverage_ratio),
            }
        )
        DatasetSnapshot.objects.filter(pk=locked_run.snapshot_id).update(
            preprocessing_report=report
        )
        FoldMetric.objects.bulk_create(
            [
                FoldMetric(
                    model_run=locked_run,
                    model_name=fold.model_name,
                    fold_index=fold.fold_index,
                    train_through=fold.train_through,
                    target_week=fold.target_week,
                    actual=fold.actual,
                    predicted=fold.predicted,
                    absolute_error=fold.absolute_error,
                    squared_error=fold.squared_error,
                    absolute_percentage_error=fold.absolute_percentage_error,
                    parameters=fold.parameters,
                )
                for fold in result.folds
            ]
        )
        locked_run.status = ModelRun.Status.SUCCEEDED
        locked_run.run_slot = None
        locked_run.finished_at = timezone.now()
        locked_run.runtime_seconds = Decimal(str(round(elapsed, 3)))
        locked_run.selected_model = result.selected_model
        locked_run.selected_parameters = result.selected_parameters
        locked_run.aggregate_metrics = score_payload
        locked_run.residuals = [str(value) for value in result.residuals]
        locked_run.target_week = result.target_week
        locked_run.point_forecast = result.point_forecast
        locked_run.interval_lower = result.interval_lower
        locked_run.interval_upper = result.interval_upper

        kind = Forecast.Kind.LIVE if publish_live else Forecast.Kind.SIMULATION
        if result.target_week <= timezone.localdate():
            kind = Forecast.Kind.SIMULATION
        if kind == Forecast.Kind.LIVE and Forecast.objects.filter(
            series=locked_run.snapshot.series,
            target_week=result.target_week,
            kind=Forecast.Kind.LIVE,
        ).exists():
            kind = Forecast.Kind.SIMULATION
        locked_run.is_live = kind == Forecast.Kind.LIVE
        locked_run.save(
            update_fields=(
                "status",
                "run_slot",
                "finished_at",
                "runtime_seconds",
                "selected_model",
                "selected_parameters",
                "aggregate_metrics",
                "residuals",
                "target_week",
                "point_forecast",
                "interval_lower",
                "interval_upper",
                "is_live",
            )
        )

        forecast = Forecast.objects.create(
            model_run=locked_run,
            series=locked_run.snapshot.series,
            kind=kind,
            target_week=result.target_week,
            point_forecast=result.point_forecast,
            interval_lower=result.interval_lower,
            interval_upper=result.interval_upper,
            data_cutoff=locked_run.snapshot.cutoff_date,
            dataset_fingerprint=locked_run.snapshot.fingerprint,
            model_name=result.selected_model,
            model_parameters=result.selected_parameters,
        )
        rules = get_active_rules(user)
        latest_data_date = _latest_observed_date(source_rows)
        current_price = result.prepared.points[-1].value
        all_prices = [point.value for point in result.prepared.points]
        decision = evaluate_decision(
            current_price=current_price,
            historical_prices=all_prices[:-1],
            point_forecast=result.point_forecast,
            interval_lower=result.interval_lower,
            interval_upper=result.interval_upper,
            mae=_selected_mae(result),
            coverage_ratio=result.prepared.coverage_ratio,
            data_as_of=latest_data_date,
            rules=rules,
            trend_prices=all_prices,
            context_prices=all_prices[:-1],
        )
        recommendation_decision = decision.decision
        recommendation_reliability = decision.reliability
        reason_codes = list(decision.reason_codes)
        explanations = decision.explanations
        if kind == Forecast.Kind.LIVE and rules.status != DecisionRuleSet.Status.FROZEN:
            recommendation_decision = Recommendation.Decision.MONITOR
            recommendation_reliability = Recommendation.Reliability.UNASSESSED
            reason_codes = ["RULE_PROTOCOL_UNAPPROVED", *reason_codes]
            explanations = {
                "en": (
                    "Monitor — the decision protocol is still awaiting adviser approval. "
                    "The numeric forecast is shown without directional advice."
                ),
                "tl": (
                    "Subaybayan — hinihintay pa ng tuntunin sa pagpapasya ang pag-apruba "
                    "ng tagapayo. Ipinapakita ang numerong taya nang walang direksiyonal na payo."
                ),
            }
        Recommendation.objects.create(
            forecast=forecast,
            rule_set=rules,
            decision=recommendation_decision,
            reliability=recommendation_reliability,
            current_price=current_price,
            data_as_of=latest_data_date,
            data_age_days=decision.data_age_days,
            normalized_mae_pct=decision.normalized_mae_pct,
            interval_half_width_pct=decision.interval_half_width_pct,
            coverage_ratio=result.prepared.coverage_ratio,
            forecast_vote=decision.forecast_vote,
            trend_vote=decision.trend_vote,
            context_vote=decision.context_vote,
            reason_codes=reason_codes,
            explanations=explanations,
            is_demo=locked_run.snapshot.series.scope == "demo",
        )
        return forecast


def run_model_experiment(
    series,
    *,
    user=None,
    through: date | None = None,
    publish_live: bool = True,
    enforce_permission: bool = True,
    time_limit_seconds: int | None = None,
) -> ModelRun:
    """Synchronously run one reproducible experiment and publish its locked ledger entry."""

    if enforce_permission and not user_can_run_experiments(user):
        raise PermissionDenied("Only a research administrator can run model experiments.")

    # This import is intentionally local so public page imports do not pull in the
    # price processing path, and certainly never scientific libraries.
    from prices.services import get_weekly_series

    if publish_live and not getattr(series, "is_primary_public", False):
        raise ForecastingError(
            "Live publication is limited to the explicitly selected primary public series."
        )

    for variable in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ.setdefault(variable, "1")

    rows = list(
        get_weekly_series(series, through=through, public_only=publish_live)
    )
    snapshot = _snapshot(
        series,
        rows,
        user,
        public_inputs_only=publish_live,
    )
    try:
        with transaction.atomic():
            model_run = ModelRun.objects.create(
                snapshot=snapshot,
                status=ModelRun.Status.RUNNING,
                run_slot=1,
                requested_by=user if getattr(user, "is_authenticated", False) else None,
                started_at=timezone.now(),
                is_live=publish_live,
            )
    except IntegrityError as exc:
        if ModelRun.objects.filter(
            status=ModelRun.Status.RUNNING,
            run_slot=1,
        ).exists():
            raise ExperimentAlreadyRunningError(
                "Another model experiment is already running."
            ) from exc
        raise

    started = time.monotonic()
    limit = time_limit_seconds
    if limit is None:
        limit = 180 if getattr(settings, "IS_LITE", False) else 60
    try:
        result = calculate_experiment(rows)
        elapsed = time.monotonic() - started
        if elapsed > limit:
            raise ForecastingError(
                f"Experiment exceeded the {limit}-second release budget ({elapsed:.1f}s)."
            )
        _persist_success(
            model_run=model_run,
            result=result,
            source_rows=_canonical_rows(rows),
            elapsed=elapsed,
            user=user,
            publish_live=publish_live,
        )
    except Exception as exc:
        elapsed = time.monotonic() - started
        ModelRun.objects.filter(pk=model_run.pk).update(
            status=ModelRun.Status.FAILED,
            run_slot=None,
            finished_at=timezone.now(),
            runtime_seconds=Decimal(str(round(elapsed, 3))),
            error_message=str(exc)[:2000],
        )
        raise
    model_run.refresh_from_db()
    return model_run


def run_model_experiment_isolated(
    series,
    *,
    user,
    through: date | None = None,
    publish_live: bool = True,
    time_limit_seconds: int | None = None,
) -> ModelRun:
    """Run the experiment in a disposable process so scientific memory is released."""

    if not user_can_run_experiments(user):
        raise PermissionDenied("Only a research administrator can run model experiments.")
    if ModelRun.objects.filter(status=ModelRun.Status.RUNNING).exists():
        raise ExperimentAlreadyRunningError("Another model experiment is already running.")

    limit = time_limit_seconds
    if limit is None:
        limit = 180 if getattr(settings, "IS_LITE", False) else 60
    management_arguments = [
        "run_forecast",
        series.slug,
        "--requested-by",
        str(user.pk),
        "--time-limit",
        str(limit),
    ]
    if through:
        management_arguments.extend(("--through", through.isoformat()))
    if not publish_live:
        management_arguments.append("--simulation")
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--forecast-worker", *management_arguments]
    else:
        command = [
            sys.executable,
            str(settings.BASE_DIR / "manage.py"),
            *management_arguments,
        ]

    worker_environment = os.environ.copy()
    for variable in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        worker_environment[variable] = "1"

    started_at = timezone.now()
    # In particular, release a SQLite handle inherited conceptually from the
    # request path before the worker opens its own short-lived connection.
    connections.close_all()
    try:
        completed = subprocess.run(
            command,
            cwd=settings.BASE_DIR,
            env=worker_environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=limit + 20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        ModelRun.objects.filter(
            snapshot__series=series,
            status=ModelRun.Status.RUNNING,
            created_at__gte=started_at,
        ).update(
            status=ModelRun.Status.FAILED,
            run_slot=None,
            finished_at=timezone.now(),
            error_message=f"Forecast worker exceeded its {limit}-second budget and was stopped.",
        )
        raise ExperimentWorkerError(
            f"Forecast worker exceeded its {limit}-second budget and was stopped."
        ) from exc

    match = re.search(r"GABAY_MODEL_RUN_ID=(\d+)", completed.stdout)
    if completed.returncode != 0 or match is None:
        detail = (completed.stderr or completed.stdout or "Forecast worker failed.").strip()
        if "GABAY_EXPERIMENT_BUSY" in detail:
            raise ExperimentAlreadyRunningError("Another model experiment is already running.")
        raise ExperimentWorkerError(detail[-1500:])
    try:
        return ModelRun.objects.get(pk=int(match.group(1)))
    except ModelRun.DoesNotExist as exc:
        raise ExperimentWorkerError("Forecast worker completed without a persisted run.") from exc


def reconcile_forecast(
    forecast: Forecast,
    *,
    user=None,
    enforce_permission: bool = True,
) -> tuple[Forecast, bool]:
    """Attach the verified target-week median and immutable error measures."""

    if enforce_permission and not user_can_run_experiments(user):
        raise PermissionDenied("Only a research administrator can reconcile forecasts.")
    from prices.services import get_weekly_series

    rows = get_weekly_series(
        forecast.series,
        through=forecast.target_week + timedelta(days=6),
        public_only=forecast.kind == Forecast.Kind.LIVE,
    )
    target_row = next(
        (
            row
            for row in rows
            if date.fromisoformat(_iso_date(row["week_start"])) == forecast.target_week
        ),
        None,
    )
    if target_row is None:
        raise ForecastObservationUnavailableError(
            f"No verified target-week observation is available for {forecast.target_week}."
        )

    with transaction.atomic():
        locked = Forecast.objects.select_for_update().get(pk=forecast.pk)
        if locked.observed_value is not None:
            return locked, False
        locked.reconcile(Decimal(str(target_row["price"])))
        return locked, True


def reconcile_due_forecasts(*, series=None, user=None, enforce_permission: bool = True) -> dict:
    if enforce_permission and not user_can_run_experiments(user):
        raise PermissionDenied("Only a research administrator can reconcile forecasts.")
    queryset = Forecast.objects.filter(
        observed_value__isnull=True,
        target_week__lte=timezone.localdate(),
    ).order_by("target_week", "pk")
    if series is not None:
        queryset = queryset.filter(series=series)
    reconciled = 0
    pending = 0
    for forecast in queryset:
        try:
            _forecast, changed = reconcile_forecast(
                forecast,
                user=user,
                enforce_permission=False,
            )
        except ForecastObservationUnavailableError:
            pending += 1
        else:
            reconciled += int(changed)
    return {"reconciled": reconciled, "pending": pending}


def _choose_public_series():
    from prices.services import get_public_price_context

    price_context = get_public_price_context(history_weeks=104)
    selected = price_context.get("series")
    if selected is not None:
        return selected

    from prices.models import PriceSeries

    queryset = PriceSeries.objects.filter(
        is_active=True,
        is_public=True,
        is_primary_public=True,
    )
    local = queryset.filter(scope=PriceSeries.Scope.LOCAL).order_by("id").first()
    if local or getattr(settings, "IS_PRODUCTION", False):
        return local
    return queryset.filter(scope=PriceSeries.Scope.DEMO).order_by("id").first()


def _insufficient_context(message: str, message_tl: str | None = None) -> dict[str, Any]:
    messages = {
        "en": message,
        "tl": message_tl or "Kulang pa ang beripikadong ebidensya para sa direksiyonal na payo.",
    }
    return {
        "available": False,
        "status": "insufficient",
        "forecast": None,
        "recommendation": None,
        "decision": Recommendation.Decision.MONITOR,
        "reliability": Recommendation.Reliability.UNASSESSED,
        "point_forecast": None,
        "interval_lower": None,
        "interval_upper": None,
        "target_week": None,
        "current_price": None,
        "data_age_days": None,
        "reason_codes": ["INSUFFICIENT_VERIFIED_EVIDENCE"],
        "rule_version": None,
        "rule_status": "UNAVAILABLE",
        "messages": messages,
        "is_demo": False,
        "message": messages["en"],
    }


def get_public_forecast_context(series=None) -> dict[str, Any]:
    """Read the latest locked forecast; this function never fits a model."""

    series = series or _choose_public_series()
    if series is None:
        return _insufficient_context(
            "No eligible verified local price series is available yet.",
            "Wala pang karapat-dapat na beripikadong lokal na serye ng presyo.",
        )
    forecast = (
        Forecast.objects.filter(series=series, kind=Forecast.Kind.LIVE)
        .select_related(
            "recommendation",
            "recommendation__rule_set",
            "model_run__snapshot",
        )
        .order_by("-target_week", "-published_at")
        .first()
    )
    if forecast is None:
        context = _insufficient_context(
            "Monitor — at least 64 verified weekly observations and a completed "
            "experiment are required.",
            "Subaybayan — kailangan ang hindi bababa sa 64 beripikadong lingguhang "
            "obserbasyon at isang natapos na eksperimento.",
        )
        context["is_demo"] = series.scope == "demo"
        return context

    recommendation = forecast.recommendation
    age = max(0, (timezone.localdate() - recommendation.data_as_of).days)
    decision = recommendation.decision
    reliability = recommendation.reliability
    reasons = list(recommendation.reason_codes)
    messages = {
        "en": recommendation.explanations.get("en", ""),
        "tl": recommendation.explanations.get("tl", ""),
    }
    if recommendation.rule_set.status != DecisionRuleSet.Status.FROZEN:
        decision = Recommendation.Decision.MONITOR
        reliability = Recommendation.Reliability.UNASSESSED
        if "RULE_PROTOCOL_UNAPPROVED" not in reasons:
            reasons.insert(0, "RULE_PROTOCOL_UNAPPROVED")
        messages = {
            "en": (
                "Monitor — the decision protocol is awaiting adviser approval; "
                "the numeric forecast is not directional advice."
            ),
            "tl": (
                "Subaybayan — hinihintay pa ang pag-apruba ng tagapayo sa tuntunin; "
                "ang numerong taya ay hindi direksiyonal na payo."
            ),
        }
    current_week = timezone.localdate() - timedelta(days=timezone.localdate().weekday())
    if forecast.target_week not in {current_week, current_week + timedelta(weeks=1)}:
        decision = Recommendation.Decision.MONITOR
        if "TARGET_WEEK_MISMATCH" not in reasons:
            reasons.insert(0, "TARGET_WEEK_MISMATCH")
        messages = {
            "en": "Monitor — this forecast is not for the current or upcoming ISO week.",
            "tl": (
                "Subaybayan — ang tayang ito ay hindi para sa kasalukuyan o susunod "
                "na linggong ISO."
            ),
        }
    try:
        from prices.services import get_weekly_series

        current_rows = _canonical_rows(
            get_weekly_series(series, public_only=True)
        )
        current_fingerprint = _dataset_fingerprint(
            series,
            current_rows,
            public_inputs_only=True,
        )
    except Exception:  # noqa: BLE001 - a failed evidence check must fail closed
        current_fingerprint = ""
    if current_fingerprint != forecast.dataset_fingerprint:
        decision = Recommendation.Decision.MONITOR
        if "CURRENT_EVIDENCE_CHANGED" not in reasons:
            reasons.insert(0, "CURRENT_EVIDENCE_CHANGED")
        messages = {
            "en": (
                "Monitor — verified public evidence changed after this forecast was locked; "
                "run a new experiment."
            ),
            "tl": (
                "Subaybayan — nagbago ang beripikadong pampublikong ebidensya matapos "
                "i-lock ang taya; magpatakbo ng bagong eksperimento."
            ),
        }
    if age > recommendation.rule_set.stale_after_days:
        decision = Recommendation.Decision.MONITOR
        if "STALE_DATA" not in reasons:
            reasons.insert(0, "STALE_DATA")
        messages = {
            "en": (
                "Monitor — the latest verified price is stale; refresh the evidence "
                "before deciding."
            ),
            "tl": (
                "Subaybayan — luma na ang huling beripikadong presyo; i-update ang "
                "ebidensya bago magpasya."
            ),
        }
    if recommendation.is_demo:
        messages = {
            "en": "Demonstration data—not verified market information. " + messages["en"],
            "tl": (
                "Pang-demonstrasyong datos—hindi beripikadong impormasyon sa merkado. "
                + messages["tl"]
            ),
        }

    return {
        "available": True,
        "status": "available",
        "forecast": forecast,
        "recommendation": recommendation,
        "decision": decision,
        "reliability": reliability,
        "point_forecast": forecast.point_forecast,
        "interval_lower": forecast.interval_lower,
        "interval_upper": forecast.interval_upper,
        "target_week": forecast.target_week,
        "current_price": recommendation.current_price,
        "data_age_days": age,
        "reason_codes": reasons,
        "rule_version": recommendation.rule_set.version,
        "rule_status": recommendation.rule_set.status,
        "messages": messages,
        "is_demo": recommendation.is_demo,
        "message": messages["en"],
    }


def evaluate_offer(offer_price: Decimal | str, series=None) -> dict[str, Any]:
    """Evaluate an offer in memory. The entered value is never persisted."""

    try:
        offer = Decimal(str(offer_price))
    except Exception as exc:  # noqa: BLE001 - form/service boundary
        raise ValueError("Enter a valid offer price.") from exc
    if not offer.is_finite() or offer <= 0:
        raise ValueError("Offer price must be greater than zero.")

    context = get_public_forecast_context(series)
    if not context["available"]:
        return {**context, "offer_price": offer}

    forecast: Forecast = context["forecast"]
    recommendation: Recommendation = context["recommendation"]
    snapshot_rows = forecast.model_run.snapshot.weekly_values
    historical = [Decimal(item["price"]) for item in snapshot_rows]
    scores = forecast.model_run.aggregate_metrics[forecast.model_name]
    result: DecisionResult = evaluate_decision(
        current_price=offer,
        historical_prices=historical,
        point_forecast=forecast.point_forecast,
        interval_lower=forecast.interval_lower,
        interval_upper=forecast.interval_upper,
        mae=Decimal(scores["mae"]),
        coverage_ratio=forecast.model_run.snapshot.coverage_ratio,
        data_as_of=recommendation.data_as_of,
        rules=recommendation.rule_set,
    )
    decision = result.decision
    reliability = result.reliability
    reason_codes = list(result.reason_codes)
    messages = dict(result.explanations)
    validity_gates = {
        "STALE_DATA",
        "CURRENT_EVIDENCE_CHANGED",
        "TARGET_WEEK_MISMATCH",
    }
    context_gate_codes = [
        code for code in context["reason_codes"] if code in validity_gates
    ]
    messages_already_demo_labeled = False
    if context_gate_codes:
        decision = Recommendation.Decision.MONITOR
        reliability = Recommendation.Reliability.UNASSESSED
        reason_codes = list(
            dict.fromkeys([*context_gate_codes, *reason_codes])
        )
        messages = dict(context["messages"])
        messages_already_demo_labeled = recommendation.is_demo
    if recommendation.rule_set.status != DecisionRuleSet.Status.FROZEN:
        decision = Recommendation.Decision.MONITOR
        reliability = Recommendation.Reliability.UNASSESSED
        reason_codes = list(
            dict.fromkeys(["RULE_PROTOCOL_UNAPPROVED", *reason_codes])
        )
        messages = {
            "en": (
                "Monitor — the decision protocol is awaiting adviser approval; "
                "this offer receives no directional advice."
            ),
            "tl": (
                "Subaybayan — hinihintay pa ang pag-apruba ng tagapayo sa tuntunin; "
                "walang direksiyonal na payo para sa alok na ito."
            ),
        }
        messages_already_demo_labeled = False
    if recommendation.is_demo and not messages_already_demo_labeled:
        messages = {
            "en": "Demonstration data—not verified market information. " + messages["en"],
            "tl": (
                "Pang-demonstrasyong datos—hindi beripikadong impormasyon sa merkado. "
                + messages["tl"]
            ),
        }
    return {
        **context,
        "available": True,
        "decision": decision,
        "reliability": reliability,
        "current_price": offer,
        "offer_price": offer,
        "data_age_days": result.data_age_days,
        "reason_codes": reason_codes,
        "messages": messages,
        "message": messages["en"],
    }
