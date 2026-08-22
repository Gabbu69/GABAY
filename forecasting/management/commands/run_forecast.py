from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from forecasting.services import ExperimentAlreadyRunningError, run_model_experiment
from prices.models import PriceSeries


class Command(BaseCommand):
    help = "Run the bounded GABAY model comparison for one compatible price series."

    def add_arguments(self, parser) -> None:
        parser.add_argument("series_slug", help="PriceSeries slug")
        parser.add_argument("--through", help="Optional cutoff date (YYYY-MM-DD)")
        parser.add_argument(
            "--simulation",
            action="store_true",
            help="Always write a retrospective simulation instead of a public live forecast.",
        )
        parser.add_argument("--time-limit", type=int, help="Override experiment budget in seconds")
        parser.add_argument("--requested-by", type=int, help="Authenticated research user ID")

    def handle(self, *args, **options):
        user_model = get_user_model()
        try:
            series = PriceSeries.objects.get(slug=options["series_slug"], is_active=True)
        except PriceSeries.DoesNotExist as exc:
            raise CommandError("No active price series matches that slug.") from exc
        try:
            through = date.fromisoformat(options["through"]) if options["through"] else None
        except ValueError as exc:
            raise CommandError("--through must use YYYY-MM-DD.") from exc
        try:
            user = None
            if options["requested_by"]:
                user = user_model.objects.get(pk=options["requested_by"])
            run = run_model_experiment(
                series,
                user=user,
                through=through,
                publish_live=not options["simulation"],
                enforce_permission=user is not None,
                time_limit_seconds=options["time_limit"],
            )
        except user_model.DoesNotExist as exc:
            raise CommandError("The requesting research user no longer exists.") from exc
        except ExperimentAlreadyRunningError as exc:
            raise CommandError(f"GABAY_EXPERIMENT_BUSY: {exc}") from exc
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"GABAY_MODEL_RUN_ID={run.pk}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Run {run.pk}: {run.selected_model}, forecast PHP {run.point_forecast}/kg "
                f"for {run.target_week} ({'live' if run.is_live else 'simulation'})."
            )
        )
