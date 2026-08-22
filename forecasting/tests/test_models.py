from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from forecasting.models import DatasetSnapshot, DecisionRuleSet, Forecast, ModelRun
from prices.models import PriceSeries

pytestmark = pytest.mark.django_db


def make_series():
    return PriceSeries.objects.create(
        name="Demo dry palay",
        slug="demo-dry-palay-model-test",
        location_name="M'lang",
        location_level=PriceSeries.LocationLevel.MUNICIPALITY,
        classification=PriceSeries.Classification.OTHER,
        condition=PriceSeries.Condition.DRY,
        scope=PriceSeries.Scope.DEMO,
    )


def make_run(series):
    snapshot = DatasetSnapshot.objects.create(
        series=series,
        fingerprint="a" * 64,
        cutoff_date=date(2026, 8, 17),
        first_week=date(2025, 6, 2),
        last_week=date(2026, 8, 17),
        observed_week_count=64,
        span_week_count=64,
        coverage_ratio=Decimal("1"),
        weekly_values=[],
    )
    return ModelRun.objects.create(snapshot=snapshot, status=ModelRun.Status.SUCCEEDED)


def test_frozen_rule_thresholds_are_immutable():
    rules = DecisionRuleSet.objects.create(version="test-v1", label="Approved protocol")
    rules.freeze()
    rules.forecast_threshold_pct = Decimal("4")
    with pytest.raises(ValidationError, match="immutable"):
        rules.save()


def test_frozen_rules_cannot_be_reopened_and_retirement_is_terminal():
    rules = DecisionRuleSet.objects.create(version="test-v2", label="Approved protocol")
    rules.freeze()
    rules.status = DecisionRuleSet.Status.DRAFT
    with pytest.raises(ValidationError, match="cannot be reopened"):
        rules.save()

    rules.refresh_from_db()
    rules.status = DecisionRuleSet.Status.RETIRED
    rules.save(update_fields=("status",))
    rules.status = DecisionRuleSet.Status.DRAFT
    with pytest.raises(ValidationError, match="cannot be reopened"):
        rules.save()
    rules.status = DecisionRuleSet.Status.RETIRED
    rules.label = "Changed after retirement"
    with pytest.raises(ValidationError, match="immutable"):
        rules.save()


def test_rule_thresholds_and_coverage_are_bounded_before_freezing():
    rules = DecisionRuleSet(version="invalid-v1", label="Invalid protocol")
    rules.forecast_threshold_pct = Decimal("-0.001")
    with pytest.raises(ValidationError):
        rules.save()
    rules.forecast_threshold_pct = Decimal("3")
    rules.minimum_coverage_ratio = Decimal("1.00001")
    with pytest.raises(ValidationError):
        rules.save()


def test_research_evidence_cannot_be_deleted_by_instance_or_queryset():
    rules = DecisionRuleSet.objects.get(version="DRAFT-v1")
    with pytest.raises(ValidationError, match="append-only"):
        rules.delete()
    with pytest.raises(ValidationError, match="append-only"):
        DecisionRuleSet.objects.filter(pk=rules.pk).delete()


def test_database_allows_only_one_running_model_run():
    series = make_series()
    first = make_run(series)
    running = ModelRun.objects.create(
        snapshot=first.snapshot,
        status=ModelRun.Status.RUNNING,
        run_slot=1,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ModelRun.objects.create(
            snapshot=first.snapshot,
            status=ModelRun.Status.RUNNING,
            run_slot=1,
        )
    running.status = ModelRun.Status.SUCCEEDED
    running.run_slot = None
    running.save(update_fields=("status", "run_slot"))
    assert ModelRun.objects.create(
        snapshot=first.snapshot,
        status=ModelRun.Status.RUNNING,
        run_slot=1,
    )


def test_running_status_and_slot_must_change_together():
    series = make_series()
    run = make_run(series)
    with pytest.raises(IntegrityError), transaction.atomic():
        ModelRun.objects.create(snapshot=run.snapshot, status=ModelRun.Status.RUNNING)
    with pytest.raises(IntegrityError), transaction.atomic():
        ModelRun.objects.create(
            snapshot=run.snapshot,
            status=ModelRun.Status.SUCCEEDED,
            run_slot=1,
        )


def test_forecast_interval_constraint_and_single_reconciliation():
    series = make_series()
    run = make_run(series)
    forecast = Forecast.objects.create(
        model_run=run,
        series=series,
        target_week=date(2026, 8, 24),
        point_forecast=Decimal("20"),
        interval_lower=Decimal("19"),
        interval_upper=Decimal("21"),
        data_cutoff=date(2026, 8, 17),
        dataset_fingerprint="a" * 64,
        model_name="naive",
    )
    stale_copy = Forecast.objects.get(pk=forecast.pk)
    forecast.reconcile(Decimal("20.5"))
    assert forecast.forecast_error == Decimal("0.5")
    assert forecast.absolute_error == Decimal("0.5")
    with pytest.raises(ValidationError, match="already"):
        forecast.reconcile(Decimal("20.6"))
    with pytest.raises(ValidationError, match="already"):
        stale_copy.reconcile(Decimal("20.7"))
    forecast.point_forecast = Decimal("22")
    with pytest.raises(ValidationError, match="immutable"):
        forecast.save()
    forecast.refresh_from_db()
    forecast.observed_value = Decimal("20.7")
    with pytest.raises(ValidationError, match="already"):
        forecast.save()

    run_two = ModelRun.objects.create(snapshot=run.snapshot, status=ModelRun.Status.SUCCEEDED)
    unreconciled = Forecast.objects.create(
        model_run=run_two,
        series=series,
        kind=Forecast.Kind.SIMULATION,
        target_week=date(2026, 8, 31),
        point_forecast=Decimal("20"),
        interval_lower=Decimal("19"),
        interval_upper=Decimal("21"),
        data_cutoff=date(2026, 8, 17),
        dataset_fingerprint="a" * 64,
        model_name="naive",
    )
    unreconciled.observed_value = Decimal("20.1")
    with pytest.raises(ValidationError, match="Use reconcile"):
        unreconciled.save()
    with pytest.raises(IntegrityError), transaction.atomic():
        Forecast.objects.filter(pk=unreconciled.pk).update(observed_value=Decimal("20.1"))
    run_three = ModelRun.objects.create(snapshot=run.snapshot, status=ModelRun.Status.SUCCEEDED)
    with pytest.raises(IntegrityError):
        Forecast.objects.create(
            model_run=run_three,
            series=series,
            target_week=date(2026, 8, 24),
            point_forecast=Decimal("20"),
            interval_lower=Decimal("19"),
            interval_upper=Decimal("21"),
            data_cutoff=date(2026, 8, 17),
            dataset_fingerprint="a" * 64,
            model_name="naive",
        )
