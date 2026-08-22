from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone


class Source(models.Model):
    class Type(models.TextChoices):
        MARKET_INTERVIEW = "market_interview", "Market interview"
        COOPERATIVE = "cooperative", "Cooperative"
        MUNICIPAL_AGRICULTURE = "municipal_agriculture", "Municipal agriculture office"
        PSA_OPENSTAT = "psa_openstat", "PSA OpenSTAT"
        OTHER_OFFICIAL = "other_official", "Other official source"
        DEMONSTRATION = "demonstration", "Demonstration fixture"

    class Scope(models.TextChoices):
        LOCAL = "local", "Local"
        PROVINCIAL = "provincial", "Provincial"
        NATIONAL = "national", "National"
        DEMO = "demo", "Demonstration"

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=120, unique=True)
    source_type = models.CharField(max_length=32, choices=Type.choices)
    scope = models.CharField(max_length=16, choices=Scope.choices)
    location_name = models.CharField(max_length=160, blank=True)
    reference_url = models.URLField(max_length=500, blank=True)
    citation = models.TextField(blank=True)
    publication_allowed = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    _EVIDENCE_IDENTITY_FIELDS = (
        "slug",
        "name",
        "source_type",
        "scope",
        "location_name",
        "reference_url",
        "citation",
        "metadata",
    )

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.source_type == self.Type.DEMONSTRATION and self.scope != self.Scope.DEMO:
            raise ValidationError({"scope": "A demonstration source must use demonstration scope."})
        if self.scope == self.Scope.DEMO and self.source_type != self.Type.DEMONSTRATION:
            raise ValidationError(
                {"source_type": "A demonstration-scoped source must be a demonstration fixture."}
            )

    def save(self, *args, **kwargs):  # noqa: DJ012
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            has_evidence = previous.observations.exists() or previous.import_batches.exists()
            changed = [
                field
                for field in self._EVIDENCE_IDENTITY_FIELDS
                if getattr(previous, field) != getattr(self, field)
            ]
            if changed and has_evidence:
                raise ValidationError(
                    "Source identity is locked after evidence intake. Create a new source instead."
                )
            if has_evidence and not previous.publication_allowed and self.publication_allowed:
                raise ValidationError(
                    "Publication permission cannot be granted retroactively; create a new source "
                    "record for newly consented evidence."
                )
        self.full_clean()
        return super().save(*args, **kwargs)


class PriceSeries(models.Model):
    class Scope(models.TextChoices):
        LOCAL = "local", "Verified local observation"
        REFERENCE = "reference", "Official reference"
        DEMO = "demo", "Demonstration"

    class LocationLevel(models.TextChoices):
        MUNICIPALITY = "municipality", "Municipality"
        PROVINCE = "province", "Province"
        REGION = "region", "Region"
        NATIONAL = "national", "National"

    class Classification(models.TextChoices):
        FANCY = "fancy", "Fancy"
        OTHER = "other", "Other variety"
        UNSPECIFIED = "unspecified", "Unspecified"

    class Condition(models.TextChoices):
        DRY = "dry", "Dry"
        WET = "wet", "Wet"
        UNSPECIFIED = "unspecified", "Unspecified"

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=140, unique=True)
    commodity = models.CharField(max_length=80, default="Palay")
    location_name = models.CharField(max_length=160)
    location_code = models.CharField(max_length=32, blank=True)
    location_level = models.CharField(max_length=20, choices=LocationLevel.choices)
    classification = models.CharField(
        max_length=20,
        choices=Classification.choices,
        default=Classification.UNSPECIFIED,
    )
    condition = models.CharField(
        max_length=20,
        choices=Condition.choices,
        default=Condition.UNSPECIFIED,
    )
    moisture_basis = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    scope = models.CharField(max_length=16, choices=Scope.choices)
    canonical_unit = models.CharField(max_length=16, default="PHP/kg", editable=False)
    is_active = models.BooleanField(default=True)
    is_public = models.BooleanField(default=True)
    is_primary_public = models.BooleanField(
        default=False,
        help_text="Explicitly selects this series for public guidance within its data scope.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("scope", "location_name", "classification", "condition")
        constraints = [
            models.UniqueConstraint(
                models.F("commodity"),
                models.F("location_name"),
                models.F("location_level"),
                models.F("classification"),
                models.F("condition"),
                Coalesce(
                    "moisture_basis",
                    models.Value(Decimal("-1.00")),
                    output_field=models.DecimalField(max_digits=5, decimal_places=2),
                ),
                models.F("scope"),
                name="prices_unique_compatible_series",
            ),
            models.UniqueConstraint(
                fields=("scope",),
                condition=Q(is_primary_public=True),
                name="prices_one_primary_series_per_scope",
            ),
            models.CheckConstraint(
                condition=(
                    Q(moisture_basis__isnull=True)
                    | Q(moisture_basis__gte=0, moisture_basis__lte=100)
                ),
                name="prices_valid_moisture_basis",
            ),
        ]

    _EVIDENCE_IDENTITY_FIELDS = (
        "slug",
        "commodity",
        "location_name",
        "location_code",
        "location_level",
        "classification",
        "condition",
        "moisture_basis",
        "scope",
        "canonical_unit",
    )

    def __str__(self) -> str:
        return self.name

    @property
    def is_demo(self) -> bool:
        return self.scope == self.Scope.DEMO

    def clean(self) -> None:
        super().clean()
        if self.canonical_unit != "PHP/kg":
            raise ValidationError({"canonical_unit": "GABAY stores normalized prices as PHP/kg."})
        if (
            self.scope == self.Scope.LOCAL
            and self.location_level != self.LocationLevel.MUNICIPALITY
        ):
            raise ValidationError(
                {"location_level": "A local decision series must use municipality level."}
            )
        if (
            self.scope == self.Scope.REFERENCE
            and self.location_level == self.LocationLevel.MUNICIPALITY
        ):
            raise ValidationError(
                {"location_level": "Official reference data must remain above municipality level."}
            )
        if self.is_primary_public and not self.is_public:
            raise ValidationError(
                {"is_primary_public": "A primary public series must also be public."}
            )
        if self.is_primary_public and not self.is_active:
            raise ValidationError(
                {"is_primary_public": "An inactive series cannot be the primary public series."}
            )
        compatibility = type(self).objects.filter(
            commodity=self.commodity,
            location_name=self.location_name,
            location_level=self.location_level,
            classification=self.classification,
            condition=self.condition,
            moisture_basis=self.moisture_basis,
            scope=self.scope,
        )
        if self.pk:
            compatibility = compatibility.exclude(pk=self.pk)
        if compatibility.exists():
            raise ValidationError(
                "A compatible series with the same moisture basis already exists."
            )

    def save(self, *args, **kwargs):  # noqa: DJ012
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            changed = [
                field
                for field in self._EVIDENCE_IDENTITY_FIELDS
                if getattr(previous, field) != getattr(self, field)
            ]
            if changed and (
                previous.observations.exists() or previous.import_batches.exists()
            ):
                raise ValidationError(
                    "Series compatibility is locked after evidence intake. "
                    "Create a new series instead."
                )
        self.full_clean()
        return super().save(*args, **kwargs)

    def is_compatible_with(self, other: PriceSeries) -> bool:
        return all(
            getattr(self, field) == getattr(other, field)
            for field in (
                "commodity",
                "location_name",
                "location_level",
                "classification",
                "condition",
                "moisture_basis",
                "scope",
            )
        )


class ImportBatch(models.Model):
    class Kind(models.TextChoices):
        MANUAL = "manual", "Manual entry"
        CSV = "csv", "CSV upload"
        PSA = "psa", "PSA OpenSTAT"
        DEMO = "demo", "Demonstration fixture"

    class Status(models.TextChoices):
        STAGED = "staged", "Staged"
        VALIDATED = "validated", "Validated"
        NEEDS_REVIEW = "needs_review", "Needs review"
        PARTIALLY_APPROVED = "partially_approved", "Partially approved"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        FAILED = "failed", "Failed"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.STAGED)
    series = models.ForeignKey(PriceSeries, on_delete=models.PROTECT, related_name="import_batches")
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="import_batches")
    original_filename = models.CharField(max_length=255, blank=True)
    checksum_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    source_payload = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="price_import_batches",
    )
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    error_rows = models.PositiveIntegerField(default=0)
    approved_rows = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.public_id}"

    def clean(self) -> None:
        super().clean()
        if self.kind == self.Kind.DEMO and self.series.scope != PriceSeries.Scope.DEMO:
            raise ValidationError({"series": "A demo batch can only target a demo series."})
        if self.series.scope == PriceSeries.Scope.DEMO and self.source.scope != Source.Scope.DEMO:
            raise ValidationError({"source": "A demo series requires a demo source."})
        if self.series.scope != PriceSeries.Scope.DEMO and self.source.scope == Source.Scope.DEMO:
            raise ValidationError({"source": "Demo data cannot enter a non-demo series."})


class ImportRow(models.Model):
    class Status(models.TextChoices):
        STAGED = "staged", "Staged"
        VALID = "valid", "Valid"
        ERROR = "error", "Error"
        DUPLICATE = "duplicate", "Exact duplicate"
        CONFLICT = "conflict", "Conflicting duplicate"
        OUTLIER = "outlier", "Outlier review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    batch = models.ForeignKey(ImportBatch, on_delete=models.PROTECT, related_name="rows")
    row_number = models.PositiveIntegerField()
    raw_data = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.STAGED)
    validation_errors = models.JSONField(default=list, blank=True)
    observed_on = models.DateField(null=True, blank=True)
    normalized_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    original_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    original_unit = models.CharField(max_length=40, blank=True)
    container_weight_kg = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )
    source_reference = models.CharField(max_length=500, blank=True)
    collection_method = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    duplicate_of = models.ForeignKey(
        "PriceObservation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="duplicate_import_rows",
    )
    observation = models.OneToOneField(
        "PriceObservation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_import_row",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_price_import_rows",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("row_number",)
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "row_number"), name="prices_unique_import_row_number"
            )
        ]

    def __str__(self) -> str:
        return f"{self.batch_id} row {self.row_number}"


class PriceObservationQuerySet(models.QuerySet):
    def current(self):
        return self.filter(is_current=True)

    def verified(self):
        return self.filter(workflow_status=PriceObservation.Status.VERIFIED)

    def pending(self):
        return self.filter(workflow_status=PriceObservation.Status.PENDING)

    def for_series(self, series: PriceSeries | int):
        return self.filter(series=series)

    def public(self):
        return self.current().verified().filter(
            series__is_public=True,
            series__is_active=True,
            source__is_active=True,
            source__publication_allowed=True,
        )


class PriceObservation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending verification"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    series = models.ForeignKey(PriceSeries, on_delete=models.PROTECT, related_name="observations")
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="observations")
    import_batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="observations",
    )
    observed_on = models.DateField(db_index=True)
    normalized_price = models.DecimalField(max_digits=12, decimal_places=4)
    original_price = models.DecimalField(max_digits=12, decimal_places=4)
    original_unit = models.CharField(max_length=40, default="PHP/kg")
    container_weight_kg = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )
    source_reference = models.CharField(max_length=500)
    collection_method = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    workflow_status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    revision = models.PositiveIntegerField(default=1)
    supersedes = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="superseded_by",
    )
    is_current = models.BooleanField(default=True, db_index=True)
    is_outlier = models.BooleanField(default=False)
    is_conflict = models.BooleanField(default=False)
    fingerprint = models.CharField(max_length=64, unique=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_price_observations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)

    objects = PriceObservationQuerySet.as_manager()

    _IMMUTABLE_FIELDS = (
        "series_id",
        "source_id",
        "import_batch_id",
        "observed_on",
        "normalized_price",
        "original_price",
        "original_unit",
        "container_weight_kg",
        "source_reference",
        "collection_method",
        "notes",
        "revision",
        "supersedes_id",
        "is_outlier",
        "is_conflict",
        "fingerprint",
        "created_by_id",
    )

    class Meta:
        ordering = ("-observed_on", "-created_at")
        constraints = [
            models.CheckConstraint(
                condition=Q(normalized_price__gt=0), name="prices_observation_price_positive"
            ),
            models.CheckConstraint(condition=Q(revision__gte=1), name="prices_revision_positive"),
        ]
        indexes = [
            models.Index(
                fields=("series", "workflow_status", "is_current", "observed_on"),
                name="prices_public_series_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.series} - {self.observed_on}: PHP {self.normalized_price}/kg"

    @property
    def is_demo(self) -> bool:
        return self.series.scope == PriceSeries.Scope.DEMO

    def _make_fingerprint(self) -> str:
        values = {
            "series": self.series.slug if self.series_id else None,
            "source": self.source.slug if self.source_id else None,
            "import_batch": (
                str(self.import_batch.public_id) if self.import_batch_id else None
            ),
            "date": self.observed_on.isoformat() if self.observed_on else None,
            "normalized_price": (
                format(self.normalized_price, "f")
                if self.normalized_price is not None
                else None
            ),
            "original_price": (
                format(self.original_price, "f") if self.original_price is not None else None
            ),
            "original_unit": self.original_unit.strip().lower(),
            "container_weight_kg": (
                format(self.container_weight_kg, "f")
                if self.container_weight_kg is not None
                else None
            ),
            "reference": (self.source_reference or "").strip(),
            "collection_method": self.collection_method.strip(),
            "notes": self.notes.strip(),
            "revision": self.revision,
            "supersedes": (
                str(self.supersedes.public_id) if self.supersedes_id else None
            ),
            "is_outlier": self.is_outlier,
            "is_conflict": self.is_conflict,
        }
        return hashlib.sha256(
            json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def clean(self) -> None:
        super().clean()
        if not self.series_id or not self.source_id:
            return
        if self.normalized_price is not None and self.normalized_price <= 0:
            raise ValidationError({"normalized_price": "Price must be greater than zero."})
        if self.original_price is not None and self.original_price <= 0:
            raise ValidationError({"original_price": "Original price must be greater than zero."})
        if self.observed_on and self.observed_on > timezone.localdate():
            raise ValidationError({"observed_on": "Observation date cannot be in the future."})
        kilogram_units = {"kg", "kilogram", "kilograms", "php/kg", "peso/kg", "pesos/kg"}
        if (
            self.original_unit.strip().lower() not in kilogram_units
            and not self.container_weight_kg
        ):
            raise ValidationError(
                {"container_weight_kg": "A non-kilogram unit requires an explicit kilogram weight."}
            )
        if self.series.scope == PriceSeries.Scope.DEMO and self.source.scope != Source.Scope.DEMO:
            raise ValidationError({"source": "A demonstration observation requires a demo source."})
        if self.series.scope != PriceSeries.Scope.DEMO and self.source.scope == Source.Scope.DEMO:
            raise ValidationError({"source": "Demo observations cannot enter a real-data series."})
        if self.series.scope == PriceSeries.Scope.LOCAL and self.source.scope != Source.Scope.LOCAL:
            raise ValidationError({"source": "A local series requires a local source."})
        if self.series.scope == PriceSeries.Scope.REFERENCE and self.source.scope not in {
            Source.Scope.PROVINCIAL,
            Source.Scope.NATIONAL,
        }:
            raise ValidationError(
                {"source": "A reference series requires an official reference source."}
            )
        if self.import_batch_id and (
            self.import_batch.series_id != self.series_id
            or self.import_batch.source_id != self.source_id
        ):
            raise ValidationError(
                {"import_batch": "Batch source and series must match the observation."}
            )

    def save(self, *args, **kwargs):  # noqa: DJ012
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            changed = [
                field
                for field in self._IMMUTABLE_FIELDS
                if getattr(previous, field) != getattr(self, field)
            ]
            if changed:
                raise ValidationError(
                    "Observation evidence is immutable; create a revision instead. "
                    f"Changed: {', '.join(changed)}"
                )
        if not self.fingerprint:
            self.fingerprint = self._make_fingerprint()
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Price observations are audit evidence and cannot be deleted.")


class VerificationEvent(models.Model):
    class Action(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        REVISION_CREATED = "revision_created", "Revision created"
        SUPERSEDED = "superseded", "Superseded"

    observation = models.ForeignKey(
        PriceObservation, on_delete=models.PROTECT, related_name="verification_events"
    )
    action = models.CharField(max_length=24, choices=Action.choices)
    from_status = models.CharField(
        max_length=16, choices=PriceObservation.Status.choices, blank=True
    )
    to_status = models.CharField(max_length=16, choices=PriceObservation.Status.choices, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="price_verification_events",
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "pk")

    def __str__(self) -> str:
        return f"{self.observation.public_id}: {self.action}"

    def clean(self) -> None:
        super().clean()
        if (
            self.action == self.Action.APPROVED
            and self.actor_id
            and self.observation.created_by_id == self.actor_id
        ):
            raise ValidationError({"actor": "The maker cannot approve their own observation."})

    def save(self, *args, **kwargs):  # noqa: DJ012
        if self.pk:
            raise ValidationError("Verification events are append-only.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Verification events are append-only.")


class AuditEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="price_audit_events",
    )
    action = models.CharField(max_length=80, db_index=True)
    entity_type = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=80)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")

    def __str__(self) -> str:
        return f"{self.action}: {self.entity_type} {self.entity_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Audit events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit events are append-only.")
