import sys
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.utils import timezone

from forecasting.engine import ForecastingError, InsufficientDataError
from forecasting.models import (
    DatasetSnapshot,
    DecisionRuleSet,
    FoldMetric,
    Forecast,
    ModelRun,
    Recommendation,
)
from forecasting.services import (
    ExperimentAlreadyRunningError,
    ForecastObservationUnavailableError,
    evaluate_offer,
    get_public_forecast_context,
    reconcile_forecast,
    run_model_experiment,
    run_model_experiment_isolated,
)
from prices.models import PriceSeries

pytestmark = pytest.mark.django_db


def make_series():
    return PriceSeries.objects.create(
        name="M'lang demonstration",
        slug="mlang-demo-forecast-service-test",
        location_name="M'lang",
        location_level=PriceSeries.LocationLevel.MUNICIPALITY,
        classification=PriceSeries.Classification.OTHER,
        condition=PriceSeries.Condition.DRY,
        scope=PriceSeries.Scope.DEMO,
        is_primary_public=True,
    )


def current_rows():
    last_week = timezone.localdate() - timedelta(days=timezone.localdate().weekday())
    first_week = last_week - timedelta(weeks=63)
    return [
        {
            "week_start": first_week + timedelta(weeks=index),
            "price": Decimal("19") + Decimal(index) / Decimal("100"),
            "low": Decimal("18.8") + Decimal(index) / Decimal("100"),
            "high": Decimal("19.2") + Decimal(index) / Decimal("100"),
            "count": 3,
            "observed_on": min(
                first_week + timedelta(weeks=index, days=5), timezone.localdate()
            ),
            "scope": "demo",
            "is_demo": True,
        }
        for index in range(64)
    ]


@patch("prices.services.get_weekly_series")
def test_successful_run_persists_reproducible_ledger(mock_weekly):
    mock_weekly.return_value = current_rows()
    series = make_series()
    run = run_model_experiment(series, enforce_permission=False)
    assert run.status == ModelRun.Status.SUCCEEDED
    assert run.snapshot.fingerprint
    assert run.snapshot.public_inputs_only is True
    assert run.snapshot.preprocessing_report["publication_filter"] == (
        "publication-approved sources only"
    )
    assert run.aggregate_metrics[run.selected_model]["folds"] == 12
    assert FoldMetric.objects.filter(model_run=run, model_name=run.selected_model).count() == 12
    assert run.forecast.kind == Forecast.Kind.LIVE
    assert run.forecast.recommendation.is_demo is True
    assert run.forecast.recommendation.decision == Recommendation.Decision.MONITOR
    assert run.forecast.recommendation.reliability == Recommendation.Reliability.UNASSESSED
    assert "RULE_PROTOCOL_UNAPPROVED" in run.forecast.recommendation.reason_codes
    evidence_objects = [
        run.snapshot,
        run,
        run.fold_metrics.first(),
        run.forecast,
        run.forecast.recommendation,
    ]
    for evidence in evidence_objects:
        with pytest.raises(ValidationError, match="append-only"):
            evidence.delete()
        with pytest.raises(ValidationError, match="append-only"):
            type(evidence).objects.filter(pk=evidence.pk).delete()
    run.forecast.recommendation.decision = Recommendation.Decision.WAIT
    with pytest.raises(ValidationError, match="immutable"):
        run.forecast.recommendation.save()

    context = get_public_forecast_context(series)
    assert context["available"] is True
    assert context["is_demo"] is True
    assert context["decision"] == Recommendation.Decision.MONITOR
    assert context["reliability"] == Recommendation.Reliability.UNASSESSED
    assert "RULE_PROTOCOL_UNAPPROVED" in context["reason_codes"]
    assert context["rule_version"] == "DRAFT-v1"
    assert context["rule_status"] == DecisionRuleSet.Status.DRAFT
    assert set(context["messages"]) == {"en", "tl"}
    assert context["message"].startswith("Demonstration data")
    mock_weekly.assert_any_call(series, through=None, public_only=True)


@patch("prices.services.get_weekly_series")
def test_offer_evaluation_is_stateless(mock_weekly):
    mock_weekly.return_value = current_rows()
    series = make_series()
    run_model_experiment(series, enforce_permission=False)
    before = Recommendation.objects.count()
    result = evaluate_offer("19.25", series)
    assert result["offer_price"] == Decimal("19.25")
    assert result["decision"] == "MONITOR"
    assert result["reliability"] == "UNASSESSED"
    assert "RULE_PROTOCOL_UNAPPROVED" in result["reason_codes"]
    assert result["rule_version"] == "DRAFT-v1"
    assert set(result["messages"]) == {"en", "tl"}
    assert Recommendation.objects.count() == before


@patch("prices.services.get_weekly_series")
def test_global_single_run_guard(mock_weekly):
    mock_weekly.return_value = current_rows()
    series = make_series()
    first = run_model_experiment(series, enforce_permission=False, publish_live=False)
    assert first.snapshot.public_inputs_only is False
    assert "RULE_PROTOCOL_UNAPPROVED" not in first.forecast.recommendation.reason_codes
    ModelRun.objects.create(
        snapshot=first.snapshot,
        status=ModelRun.Status.RUNNING,
        run_slot=1,
    )
    with pytest.raises(ExperimentAlreadyRunningError):
        run_model_experiment(series, enforce_permission=False)


@patch("prices.services.get_weekly_series")
def test_failure_is_persisted_without_replacing_last_valid_forecast(mock_weekly):
    series = make_series()
    mock_weekly.return_value = current_rows()
    valid = run_model_experiment(series, enforce_permission=False)
    mock_weekly.return_value = current_rows()[:20]
    with pytest.raises(InsufficientDataError):
        run_model_experiment(series, enforce_permission=False)
    failed = ModelRun.objects.get(status=ModelRun.Status.FAILED)
    assert failed.run_slot is None
    assert get_public_forecast_context(series)["forecast"].pk == valid.forecast.pk


@patch("prices.services.get_weekly_series")
def test_permission_is_required_for_interactive_execution(mock_weekly):
    mock_weekly.return_value = current_rows()
    user = get_user_model().objects.create_user(username="encoder", password="test-password")
    with pytest.raises(PermissionDenied):
        run_model_experiment(make_series(), user=user)


@patch("prices.services.get_weekly_series")
def test_live_publication_requires_explicit_primary_series(mock_weekly):
    mock_weekly.return_value = current_rows()
    series = make_series()
    series.is_primary_public = False
    series.save(update_fields=("is_primary_public",))
    with pytest.raises(ForecastingError, match="primary public series"):
        run_model_experiment(series, enforce_permission=False)
    simulation = run_model_experiment(
        series,
        enforce_permission=False,
        publish_live=False,
    )
    assert simulation.forecast.kind == Forecast.Kind.SIMULATION
    mock_weekly.assert_called_with(series, through=None, public_only=False)


def test_missing_forecast_returns_honest_monitor_state():
    series = make_series()
    context = get_public_forecast_context(series)
    assert context["available"] is False
    assert context["decision"] == "MONITOR"
    assert context["reliability"] == "UNASSESSED"
    assert context["rule_version"] is None
    assert context["rule_status"] == "UNAVAILABLE"
    assert set(context["messages"]) == {"en", "tl"}
    offer = evaluate_offer("20", series)
    assert offer["rule_version"] is None
    assert offer["rule_status"] == "UNAVAILABLE"
    assert set(offer["messages"]) == {"en", "tl"}


@patch("forecasting.services.connections.close_all")
@patch("forecasting.services.subprocess.run")
def test_isolated_runner_returns_child_process_result(mock_worker, _mock_close):
    series = make_series()
    user = get_user_model().objects.create_user(username="research-worker", password="safe-pass")
    user.groups.add(Group.objects.create(name="research_admin"))
    snapshot = DatasetSnapshot.objects.create(
        series=series,
        fingerprint="b" * 64,
        cutoff_date=timezone.localdate(),
        first_week=timezone.localdate() - timedelta(weeks=63),
        last_week=timezone.localdate(),
        observed_week_count=64,
        span_week_count=64,
        coverage_ratio=Decimal("1"),
        weekly_values=[],
    )
    child_run = ModelRun.objects.create(snapshot=snapshot, status=ModelRun.Status.SUCCEEDED)
    mock_worker.return_value = SimpleNamespace(
        returncode=0,
        stdout=f"GABAY_MODEL_RUN_ID={child_run.pk}\n",
        stderr="",
    )
    returned = run_model_experiment_isolated(series, user=user)
    assert returned.pk == child_run.pk
    command = mock_worker.call_args.args[0]
    assert "run_forecast" in command
    assert "--requested-by" in command


@patch("forecasting.services.connections.close_all")
@patch("forecasting.services.subprocess.run")
def test_isolated_runner_maps_child_slot_collision_to_busy(mock_worker, _mock_close):
    series = make_series()
    user = get_user_model().objects.create_user(username="busy-worker", password="safe-pass")
    user.groups.add(Group.objects.create(name="research_admin"))
    mock_worker.return_value = SimpleNamespace(
        returncode=1,
        stdout="",
        stderr="CommandError: GABAY_EXPERIMENT_BUSY: another run",
    )
    with pytest.raises(ExperimentAlreadyRunningError):
        run_model_experiment_isolated(series, user=user)


@patch("forecasting.services.connections.close_all")
@patch("forecasting.services.subprocess.run")
@patch("forecasting.services.sys.frozen", True, create=True)
def test_frozen_isolated_runner_uses_packaged_worker_dispatch(mock_worker, _mock_close):
    series = make_series()
    user = get_user_model().objects.create_user(username="frozen-worker", password="safe-pass")
    user.groups.add(Group.objects.create(name="research_admin"))
    snapshot = DatasetSnapshot.objects.create(
        series=series,
        fingerprint="c" * 64,
        cutoff_date=timezone.localdate(),
        first_week=timezone.localdate() - timedelta(weeks=63),
        last_week=timezone.localdate(),
        observed_week_count=64,
        span_week_count=64,
        coverage_ratio=Decimal("1"),
        weekly_values=[],
    )
    child_run = ModelRun.objects.create(snapshot=snapshot, status=ModelRun.Status.SUCCEEDED)
    mock_worker.return_value = SimpleNamespace(
        returncode=0,
        stdout=f"GABAY_MODEL_RUN_ID={child_run.pk}\n",
        stderr="",
    )
    run_model_experiment_isolated(series, user=user)
    command = mock_worker.call_args.args[0]
    assert command[:2] == [sys.executable, "--forecast-worker"]
    assert command[2] == "run_forecast"


def test_real_demo_fixture_flows_through_price_service_and_forecast_ledger():
    call_command("seed_demo_prices", stdout=StringIO())
    series = PriceSeries.objects.get(slug="demo-mlang-palay-other-dry")
    with patch("forecasting.services.timezone.localdate", return_value=date(2026, 8, 22)):
        run = run_model_experiment(series, enforce_permission=False)
        context = get_public_forecast_context(series)
    assert run.status == ModelRun.Status.SUCCEEDED
    assert run.snapshot.observed_week_count == 104
    assert run.snapshot.cutoff_date.isoformat() == "2026-08-17"
    assert run.target_week.isoformat() == "2026-08-24"
    assert run.forecast.kind == Forecast.Kind.LIVE
    assert run.forecast.recommendation.is_demo is True
    assert "CURRENT_EVIDENCE_CHANGED" not in context["reason_codes"]


@patch("prices.services.get_weekly_series")
def test_frozen_rules_allow_assessed_live_recommendation(mock_weekly):
    mock_weekly.return_value = current_rows()
    rules = DecisionRuleSet.objects.get(version="DRAFT-v1")
    rules.freeze()
    series = make_series()
    run = run_model_experiment(series, enforce_permission=False)
    recommendation = run.forecast.recommendation
    assert recommendation.rule_set == rules
    assert recommendation.reliability != Recommendation.Reliability.UNASSESSED
    assert "RULE_PROTOCOL_UNAPPROVED" not in recommendation.reason_codes


@patch("prices.services.get_weekly_series")
def test_public_context_fails_closed_when_evidence_fingerprint_changes(mock_weekly):
    rows = current_rows()
    mock_weekly.return_value = rows
    DecisionRuleSet.objects.get(version="DRAFT-v1").freeze()
    series = make_series()
    run_model_experiment(series, enforce_permission=False)
    mock_weekly.return_value = [
        *rows,
        {
            **rows[-1],
            "week_start": rows[-1]["week_start"] + timedelta(weeks=1),
            "observed_on": rows[-1]["observed_on"] + timedelta(days=1),
            "price": Decimal("21.00"),
        },
    ]
    context = get_public_forecast_context(series)
    assert context["decision"] == Recommendation.Decision.MONITOR
    assert "CURRENT_EVIDENCE_CHANGED" in context["reason_codes"]
    offer = evaluate_offer("20.00", series)
    assert offer["decision"] == Recommendation.Decision.MONITOR
    assert offer["reliability"] == Recommendation.Reliability.UNASSESSED
    assert "CURRENT_EVIDENCE_CHANGED" in offer["reason_codes"]


@patch("prices.services.get_weekly_series")
def test_public_context_rejects_forecast_outside_current_or_upcoming_week(mock_weekly):
    mock_weekly.return_value = current_rows()
    DecisionRuleSet.objects.get(version="DRAFT-v1").freeze()
    series = make_series()
    run = run_model_experiment(series, enforce_permission=False)
    with patch(
        "forecasting.services.timezone.localdate",
        return_value=run.target_week + timedelta(weeks=2),
    ):
        context = get_public_forecast_context(series)
        offer = evaluate_offer("20.00", series)
    assert context["decision"] == Recommendation.Decision.MONITOR
    assert "TARGET_WEEK_MISMATCH" in context["reason_codes"]
    assert offer["decision"] == Recommendation.Decision.MONITOR
    assert offer["reliability"] == Recommendation.Reliability.UNASSESSED
    assert "TARGET_WEEK_MISMATCH" in offer["reason_codes"]


@patch("prices.services.get_weekly_series")
def test_offer_preserves_public_stale_data_gate(mock_weekly):
    mock_weekly.return_value = current_rows()
    DecisionRuleSet.objects.get(version="DRAFT-v1").freeze()
    series = make_series()
    run = run_model_experiment(series, enforce_permission=False)
    Recommendation.objects.filter(pk=run.forecast.recommendation.pk).update(
        data_as_of=timezone.localdate() - timedelta(days=15)
    )
    offer = evaluate_offer("20.00", series)
    assert offer["decision"] == Recommendation.Decision.MONITOR
    assert offer["reliability"] == Recommendation.Reliability.UNASSESSED
    assert "STALE_DATA" in offer["reason_codes"]


@patch("prices.services.get_weekly_series")
def test_reconciliation_records_signed_absolute_and_percentage_error_once(mock_weekly):
    rows = current_rows()
    mock_weekly.return_value = rows
    series = make_series()
    run = run_model_experiment(series, enforce_permission=False)
    forecast = run.forecast
    observed = forecast.point_forecast + Decimal("0.5000")
    mock_weekly.return_value = [
        *rows,
        {
            **rows[-1],
            "week_start": forecast.target_week,
            "observed_on": forecast.target_week,
            "price": observed,
        },
    ]
    reconciled, changed = reconcile_forecast(forecast, enforce_permission=False)
    assert changed is True
    assert reconciled.observed_value == observed
    assert reconciled.forecast_error == Decimal("0.5000")
    assert reconciled.absolute_error == Decimal("0.5000")
    assert reconciled.absolute_percentage_error > 0
    reconciled_again, changed = reconcile_forecast(reconciled, enforce_permission=False)
    assert changed is False
    assert reconciled_again.observed_value == observed
    mock_weekly.assert_called_with(
        series,
        through=forecast.target_week + timedelta(days=6),
        public_only=True,
    )


@patch("prices.services.get_weekly_series", return_value=[])
def test_reconciliation_requires_verified_target_week_evidence(_mock_weekly):
    rows = current_rows()
    with patch("prices.services.get_weekly_series", return_value=rows):
        series = make_series()
        forecast = run_model_experiment(series, enforce_permission=False).forecast
    with pytest.raises(ForecastObservationUnavailableError):
        reconcile_forecast(forecast, enforce_permission=False)
