from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class AppendOnlyQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("Research evidence is append-only and cannot be deleted.")


class AppendOnlyManager(models.Manager.from_queryset(AppendOnlyQuerySet)):
    pass


class AppendOnlyEvidence(models.Model):
    objects = AppendOnlyManager()

    class Meta:
        abstract = True

    def delete(self, *args, **kwargs):
        raise ValidationError("Research evidence is append-only and cannot be deleted.")


class DatasetSnapshot(AppendOnlyEvidence):
    """Immutable, reproducible input used by a model experiment."""

    series = models.ForeignKey(
        "prices.PriceSeries",
        on_delete=models.PROTECT,
        related_name="dataset_snapshots",
    )
    fingerprint = models.CharField(max_length=64, unique=True)
    cutoff_date = models.DateField()
    first_week = models.DateField()
    last_week = models.DateField()
    observed_week_count = models.PositiveIntegerField()
    span_week_count = models.PositiveIntegerField()
    coverage_ratio = models.DecimalField(max_digits=6, decimal_places=5)
    public_inputs_only = models.BooleanField(
        default=False,
        help_text="True when the snapshot was filtered to publication-approved source records.",
    )
    weekly_values = models.JSONField(
        help_text="Canonical ordered verified-source weekly rows used to reproduce preprocessing."
    )
    preprocessing_report = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="forecast_snapshots_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-cutoff_date", "-created_at")

    def __str__(self) -> str:
        return f"{self.series} through {self.cutoff_date} ({self.fingerprint[:10]})"


class ModelRun(AppendOnlyEvidence):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"

    snapshot = models.ForeignKey(
        DatasetSnapshot,
        on_delete=models.PROTECT,
        related_name="model_runs",
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="forecast_runs_requested",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    runtime_seconds = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )
    selected_model = models.CharField(max_length=40, blank=True)
    selected_parameters = models.JSONField(default=dict)
    aggregate_metrics = models.JSONField(default=dict)
    residuals = models.JSONField(default=list)
    target_week = models.DateField(null=True, blank=True)
    point_forecast = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    interval_lower = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    interval_upper = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    is_live = models.BooleanField(default=True)
    run_slot = models.PositiveSmallIntegerField(null=True, blank=True, editable=False)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        permissions = (("run_model_experiment", "Can run forecasting experiments"),)
        constraints = [
            models.UniqueConstraint(
                fields=("run_slot",),
                condition=Q(run_slot__isnull=False),
                name="forecast_one_active_run_slot",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="RUNNING", run_slot=1, run_slot__isnull=False)
                    | (~Q(status="RUNNING") & Q(run_slot__isnull=True))
                ),
                name="forecast_running_owns_slot",
            ),
        ]

    def __str__(self) -> str:
        return f"Run {self.pk or 'new'}: {self.status}"

    @property
    def selected_metrics(self) -> dict:
        return self.aggregate_metrics.get(self.selected_model, {}) if self.selected_model else {}


class FoldMetric(AppendOnlyEvidence):
    model_run = models.ForeignKey(ModelRun, on_delete=models.CASCADE, related_name="fold_metrics")
    model_name = models.CharField(max_length=40)
    fold_index = models.PositiveSmallIntegerField()
    train_through = models.DateField()
    target_week = models.DateField()
    actual = models.DecimalField(max_digits=10, decimal_places=4)
    predicted = models.DecimalField(max_digits=10, decimal_places=4)
    absolute_error = models.DecimalField(max_digits=10, decimal_places=4)
    squared_error = models.DecimalField(max_digits=14, decimal_places=6)
    absolute_percentage_error = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True
    )
    parameters = models.JSONField(default=dict)

    class Meta:
        ordering = ("model_name", "fold_index")
        constraints = [
            models.UniqueConstraint(
                fields=("model_run", "model_name", "fold_index"),
                name="forecast_unique_model_fold",
            )
        ]

    def __str__(self) -> str:
        return f"{self.model_name} fold {self.fold_index}"


class Forecast(AppendOnlyEvidence):
    class Kind(models.TextChoices):
        LIVE = "LIVE", "Live"
        SIMULATION = "SIMULATION", "Retrospective simulation"

    model_run = models.OneToOneField(ModelRun, on_delete=models.PROTECT, related_name="forecast")
    series = models.ForeignKey(
        "prices.PriceSeries", on_delete=models.PROTECT, related_name="forecasts"
    )
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.LIVE)
    target_week = models.DateField()
    point_forecast = models.DecimalField(max_digits=10, decimal_places=4)
    interval_lower = models.DecimalField(max_digits=10, decimal_places=4)
    interval_upper = models.DecimalField(max_digits=10, decimal_places=4)
    data_cutoff = models.DateField()
    dataset_fingerprint = models.CharField(max_length=64)
    model_name = models.CharField(max_length=40)
    model_parameters = models.JSONField(default=dict)
    observed_value = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    forecast_error = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    absolute_error = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    absolute_percentage_error = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True
    )
    observed_at = models.DateTimeField(null=True, blank=True)
    locked_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(auto_now_add=True)

    _IMMUTABLE_FIELDS = (
        "model_run_id",
        "series_id",
        "kind",
        "target_week",
        "point_forecast",
        "interval_lower",
        "interval_upper",
        "data_cutoff",
        "dataset_fingerprint",
        "model_name",
        "model_parameters",
        "locked_at",
    )

    class Meta:
        ordering = ("-target_week", "-published_at")
        constraints = [
            models.UniqueConstraint(
                fields=("series", "target_week"),
                condition=Q(kind="LIVE"),
                name="forecast_one_live_per_series_week",
            ),
            models.CheckConstraint(
                condition=Q(interval_lower__lte=models.F("point_forecast"))
                & Q(point_forecast__lte=models.F("interval_upper")),
                name="forecast_point_inside_interval",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        observed_value__isnull=True,
                        observed_at__isnull=True,
                        forecast_error__isnull=True,
                        absolute_error__isnull=True,
                        absolute_percentage_error__isnull=True,
                    )
                    | Q(
                        observed_value__isnull=False,
                        observed_at__isnull=False,
                        forecast_error__isnull=False,
                        absolute_error__isnull=False,
                        absolute_percentage_error__isnull=False,
                    )
                ),
                name="forecast_reconciliation_complete",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.series} forecast for {self.target_week}"

    def save(self, *args, **kwargs) -> None:
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and any(
                getattr(previous, field) != getattr(self, field)
                for field in self._IMMUTABLE_FIELDS
            ):
                raise ValidationError(
                    "A published forecast ledger entry is immutable; "
                    "reconcile the observation only."
                )
            reconciliation_fields = (
                "observed_value",
                "observed_at",
                "forecast_error",
                "absolute_error",
                "absolute_percentage_error",
            )
            reconciliation_changed = previous and any(
                getattr(previous, field) != getattr(self, field)
                for field in reconciliation_fields
            )
            if reconciliation_changed:
                if previous.observed_value is not None:
                    raise ValidationError("This forecast has already been reconciled.")
                if not getattr(self, "_allow_reconciliation_write", False):
                    raise ValidationError("Use reconcile() to record an observation exactly once.")
        super().save(*args, **kwargs)

    def reconcile(self, observed_value: Decimal) -> None:
        if self.observed_value is not None:
            raise ValidationError("This forecast has already been reconciled.")
        if observed_value <= 0:
            raise ValidationError("Observed value must be positive.")
        self.observed_value = observed_value
        self.forecast_error = observed_value - self.point_forecast
        self.absolute_error = abs(self.forecast_error)
        self.absolute_percentage_error = (
            self.absolute_error / observed_value
        ) * Decimal(100)
        self.observed_at = timezone.now()
        self._allow_reconciliation_write = True
        try:
            self.save(
                update_fields=(
                    "observed_value",
                    "forecast_error",
                    "absolute_error",
                    "absolute_percentage_error",
                    "observed_at",
                )
            )
        finally:
            self._allow_reconciliation_write = False


class DecisionRuleSet(AppendOnlyEvidence):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        FROZEN = "FROZEN", "Frozen"
        RETIRED = "RETIRED", "Retired"

    version = models.CharField(max_length=30, unique=True)
    label = models.CharField(max_length=120)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    forecast_threshold_pct = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("3.000"),
        validators=(MinValueValidator(Decimal("0")),),
    )
    trend_threshold_pct = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("1.000"),
        validators=(MinValueValidator(Decimal("0")),),
    )
    context_threshold_pct = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("3.000"),
        validators=(MinValueValidator(Decimal("0")),),
    )
    stale_after_days = models.PositiveSmallIntegerField(default=14)
    minimum_coverage_ratio = models.DecimalField(
        max_digits=6,
        decimal_places=5,
        default=Decimal("0.90000"),
        validators=(MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("1"))),
    )
    high_reliability_nmae_pct = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("5.000"),
        validators=(MinValueValidator(Decimal("0")),),
    )
    medium_reliability_nmae_pct = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("10.000"),
        validators=(MinValueValidator(Decimal("0")),),
    )
    maximum_interval_half_width_pct = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("10.000"),
        validators=(MinValueValidator(Decimal("0")),),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="decision_rule_sets_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    frozen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        permissions = (("freeze_decision_rules", "Can freeze decision rule sets"),)

    def __str__(self) -> str:
        return f"{self.version} ({self.get_status_display()})"

    def save(self, *args, **kwargs) -> None:
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and previous.status in {self.Status.FROZEN, self.Status.RETIRED}:
                changed = any(
                    getattr(previous, field) != getattr(self, field)
                    for field in (
                        "version",
                        "label",
                        "forecast_threshold_pct",
                        "trend_threshold_pct",
                        "context_threshold_pct",
                        "stale_after_days",
                        "minimum_coverage_ratio",
                        "high_reliability_nmae_pct",
                        "medium_reliability_nmae_pct",
                        "maximum_interval_half_width_pct",
                        "created_by_id",
                        "frozen_at",
                    )
                )
                if changed:
                    raise ValidationError("Frozen decision rules are immutable.")
                allowed_statuses = {self.Status.FROZEN, self.Status.RETIRED}
                if previous.status == self.Status.RETIRED:
                    allowed_statuses = {self.Status.RETIRED}
                if self.status not in allowed_statuses:
                    raise ValidationError("A frozen decision rule set cannot be reopened.")
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if self.high_reliability_nmae_pct > self.medium_reliability_nmae_pct:
            raise ValidationError(
                {"high_reliability_nmae_pct": "High cutoff cannot exceed the medium cutoff."}
            )
        if self.status == self.Status.FROZEN and not self.frozen_at:
            raise ValidationError({"frozen_at": "A frozen rule set needs a freeze timestamp."})

    def freeze(self) -> None:
        if self.status != self.Status.DRAFT:
            raise ValidationError("Only a draft rule set can be frozen.")
        self.status = self.Status.FROZEN
        self.frozen_at = timezone.now()
        self.save(update_fields=("status", "frozen_at"))

class Recommendation(AppendOnlyEvidence):
    class Decision(models.TextChoices):
        SELL = "SELL", "Sell"
        WAIT = "WAIT", "Wait"
        MONITOR = "MONITOR", "Monitor"

    class Vote(models.TextChoices):
        SELL = "SELL", "Sell"
        WAIT = "WAIT", "Wait"
        NEUTRAL = "NEUTRAL", "Neutral"

    class Reliability(models.TextChoices):
        HIGH = "HIGH", "High"
        MEDIUM = "MEDIUM", "Medium"
        LOW = "LOW", "Low"
        UNASSESSED = "UNASSESSED", "Unassessed"

    forecast = models.OneToOneField(
        Forecast, on_delete=models.PROTECT, related_name="recommendation"
    )
    rule_set = models.ForeignKey(
        DecisionRuleSet, on_delete=models.PROTECT, related_name="recommendations"
    )
    decision = models.CharField(max_length=10, choices=Decision.choices)
    reliability = models.CharField(max_length=12, choices=Reliability.choices)
    current_price = models.DecimalField(max_digits=10, decimal_places=4)
    data_as_of = models.DateField()
    data_age_days = models.PositiveIntegerField()
    normalized_mae_pct = models.DecimalField(max_digits=8, decimal_places=4)
    interval_half_width_pct = models.DecimalField(max_digits=8, decimal_places=4)
    coverage_ratio = models.DecimalField(max_digits=6, decimal_places=5)
    forecast_vote = models.CharField(max_length=10, choices=Vote.choices)
    trend_vote = models.CharField(max_length=10, choices=Vote.choices)
    context_vote = models.CharField(max_length=10, choices=Vote.choices)
    reason_codes = models.JSONField(default=list)
    explanations = models.JSONField(default=dict)
    is_demo = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.get_decision_display()} for {self.forecast.target_week}"

    def save(self, *args, **kwargs) -> None:
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and any(
                getattr(previous, field.attname) != getattr(self, field.attname)
                for field in self._meta.concrete_fields
                if field.name not in {"id", "created_at"}
            ):
                raise ValidationError("A published recommendation is immutable.")
        super().save(*args, **kwargs)
