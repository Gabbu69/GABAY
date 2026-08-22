from django.core.management.base import BaseCommand, CommandError

from forecasting.models import Forecast
from forecasting.services import (
    ForecastObservationUnavailableError,
    reconcile_due_forecasts,
    reconcile_forecast,
)
from prices.models import PriceSeries


class Command(BaseCommand):
    help = "Reconcile locked forecasts with verified target-week observations and errors."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--forecast-id", type=int, help="Reconcile one forecast ledger ID")
        parser.add_argument("--series", help="Limit due forecasts to one PriceSeries slug")

    def handle(self, *args, **options):
        if options["forecast_id"] and options["series"]:
            raise CommandError("Use either --forecast-id or --series, not both.")
        if options["forecast_id"]:
            try:
                forecast = Forecast.objects.get(pk=options["forecast_id"])
            except Forecast.DoesNotExist as exc:
                raise CommandError("No forecast matches that ID.") from exc
            try:
                forecast, changed = reconcile_forecast(
                    forecast,
                    enforce_permission=False,
                )
            except ForecastObservationUnavailableError as exc:
                raise CommandError(str(exc)) from exc
            state = "reconciled" if changed else "already reconciled"
            self.stdout.write(
                self.style.SUCCESS(
                    f"Forecast {forecast.pk} {state}: observed PHP "
                    f"{forecast.observed_value}/kg; absolute error {forecast.absolute_error}."
                )
            )
            return

        series = None
        if options["series"]:
            try:
                series = PriceSeries.objects.get(slug=options["series"], is_active=True)
            except PriceSeries.DoesNotExist as exc:
                raise CommandError("No active price series matches that slug.") from exc
        result = reconcile_due_forecasts(series=series, enforce_permission=False)
        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled {result['reconciled']} forecast(s); "
                f"{result['pending']} still await verified target-week evidence."
            )
        )
