from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.urls import reverse

from forecasting.models import DatasetSnapshot, DecisionRuleSet, FoldMetric, ModelRun
from forecasting.services import ExperimentAlreadyRunningError
from prices.models import PriceSeries

pytestmark = pytest.mark.django_db


def make_research_user(username="research-view"):
    user = get_user_model().objects.create_user(username=username, password="safe-pass")
    user.groups.add(Group.objects.get_or_create(name="research_admin")[0])
    return user


def make_series():
    return PriceSeries.objects.create(
        name="Research view series",
        slug="research-view-series",
        location_name="M'lang",
        location_level=PriceSeries.LocationLevel.MUNICIPALITY,
        classification=PriceSeries.Classification.OTHER,
        condition=PriceSeries.Condition.DRY,
        scope=PriceSeries.Scope.DEMO,
    )


def test_research_dashboard_requires_login(client):
    response = client.get(reverse("forecasting:dashboard"))
    assert response.status_code == 302
    assert reverse("portal:login") in response.url


def test_research_dashboard_rejects_nonresearch_user(client):
    user = get_user_model().objects.create_user(username="encoder-view", password="safe-pass")
    client.force_login(user)
    response = client.get(reverse("forecasting:dashboard"))
    assert response.status_code == 403


def test_research_group_can_view_dashboard(client):
    user = make_research_user()
    client.force_login(user)
    response = client.get(reverse("forecasting:dashboard"))
    assert response.status_code == 200
    assert b"Forecast laboratory" in response.content
    assert b"DRAFT-v1" in response.content


def test_dashboard_renders_selected_metrics_and_retained_fold_evidence(client):
    series = make_series()
    snapshot = DatasetSnapshot.objects.create(
        series=series,
        fingerprint="d" * 64,
        cutoff_date=date(2026, 8, 17),
        first_week=date(2025, 6, 2),
        last_week=date(2026, 8, 17),
        observed_week_count=64,
        span_week_count=64,
        coverage_ratio=Decimal("1"),
        weekly_values=[],
    )
    run = ModelRun.objects.create(
        snapshot=snapshot,
        status=ModelRun.Status.SUCCEEDED,
        selected_model="naive",
        aggregate_metrics={
            "naive": {
                "available": True,
                "mae": "0.2000",
                "rmse": "0.2500",
                "mape": "1.1000",
                "folds": 12,
            }
        },
    )
    FoldMetric.objects.create(
        model_run=run,
        model_name="naive",
        fold_index=1,
        train_through=date(2026, 5, 25),
        target_week=date(2026, 6, 1),
        actual=Decimal("20"),
        predicted=Decimal("19.8"),
        absolute_error=Decimal("0.2"),
        squared_error=Decimal("0.04"),
        absolute_percentage_error=Decimal("1"),
    )
    client.force_login(make_research_user())
    response = client.get(reverse("forecasting:dashboard"))
    assert b"MAE 0.2000" in response.content
    assert b"RMSE 0.2500" in response.content
    assert b"MAPE 1.1000%" in response.content
    assert b"12 held-out folds" in response.content


def test_experiment_get_redirects_to_dashboard(client):
    client.force_login(make_research_user())
    response = client.get(reverse("forecasting:run-experiment"))
    assert response.status_code == 302
    assert response.url == reverse("forecasting:dashboard")


def test_invalid_experiment_form_returns_400_with_bound_form(client):
    client.force_login(make_research_user())
    response = client.post(reverse("forecasting:run-experiment"), {})
    assert response.status_code == 400
    assert b"This field is required" in response.content


@patch("forecasting.views.run_model_experiment_isolated")
def test_valid_experiment_reports_live_success(mock_run, client):
    series = make_series()
    client.force_login(make_research_user())
    mock_run.return_value = SimpleNamespace(pk=17, is_live=True, selected_model="naive")
    response = client.post(
        reverse("forecasting:run-experiment"),
        {"series": series.pk, "publish_live": "on"},
        follow=True,
    )
    assert response.status_code == 200
    assert b"Experiment 17 completed with naive" in response.content
    mock_run.assert_called_once()


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (ExperimentAlreadyRunningError("one at a time"), b"one at a time"),
        (RuntimeError("worker stopped"), b"Experiment failed safely: worker stopped"),
    ],
)
@patch("forecasting.views.run_model_experiment_isolated")
def test_experiment_failures_return_safely_to_dashboard(
    mock_run, failure, expected, client
):
    series = make_series()
    client.force_login(make_research_user(f"research-{type(failure).__name__}"))
    mock_run.side_effect = failure
    response = client.post(
        reverse("forecasting:run-experiment"),
        {"series": series.pk, "publish_live": "on"},
        follow=True,
    )
    assert response.status_code == 200
    assert expected in response.content


def test_freeze_get_redirects_and_nonprivileged_research_user_is_forbidden(client):
    client.force_login(make_research_user())
    assert client.get(reverse("forecasting:freeze-rule-set")).status_code == 302
    response = client.post(
        reverse("forecasting:freeze-rule-set"),
        {
            "rule_set_id": DecisionRuleSet.objects.get(version="DRAFT-v1").pk,
            "confirmation": "DRAFT-v1",
        },
    )
    assert response.status_code == 403


def test_freeze_form_and_confirmation_are_validated(client):
    user = make_research_user()
    user.user_permissions.add(Permission.objects.get(codename="freeze_decision_rules"))
    client.force_login(user)
    url = reverse("forecasting:freeze-rule-set")
    invalid = client.post(url, {}, follow=True)
    assert b"Rule-set confirmation was invalid" in invalid.content
    rules = DecisionRuleSet.objects.get(version="DRAFT-v1")
    mismatch = client.post(
        url,
        {"rule_set_id": rules.pk, "confirmation": "wrong-version"},
        follow=True,
    )
    assert b"confirmation must exactly match" in mismatch.content


def test_authorized_freeze_succeeds_and_second_attempt_fails_safely(client):
    user = make_research_user()
    user.user_permissions.add(Permission.objects.get(codename="freeze_decision_rules"))
    client.force_login(user)
    rules = DecisionRuleSet.objects.get(version="DRAFT-v1")
    payload = {"rule_set_id": rules.pk, "confirmation": rules.version}
    response = client.post(reverse("forecasting:freeze-rule-set"), payload, follow=True)
    assert b"now frozen and immutable" in response.content
    rules.refresh_from_db()
    assert rules.status == DecisionRuleSet.Status.FROZEN
    response = client.post(reverse("forecasting:freeze-rule-set"), payload, follow=True)
    assert b"Only a draft rule set can be frozen" in response.content
