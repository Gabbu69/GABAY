from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from statistics import median

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    AuditEvent,
    ImportBatch,
    ImportRow,
    PriceObservation,
    PriceSeries,
    Source,
    VerificationEvent,
)
from .permissions import can_administer_research, can_encode, can_verify

PRICE_QUANTUM = Decimal("0.0001")
KG_UNITS = {"kg", "kilogram", "kilograms", "php/kg", "peso/kg", "pesos/kg"}
CONTAINER_UNITS = {"bag", "sack", "container", "cavan", "kaban"}


class CandidateOutcome(StrEnum):
    INVALID = "invalid"
    VALID = "valid"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    OUTLIER = "outlier"


@dataclass(frozen=True)
class PriceCandidateAssessment:
    outcome: CandidateOutcome
    message: str = ""
    duplicate_of: PriceObservation | None = None


def normalize_price(
    price: Decimal | str | int | float,
    *,
    currency: str = "PHP",
    unit: str = "kg",
    container_weight_kg: Decimal | str | int | float | None = None,
) -> Decimal:
    """Normalize an explicit PHP price to PHP/kg without guessing container weights."""
    if currency.strip().upper() not in {"PHP", "PHP ", "PESO", "PESOS", "₱"}:
        raise ValidationError("Only Philippine-peso prices are supported.")
    try:
        amount = Decimal(str(price).strip())
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError("Price must be a valid number.") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValidationError("Price must be greater than zero.")

    normalized_unit = unit.strip().lower().replace("per ", "")
    if normalized_unit in KG_UNITS:
        value = amount
    elif normalized_unit in CONTAINER_UNITS:
        if container_weight_kg in (None, ""):
            raise ValidationError("Container prices require an explicit weight in kilograms.")
        try:
            weight = Decimal(str(container_weight_kg).strip())
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValidationError("Container weight must be a valid number.") from exc
        if not weight.is_finite() or weight <= 0:
            raise ValidationError("Container weight must be greater than zero.")
        value = amount / weight
    else:
        raise ValidationError(f"Unsupported unit: {unit}. Use kg or an explicit container weight.")
    return value.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)


def assess_price_candidate(
    *,
    series: PriceSeries,
    source: Source,
    observed_on: date,
    normalized_price: Decimal,
    source_reference: str,
    batch: ImportBatch | None = None,
    exclude_observation: PriceObservation | None = None,
) -> PriceCandidateAssessment:
    """Apply the same duplicate, conflict, and outlier rules to every intake path."""
    if observed_on > timezone.localdate():
        return PriceCandidateAssessment(
            CandidateOutcome.INVALID,
            "Observation date cannot be in the future.",
        )
    existing = PriceObservation.objects.current().filter(
        series=series,
        source=source,
        observed_on=observed_on,
        source_reference=source_reference.strip(),
    )
    if exclude_observation is not None:
        existing = existing.exclude(pk=exclude_observation.pk)
    exact = existing.filter(normalized_price=normalized_price).first()
    if exact:
        return PriceCandidateAssessment(
            CandidateOutcome.DUPLICATE,
            "Exact duplicate collapsed; no new evidence record is needed.",
            exact,
        )
    if existing.exists():
        return PriceCandidateAssessment(
            CandidateOutcome.CONFLICT,
            "A different value exists for this source, date, and reference.",
        )

    if batch is not None:
        prior_rows = batch.rows.filter(
            observed_on=observed_on,
            source_reference=source_reference.strip(),
        ).exclude(status__in=[ImportRow.Status.ERROR, ImportRow.Status.REJECTED])
        exact_row = prior_rows.filter(normalized_price=normalized_price).first()
        if exact_row:
            return PriceCandidateAssessment(
                CandidateOutcome.DUPLICATE,
                "Exact duplicate collapsed within this import batch.",
                exact_row.duplicate_of,
            )
        if prior_rows.exists():
            return PriceCandidateAssessment(
                CandidateOutcome.CONFLICT,
                "A different value exists in this batch for the same evidence reference.",
            )

    history = list(
        PriceObservation.objects.current()
        .verified()
        .for_series(series)
        .filter(observed_on__lt=observed_on)
        .order_by("-observed_on")
        .values_list("normalized_price", flat=True)[:8]
    )
    if len(history) >= 4:
        center = Decimal(median(history))
        if center > 0 and abs(normalized_price - center) / center > Decimal("0.30"):
            return PriceCandidateAssessment(
                CandidateOutcome.OUTLIER,
                "More than 30% from the recent median; explicit verifier review is required.",
            )
    return PriceCandidateAssessment(CandidateOutcome.VALID)


def _audit(*, actor, action: str, instance, details: dict | None = None) -> AuditEvent:
    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        entity_type=instance._meta.label,
        entity_id=str(getattr(instance, "public_id", instance.pk)),
        details=details or {},
    )


def _assert_encoder(user) -> None:
    if user is not None and not can_encode(user):
        raise PermissionDenied("Encoder or research administrator access is required.")


def _assert_verifier(user) -> None:
    if user is None or not can_verify(user):
        raise PermissionDenied("Verifier or research administrator access is required.")


@transaction.atomic
def select_primary_public_series(*, series: PriceSeries, actor) -> PriceSeries:
    if actor is None or not can_administer_research(actor):
        raise PermissionDenied("Research administrator access is required.")
    series = PriceSeries.objects.select_for_update().get(pk=series.pk)
    if not series.is_active or not series.is_public:
        raise ValidationError("Only an active public series can be selected.")
    previous_ids = list(
        PriceSeries.objects.filter(scope=series.scope, is_primary_public=True).values_list(
            "pk", flat=True
        )
    )
    PriceSeries.objects.filter(scope=series.scope, is_primary_public=True).update(
        is_primary_public=False
    )
    series.is_primary_public = True
    series.save(update_fields=["is_primary_public"])
    _audit(
        actor=actor,
        action="price_series.primary_selected",
        instance=series,
        details={"scope": series.scope, "previous_series_ids": previous_ids},
    )
    return series


@transaction.atomic
def create_price_observation(
    *,
    series: PriceSeries,
    source: Source,
    observed_on: date,
    original_price: Decimal | str,
    original_unit: str,
    source_reference: str,
    created_by,
    currency: str = "PHP",
    container_weight_kg: Decimal | str | None = None,
    collection_method: str = "",
    notes: str = "",
    import_batch: ImportBatch | None = None,
    is_outlier: bool = False,
    is_conflict: bool = False,
    _assessment_exclude: PriceObservation | None = None,
    _supersedes: PriceObservation | None = None,
    _revision: int = 1,
    _initial_is_current: bool = True,
) -> PriceObservation:
    _assert_encoder(created_by)
    # A stable parent-row lock serializes evidence identity checks on Postgres.
    # SQLite serializes the subsequent writes, preserving the same single-series path.
    series = PriceSeries.objects.select_for_update().get(pk=series.pk)
    normalized = normalize_price(
        original_price,
        currency=currency,
        unit=original_unit,
        container_weight_kg=container_weight_kg,
    )
    assessment = assess_price_candidate(
        series=series,
        source=source,
        observed_on=observed_on,
        normalized_price=normalized,
        source_reference=source_reference,
        exclude_observation=_assessment_exclude,
    )
    if assessment.outcome in {CandidateOutcome.INVALID, CandidateOutcome.DUPLICATE}:
        raise ValidationError(assessment.message)
    is_conflict = is_conflict or assessment.outcome == CandidateOutcome.CONFLICT
    is_outlier = is_outlier or assessment.outcome == CandidateOutcome.OUTLIER
    observation = PriceObservation(
        series=series,
        source=source,
        import_batch=import_batch,
        observed_on=observed_on,
        normalized_price=normalized,
        original_price=Decimal(str(original_price)),
        original_unit=original_unit,
        container_weight_kg=(
            Decimal(str(container_weight_kg)) if container_weight_kg not in (None, "") else None
        ),
        source_reference=source_reference.strip(),
        collection_method=collection_method.strip(),
        notes=notes.strip(),
        created_by=created_by,
        is_outlier=is_outlier,
        is_conflict=is_conflict,
        supersedes=_supersedes,
        revision=_revision,
        is_current=_initial_is_current,
    )
    observation.save()
    VerificationEvent.objects.create(
        observation=observation,
        action=VerificationEvent.Action.SUBMITTED,
        from_status="",
        to_status=PriceObservation.Status.PENDING,
        actor=created_by,
    )
    _audit(
        actor=created_by,
        action="price_observation.submitted",
        instance=observation,
        details={
            "normalized_unit": "PHP/kg",
            "is_outlier": is_outlier,
            "is_conflict": is_conflict,
            "assessment": assessment.outcome.value,
        },
    )
    return observation


@transaction.atomic
def create_revision(
    *,
    original: PriceObservation,
    created_by,
    observed_on: date,
    original_price: Decimal | str,
    original_unit: str,
    source_reference: str,
    currency: str = "PHP",
    container_weight_kg: Decimal | str | None = None,
    collection_method: str = "",
    notes: str = "",
    reason: str,
) -> PriceObservation:
    _assert_encoder(created_by)
    original = PriceObservation.objects.select_for_update().get(pk=original.pk)
    if not original.is_current or original.workflow_status != PriceObservation.Status.VERIFIED:
        raise ValidationError("Only the current verified observation can be revised.")
    if hasattr(original, "superseded_by"):
        raise ValidationError("This observation already has a revision.")
    revision = create_price_observation(
        series=original.series,
        source=original.source,
        observed_on=observed_on,
        original_price=original_price,
        original_unit=original_unit,
        source_reference=source_reference,
        created_by=created_by,
        currency=currency,
        container_weight_kg=container_weight_kg,
        collection_method=collection_method,
        notes=notes,
        _assessment_exclude=original,
        _supersedes=original,
        _revision=original.revision + 1,
        _initial_is_current=False,
    )
    VerificationEvent.objects.create(
        observation=revision,
        action=VerificationEvent.Action.REVISION_CREATED,
        from_status=PriceObservation.Status.VERIFIED,
        to_status=PriceObservation.Status.PENDING,
        actor=created_by,
        reason=reason.strip(),
    )
    _audit(
        actor=created_by,
        action="price_observation.revision_created",
        instance=revision,
        details={"supersedes": str(original.public_id), "reason": reason.strip()},
    )
    return revision


@transaction.atomic
def approve_observation(
    observation: PriceObservation,
    *,
    verifier,
    reason: str = "",
    allow_outlier: bool = False,
    allow_conflict: bool = False,
) -> PriceObservation:
    _assert_verifier(verifier)
    observation = PriceObservation.objects.select_for_update().get(pk=observation.pk)
    if observation.created_by_id == verifier.pk:
        raise ValidationError("The maker cannot approve their own observation.")
    if observation.workflow_status != PriceObservation.Status.PENDING:
        raise ValidationError("Only pending observations can be approved.")
    if observation.is_outlier and not allow_outlier:
        raise ValidationError("This outlier is quarantined until explicitly accepted.")
    if observation.is_conflict and not allow_conflict:
        raise ValidationError("This conflicting value is quarantined until explicitly accepted.")
    if (observation.is_outlier or observation.is_conflict) and not reason.strip():
        raise ValidationError("A verification reason is required for flagged evidence.")
    if observation.is_conflict:
        unresolved = PriceObservation.objects.current().filter(
            series=observation.series,
            source=observation.source,
            observed_on=observation.observed_on,
            source_reference=observation.source_reference,
        ).exclude(pk=observation.pk)
        if unresolved.filter(workflow_status=PriceObservation.Status.VERIFIED).exists():
            raise ValidationError(
                "Verified evidence already exists for this reference; submit a revision instead."
            )
        if unresolved.filter(workflow_status=PriceObservation.Status.PENDING).exists():
            raise ValidationError(
                "Reject the competing pending evidence before accepting this conflict."
            )

    now = timezone.now()
    claimed = PriceObservation.objects.filter(
        pk=observation.pk, workflow_status=PriceObservation.Status.PENDING
    ).update(
        workflow_status=PriceObservation.Status.VERIFIED,
        is_current=True,
        verified_at=now,
    )
    if claimed != 1:
        raise ValidationError("This observation was already decided by another verifier.")
    if observation.supersedes_id:
        predecessor = PriceObservation.objects.select_for_update().get(pk=observation.supersedes_id)
        if not predecessor.is_current:
            raise ValidationError("The revision no longer points to the current observation.")
        replaced = PriceObservation.objects.filter(pk=predecessor.pk, is_current=True).update(
            is_current=False,
            superseded_at=now,
        )
        if replaced != 1:
            raise ValidationError("The predecessor was already superseded by another revision.")
        VerificationEvent.objects.create(
            observation=predecessor,
            action=VerificationEvent.Action.SUPERSEDED,
            from_status=predecessor.workflow_status,
            to_status=predecessor.workflow_status,
            actor=verifier,
            reason=f"Superseded by revision {observation.public_id}",
        )

    observation.refresh_from_db()
    VerificationEvent.objects.create(
        observation=observation,
        action=VerificationEvent.Action.APPROVED,
        from_status=PriceObservation.Status.PENDING,
        to_status=PriceObservation.Status.VERIFIED,
        actor=verifier,
        reason=reason.strip(),
    )
    _audit(
        actor=verifier,
        action="price_observation.approved",
        instance=observation,
        details={"reason": reason.strip()},
    )
    return observation


@transaction.atomic
def reject_observation(
    observation: PriceObservation, *, verifier, reason: str
) -> PriceObservation:
    _assert_verifier(verifier)
    observation = PriceObservation.objects.select_for_update().get(pk=observation.pk)
    if observation.created_by_id == verifier.pk:
        raise ValidationError("The maker cannot reject their own observation.")
    if observation.workflow_status != PriceObservation.Status.PENDING:
        raise ValidationError("Only pending observations can be rejected.")
    if not reason.strip():
        raise ValidationError("A rejection reason is required.")
    decided = PriceObservation.objects.filter(
        pk=observation.pk, workflow_status=PriceObservation.Status.PENDING
    ).update(
        workflow_status=PriceObservation.Status.REJECTED,
        is_current=False,
    )
    if decided != 1:
        raise ValidationError("This observation was already decided by another verifier.")
    observation.refresh_from_db()
    VerificationEvent.objects.create(
        observation=observation,
        action=VerificationEvent.Action.REJECTED,
        from_status=PriceObservation.Status.PENDING,
        to_status=PriceObservation.Status.REJECTED,
        actor=verifier,
        reason=reason.strip(),
    )
    _audit(
        actor=verifier,
        action="price_observation.rejected",
        instance=observation,
        details={"reason": reason.strip()},
    )
    return observation


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def get_weekly_series(
    series: PriceSeries,
    *,
    through: date | None = None,
    limit: int | None = None,
    public_only: bool = False,
) -> list[dict]:
    """Return compatible, verified observations aggregated into ISO-week medians."""
    queryset = (
        PriceObservation.objects.current()
        .verified()
        .for_series(series)
        .select_related("source")
        .order_by("observed_on", "pk")
    )
    if through:
        queryset = queryset.filter(observed_on__lte=through)
    if public_only:
        queryset = queryset.filter(source__is_active=True, source__publication_allowed=True)

    grouped: dict[date, list[PriceObservation]] = defaultdict(list)
    for observation in queryset:
        grouped[_week_start(observation.observed_on)].append(observation)

    weeks = []
    for week_start, observations in sorted(grouped.items()):
        values = [item.normalized_price for item in observations]
        sources = list({item.source_id: item.source for item in observations}.values())
        weeks.append(
            {
                "week_start": week_start,
                "week_end": week_start + timedelta(days=6),
                "price": Decimal(median(values)).quantize(PRICE_QUANTUM),
                "low": min(values),
                "high": max(values),
                "count": len(values),
                "latest_observed_on": max(item.observed_on for item in observations),
                "sources": sources,
                "source_count": len(sources),
                "scope": series.scope,
                "is_demo": series.scope == PriceSeries.Scope.DEMO,
            }
        )
    if limit is not None:
        return weeks[-max(0, limit) :]
    return weeks


def _series_metadata(series: PriceSeries | None) -> dict | None:
    if series is None:
        return None
    return {
        "name": series.name,
        "slug": series.slug,
        "commodity": series.commodity,
        "location_name": series.location_name,
        "location_code": series.location_code,
        "location_level": series.location_level,
        "location_level_label": series.get_location_level_display(),
        "classification": series.classification,
        "classification_label": series.get_classification_display(),
        "condition": series.condition,
        "condition_label": series.get_condition_display(),
        "moisture_basis": series.moisture_basis,
        "scope": series.scope,
        "scope_label": series.get_scope_display(),
        "canonical_unit": series.canonical_unit,
    }


def _section_for_scope(scope: str, history_weeks: int) -> dict:
    primary_series = PriceSeries.objects.filter(
        scope=scope,
        is_primary_public=True,
        is_public=True,
        is_active=True,
    ).first()
    if primary_series is None:
        return {
            "available": False,
            "series": None,
            "series_metadata": None,
            "latest": None,
            "history": [],
            "source": None,
            "sources": [],
            "selection_status": "no_primary_series",
        }
    history = get_weekly_series(primary_series, limit=history_weeks, public_only=True)
    sources_by_id = {}
    for week in history:
        sources_by_id.update({source.pk: source for source in week["sources"]})
    sources = list(sources_by_id.values())
    return {
        "available": bool(history),
        "series": primary_series,
        "series_metadata": _series_metadata(primary_series),
        "latest": history[-1] if history else None,
        "history": history,
        "source": sources[0] if len(sources) == 1 else None,
        "sources": sources,
        "selection_status": "selected_primary",
    }


def get_public_price_context(
    *, today: date | None = None, history_weeks: int = 104
) -> dict:
    """Build public price context without mixing local, reference, and demo evidence."""
    if history_weeks < 1 or history_weeks > 520:
        raise ValueError("history_weeks must be between 1 and 520")
    today = today or timezone.localdate()
    local = _section_for_scope(PriceSeries.Scope.LOCAL, history_weeks)
    reference = _section_for_scope(PriceSeries.Scope.REFERENCE, history_weeks)
    demo = _section_for_scope(PriceSeries.Scope.DEMO, history_weeks)

    selected = local if local["available"] else None
    selected_scope = PriceSeries.Scope.LOCAL if selected else None
    if selected is None and not getattr(settings, "IS_PRODUCTION", False) and demo["available"]:
        selected = demo
        selected_scope = PriceSeries.Scope.DEMO

    latest = selected["latest"] if selected else None
    age = (today - latest["latest_observed_on"]).days if latest else None
    is_demo = selected_scope == PriceSeries.Scope.DEMO
    if is_demo:
        notice = "Demonstration data—not verified market information."
    elif selected is None:
        notice = "No verified local market evidence is available."
    else:
        notice = "Verified local observations; prices are not a guarantee of future outcomes."

    return {
        "selected_scope": selected_scope,
        "has_verified_local_data": local["available"],
        "series": selected["series"] if selected else None,
        "series_metadata": selected["series_metadata"] if selected else None,
        "latest": latest,
        "history": selected["history"] if selected else [],
        "is_demo": is_demo,
        "is_stale": age is None or age > 14,
        "data_age_days": age,
        "source": selected["source"] if selected else None,
        "sources": selected["sources"] if selected else [],
        "local": local,
        "reference": reference,
        "demo": demo,
        "notice": notice,
    }


@transaction.atomic
def approve_import_row(
    row: ImportRow,
    *,
    verifier,
    reason: str = "",
    allow_outlier: bool = False,
    allow_conflict: bool = False,
) -> PriceObservation:
    _assert_verifier(verifier)
    row = ImportRow.objects.select_for_update().select_related("batch").get(pk=row.pk)
    if row.batch.uploaded_by_id == verifier.pk:
        raise ValidationError("The uploader cannot approve their own import rows.")
    allowed = {ImportRow.Status.VALID}
    if allow_outlier:
        allowed.add(ImportRow.Status.OUTLIER)
    if allow_conflict:
        allowed.add(ImportRow.Status.CONFLICT)
    if row.status not in allowed:
        raise ValidationError("Only valid rows (or explicitly accepted outliers) can be approved.")
    claimed = ImportRow.objects.filter(pk=row.pk, status=row.status).update(
        status=ImportRow.Status.STAGED
    )
    if claimed != 1:
        raise ValidationError("This row was already decided by another verifier.")
    observation = create_price_observation(
        series=row.batch.series,
        source=row.batch.source,
        observed_on=row.observed_on,
        original_price=row.original_price,
        original_unit=row.original_unit,
        container_weight_kg=row.container_weight_kg,
        source_reference=row.source_reference,
        collection_method=row.collection_method,
        notes=row.notes,
        created_by=row.batch.uploaded_by,
        import_batch=row.batch,
        is_outlier=row.status == ImportRow.Status.OUTLIER,
        is_conflict=row.status == ImportRow.Status.CONFLICT,
    )
    approve_observation(
        observation,
        verifier=verifier,
        reason=reason,
        allow_outlier=allow_outlier,
        allow_conflict=allow_conflict,
    )
    ImportRow.objects.filter(pk=row.pk, status=ImportRow.Status.STAGED).update(
        status=ImportRow.Status.APPROVED,
        observation=observation,
        reviewed_by=verifier,
        reviewed_at=timezone.now(),
    )
    _refresh_batch_status(row.batch_id)
    return observation


@transaction.atomic
def reject_import_row(row: ImportRow, *, verifier, reason: str) -> ImportRow:
    _assert_verifier(verifier)
    row = ImportRow.objects.select_for_update().select_related("batch").get(pk=row.pk)
    if row.batch.uploaded_by_id == verifier.pk:
        raise ValidationError("The uploader cannot reject their own import rows.")
    if row.status not in {
        ImportRow.Status.VALID,
        ImportRow.Status.OUTLIER,
        ImportRow.Status.CONFLICT,
    }:
        raise ValidationError("This row is not awaiting a verification decision.")
    if not reason.strip():
        raise ValidationError("A rejection reason is required.")
    errors = list(row.validation_errors)
    errors.append({"field": "review", "message": reason.strip()})
    decided = ImportRow.objects.filter(pk=row.pk, status=row.status).update(
        status=ImportRow.Status.REJECTED,
        validation_errors=errors,
        reviewed_by=verifier,
        reviewed_at=timezone.now(),
    )
    if decided != 1:
        raise ValidationError("This row was already decided by another verifier.")
    _audit(
        actor=verifier,
        action="price_import_row.rejected",
        instance=row,
        details={"reason": reason.strip(), "batch": str(row.batch.public_id)},
    )
    _refresh_batch_status(row.batch_id)
    row.refresh_from_db()
    return row


def _refresh_batch_status(batch_id: int) -> None:
    batch = ImportBatch.objects.get(pk=batch_id)
    rows = batch.rows.all()
    total = rows.count()
    approved = rows.filter(status=ImportRow.Status.APPROVED).count()
    actionable = rows.filter(
        status__in=[ImportRow.Status.VALID, ImportRow.Status.OUTLIER, ImportRow.Status.CONFLICT]
    ).count()
    if total and approved == total:
        status = ImportBatch.Status.APPROVED
        completed_at = timezone.now()
    elif approved and not actionable:
        status = ImportBatch.Status.PARTIALLY_APPROVED
        completed_at = timezone.now()
    elif actionable:
        status = ImportBatch.Status.NEEDS_REVIEW
        completed_at = None
    else:
        status = ImportBatch.Status.REJECTED
        completed_at = timezone.now()
    ImportBatch.objects.filter(pk=batch_id).update(
        total_rows=total,
        valid_rows=rows.filter(
            status__in=[ImportRow.Status.VALID, ImportRow.Status.OUTLIER]
        ).count(),
        error_rows=rows.filter(
            status__in=[
                ImportRow.Status.ERROR,
                ImportRow.Status.CONFLICT,
                ImportRow.Status.DUPLICATE,
                ImportRow.Status.REJECTED,
            ]
        ).count(),
        approved_rows=approved,
        status=status,
        completed_at=completed_at,
    )
