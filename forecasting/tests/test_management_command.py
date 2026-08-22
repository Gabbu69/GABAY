from datetime import date
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from forecasting.services import ExperimentAlreadyRunningError
from prices.models import PriceSeries

pytestmark = pytest.mark.django_db


def make_series():
    return PriceSeries.objects.create(
        name="Command series",
        slug="command-series",
        location_name="M'lang",
        location_level=PriceSeries.LocationLevel.MUNICIPALITY,
        classification=PriceSeries.Classification.OTHER,
        condition=PriceSeries.Condition.DRY,
        scope=PriceSeries.Scope.DEMO,
    )


@patch("forecasting.management.commands.run_forecast.run_model_experiment")
def test_command_reports_persisted_run_id_and_forwards_protocol_options(mock_run):
    series = make_series()
    user = get_user_model().objects.create_user(username="command-researcher")
    mock_run.return_value = SimpleNamespace(
        pk=42,
        selected_model="naive",
        point_forecast=Decimal("20.2500"),
        target_week=date(2026, 8, 24),
        is_live=False,
    )
    output = StringIO()
    call_command(
        "run_forecast",
        series.slug,
        through="2026-08-17",
        simulation=True,
        requested_by=user.pk,
        time_limit=45,
        stdout=output,
    )
    assert "GABAY_MODEL_RUN_ID=42" in output.getvalue()
    assert "simulation" in output.getvalue()
    mock_run.assert_called_once_with(
        series,
        user=user,
        through=date(2026, 8, 17),
        publish_live=False,
        enforce_permission=True,
        time_limit_seconds=45,
    )


def test_command_rejects_unknown_series():
    with pytest.raises(CommandError, match="No active price series"):
        call_command("run_forecast", "missing-series")


def test_command_rejects_invalid_cutoff():
    series = make_series()
    with pytest.raises(CommandError, match="YYYY-MM-DD"):
        call_command("run_forecast", series.slug, through="17/08/2026")


def test_command_rejects_missing_requesting_user():
    series = make_series()
    with pytest.raises(CommandError, match="no longer exists"):
        call_command("run_forecast", series.slug, requested_by=999999)


@patch("forecasting.management.commands.run_forecast.run_model_experiment")
def test_command_converts_worker_failure_to_command_error(mock_run):
    series = make_series()
    mock_run.side_effect = RuntimeError("bounded model fit failed")
    with pytest.raises(CommandError, match="bounded model fit failed"):
        call_command("run_forecast", series.slug)


@patch("forecasting.management.commands.run_forecast.run_model_experiment")
def test_command_marks_database_slot_collision_as_busy(mock_run):
    series = make_series()
    mock_run.side_effect = ExperimentAlreadyRunningError("another run")
    with pytest.raises(CommandError, match="GABAY_EXPERIMENT_BUSY"):
        call_command("run_forecast", series.slug)
