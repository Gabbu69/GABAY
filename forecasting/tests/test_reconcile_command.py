from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from forecasting.services import ForecastObservationUnavailableError

pytestmark = pytest.mark.django_db


@patch("forecasting.management.commands.reconcile_forecasts.reconcile_due_forecasts")
def test_reconcile_command_reports_due_summary(mock_reconcile):
    mock_reconcile.return_value = {"reconciled": 2, "pending": 1}
    output = StringIO()
    call_command("reconcile_forecasts", stdout=output)
    assert "Reconciled 2 forecast(s); 1 still await" in output.getvalue()


def test_reconcile_command_rejects_conflicting_filters():
    with pytest.raises(CommandError, match="either"):
        call_command("reconcile_forecasts", forecast_id=1, series="demo")


def test_reconcile_command_rejects_unknown_series():
    with pytest.raises(CommandError, match="No active price series"):
        call_command("reconcile_forecasts", series="missing")


@patch("forecasting.management.commands.reconcile_forecasts.reconcile_forecast")
@patch("forecasting.management.commands.reconcile_forecasts.Forecast.objects.get")
def test_reconcile_command_reports_one_forecast(mock_get, mock_reconcile):
    forecast = SimpleNamespace(
        pk=8,
        observed_value=Decimal("20.5000"),
        absolute_error=Decimal("0.5000"),
    )
    mock_get.return_value = forecast
    mock_reconcile.return_value = (forecast, True)
    output = StringIO()
    call_command("reconcile_forecasts", forecast_id=8, stdout=output)
    assert "Forecast 8 reconciled" in output.getvalue()
    assert "absolute error 0.5000" in output.getvalue()


@patch("forecasting.management.commands.reconcile_forecasts.reconcile_forecast")
@patch("forecasting.management.commands.reconcile_forecasts.Forecast.objects.get")
def test_reconcile_command_reports_missing_target_evidence(mock_get, mock_reconcile):
    mock_get.return_value = SimpleNamespace(pk=9)
    mock_reconcile.side_effect = ForecastObservationUnavailableError("No verified target-week")
    with pytest.raises(CommandError, match="No verified target-week"):
        call_command("reconcile_forecasts", forecast_id=9)
