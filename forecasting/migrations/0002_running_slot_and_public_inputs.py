import django.core.validators
from decimal import Decimal

from django.db import migrations, models
from django.utils import timezone


def close_pre_guard_running_rows(apps, schema_editor):
    ModelRun = apps.get_model("forecasting", "ModelRun")
    ModelRun.objects.filter(status="RUNNING").update(
        status="FAILED",
        run_slot=None,
        finished_at=timezone.now(),
        error_message=(
            "Run closed while installing the cross-process execution guard; "
            "start a new experiment."
        ),
    )


def backfill_reconciliation_errors(apps, schema_editor):
    Forecast = apps.get_model("forecasting", "Forecast")
    Forecast.objects.filter(observed_value__isnull=True).update(
        observed_at=None,
        forecast_error=None,
        absolute_error=None,
        absolute_percentage_error=None,
    )
    for forecast in Forecast.objects.filter(observed_value__isnull=False).iterator():
        error = forecast.observed_value - forecast.point_forecast
        absolute_error = abs(error)
        percentage_error = (absolute_error / forecast.observed_value) * Decimal(100)
        Forecast.objects.filter(pk=forecast.pk).update(
            observed_at=forecast.observed_at or timezone.now(),
            forecast_error=error,
            absolute_error=absolute_error,
            absolute_percentage_error=percentage_error,
        )


class Migration(migrations.Migration):
    dependencies = [("forecasting", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="datasetsnapshot",
            name="public_inputs_only",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True when the snapshot was filtered to publication-approved source records."
                ),
            ),
        ),
        migrations.AddField(
            model_name="modelrun",
            name="run_slot",
            field=models.PositiveSmallIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="forecast",
            name="absolute_error",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="forecast",
            name="absolute_percentage_error",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="forecast",
            name="forecast_error",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=10, null=True),
        ),
        migrations.AlterField(
            model_name="decisionruleset",
            name="context_threshold_pct",
            field=models.DecimalField(
                decimal_places=3,
                default=Decimal("3.000"),
                max_digits=6,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AlterField(
            model_name="decisionruleset",
            name="forecast_threshold_pct",
            field=models.DecimalField(
                decimal_places=3,
                default=Decimal("3.000"),
                max_digits=6,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AlterField(
            model_name="decisionruleset",
            name="high_reliability_nmae_pct",
            field=models.DecimalField(
                decimal_places=3,
                default=Decimal("5.000"),
                max_digits=6,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AlterField(
            model_name="decisionruleset",
            name="maximum_interval_half_width_pct",
            field=models.DecimalField(
                decimal_places=3,
                default=Decimal("10.000"),
                max_digits=6,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AlterField(
            model_name="decisionruleset",
            name="medium_reliability_nmae_pct",
            field=models.DecimalField(
                decimal_places=3,
                default=Decimal("10.000"),
                max_digits=6,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AlterField(
            model_name="decisionruleset",
            name="minimum_coverage_ratio",
            field=models.DecimalField(
                decimal_places=5,
                default=Decimal("0.90000"),
                max_digits=6,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0")),
                    django.core.validators.MaxValueValidator(Decimal("1")),
                ],
            ),
        ),
        migrations.AlterField(
            model_name="decisionruleset",
            name="trend_threshold_pct",
            field=models.DecimalField(
                decimal_places=3,
                default=Decimal("1.000"),
                max_digits=6,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.RunPython(backfill_reconciliation_errors, migrations.RunPython.noop),
        migrations.RunPython(close_pre_guard_running_rows, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="modelrun",
            constraint=models.UniqueConstraint(
                condition=models.Q(("run_slot__isnull", False)),
                fields=("run_slot",),
                name="forecast_one_active_run_slot",
            ),
        ),
        migrations.AddConstraint(
            model_name="modelrun",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("run_slot", 1),
                        ("run_slot__isnull", False),
                        ("status", "RUNNING"),
                    ),
                    models.Q(
                        models.Q(("status", "RUNNING"), _negated=True),
                        ("run_slot__isnull", True),
                    ),
                    _connector="OR",
                ),
                name="forecast_running_owns_slot",
            ),
        ),
        migrations.AddConstraint(
            model_name="forecast",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("absolute_error__isnull", True),
                        ("absolute_percentage_error__isnull", True),
                        ("forecast_error__isnull", True),
                        ("observed_at__isnull", True),
                        ("observed_value__isnull", True),
                    ),
                    models.Q(
                        ("absolute_error__isnull", False),
                        ("absolute_percentage_error__isnull", False),
                        ("forecast_error__isnull", False),
                        ("observed_at__isnull", False),
                        ("observed_value__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="forecast_reconciliation_complete",
            ),
        ),
    ]
