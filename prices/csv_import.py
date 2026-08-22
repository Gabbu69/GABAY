from __future__ import annotations

import csv
import hashlib
import io
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import ImportBatch, ImportRow, PriceSeries, Source
from .permissions import can_encode
from .services import CandidateOutcome, assess_price_candidate, normalize_price

MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_CSV_ROWS = 5_000
REQUIRED_HEADERS = {
    "observation_date",
    "price_value",
    "currency",
    "unit",
    "location_name",
    "location_level",
    "palay_classification",
    "condition",
    "source_name",
    "source_type",
    "source_reference",
}
OPTIONAL_HEADERS = {
    "container_weight_kg",
    "moisture_percentage",
    "collection_method",
    "source_publication_permission",
    "notes",
}


def _read_bytes(upload) -> bytes:
    if isinstance(upload, bytes):
        content = upload
    elif isinstance(upload, str):
        content = upload.encode("utf-8")
    else:
        content = upload.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        raise ValidationError("CSV upload exceeds the 5 MB limit.")
    if b"\x00" in content:
        raise ValidationError("CSV upload contains invalid binary data.")
    return content


def _canonical_headers(fieldnames: list[str] | None) -> list[str]:
    if not fieldnames:
        return []
    return [name.strip().lower() for name in fieldnames]


def _choice_matches(raw: str, value: str, choices) -> bool:
    normalized = raw.strip().casefold()
    valid = {str(value).casefold()}
    valid.update(str(label).casefold() for choice_value, label in choices if choice_value == value)
    return normalized in valid


def _parse_decimal(raw: str, field: str, errors: list[dict]) -> Decimal | None:
    if not raw.strip():
        return None
    try:
        value = Decimal(raw.strip())
    except (InvalidOperation, ValueError):
        errors.append({"field": field, "message": "Enter a valid number."})
        return None
    if not value.is_finite():
        errors.append({"field": field, "message": "Enter a finite number."})
        return None
    return value


def _parse_date(raw: str, errors: list[dict]) -> date | None:
    try:
        return date.fromisoformat(raw.strip())
    except (ValueError, TypeError):
        errors.append(
            {"field": "observation_date", "message": "Use an ISO date in YYYY-MM-DD format."}
        )
        return None


def _validate_compatibility(
    raw: dict[str, str], *, series: PriceSeries, source: Source, errors: list[dict]
) -> None:
    if raw["location_name"].strip().casefold() != series.location_name.casefold():
        errors.append({"field": "location_name", "message": "Does not match the selected series."})
    if not _choice_matches(
        raw["location_level"], series.location_level, PriceSeries.LocationLevel.choices
    ):
        errors.append(
            {"field": "location_level", "message": "Does not match the selected series."}
        )
    if not _choice_matches(
        raw["palay_classification"], series.classification, PriceSeries.Classification.choices
    ):
        errors.append(
            {"field": "palay_classification", "message": "Does not match the selected series."}
        )
    if not _choice_matches(raw["condition"], series.condition, PriceSeries.Condition.choices):
        errors.append({"field": "condition", "message": "Does not match the selected series."})
    if raw["source_name"].strip().casefold() != source.name.casefold():
        errors.append({"field": "source_name", "message": "Does not match the selected source."})
    if not _choice_matches(raw["source_type"], source.source_type, Source.Type.choices):
        errors.append({"field": "source_type", "message": "Does not match the selected source."})
    publication = raw.get("source_publication_permission", "").strip().casefold()
    if publication:
        if publication not in {"yes", "no", "true", "false", "1", "0"}:
            errors.append(
                {
                    "field": "source_publication_permission",
                    "message": "Use yes/no, true/false, or 1/0.",
                }
            )
        else:
            row_allows_publication = publication in {"yes", "true", "1"}
            if row_allows_publication != source.publication_allowed:
                errors.append(
                    {
                        "field": "source_publication_permission",
                        "message": "Does not match the selected source's permission record.",
                    }
                )
    moisture = raw.get("moisture_percentage", "").strip()
    if moisture:
        parsed = _parse_decimal(moisture, "moisture_percentage", errors)
        if parsed is not None and parsed != series.moisture_basis:
            errors.append(
                {"field": "moisture_percentage", "message": "Does not match the series basis."}
            )


@transaction.atomic
def stage_csv_import(
    upload,
    *,
    series: PriceSeries,
    source: Source,
    uploaded_by,
    filename: str = "prices.csv",
) -> ImportBatch:
    """Validate a bounded CSV into reviewable rows; this never publishes observations."""
    if uploaded_by is not None and not can_encode(uploaded_by):
        raise PermissionDenied("Encoder or research administrator access is required.")
    if not series.is_active or not source.is_active:
        raise ValidationError("The selected series and source must both be active.")
    content = _read_bytes(upload)
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("CSV must be UTF-8 encoded.") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = _canonical_headers(reader.fieldnames)
    missing = sorted(REQUIRED_HEADERS - set(headers))
    if missing:
        raise ValidationError(f"Missing required CSV columns: {', '.join(missing)}")
    unknown = sorted(set(headers) - REQUIRED_HEADERS - OPTIONAL_HEADERS)
    if unknown:
        raise ValidationError(f"Unknown CSV columns: {', '.join(unknown)}")
    if len(headers) != len(set(headers)):
        raise ValidationError("CSV contains duplicate column names.")

    # Match manual intake's parent-row serialization before assessing any row.
    series = PriceSeries.objects.select_for_update().get(pk=series.pk)
    batch = ImportBatch(
        kind=ImportBatch.Kind.CSV,
        status=ImportBatch.Status.STAGED,
        series=series,
        source=source,
        original_filename=filename[:255],
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        uploaded_by=uploaded_by,
    )
    batch.full_clean()
    batch.save()

    for index, source_row in enumerate(reader, start=2):
        if index - 1 > MAX_CSV_ROWS:
            raise ValidationError(f"CSV exceeds the {MAX_CSV_ROWS:,}-row limit.")
        raw = {
            str(key).strip().lower(): (value or "").strip()[:2_000]
            for key, value in source_row.items()
            if key is not None
        }
        errors: list[dict] = []
        if not any(raw.values()):
            continue
        observed_on = _parse_date(raw["observation_date"], errors)
        original_price = _parse_decimal(raw["price_value"], "price_value", errors)
        container_weight = _parse_decimal(
            raw.get("container_weight_kg", ""), "container_weight_kg", errors
        )
        _validate_compatibility(raw, series=series, source=source, errors=errors)
        source_reference = raw["source_reference"].strip()
        if not source_reference:
            errors.append({"field": "source_reference", "message": "This field is required."})

        normalized = None
        if original_price is not None:
            try:
                normalized = normalize_price(
                    original_price,
                    currency=raw["currency"],
                    unit=raw["unit"],
                    container_weight_kg=container_weight,
                )
            except ValidationError as exc:
                errors.append({"field": "price_value", "message": "; ".join(exc.messages)})

        status = ImportRow.Status.ERROR
        duplicate_of = None
        if not errors and observed_on is not None and normalized is not None:
            assessment = assess_price_candidate(
                series=batch.series,
                source=batch.source,
                batch=batch,
                observed_on=observed_on,
                normalized_price=normalized,
                source_reference=source_reference,
            )
            status = {
                CandidateOutcome.INVALID: ImportRow.Status.ERROR,
                CandidateOutcome.VALID: ImportRow.Status.VALID,
                CandidateOutcome.DUPLICATE: ImportRow.Status.DUPLICATE,
                CandidateOutcome.CONFLICT: ImportRow.Status.CONFLICT,
                CandidateOutcome.OUTLIER: ImportRow.Status.OUTLIER,
            }[assessment.outcome]
            duplicate_of = assessment.duplicate_of
            if assessment.outcome != CandidateOutcome.VALID:
                field = (
                    "observation_date"
                    if assessment.outcome == CandidateOutcome.INVALID
                    else "price_value"
                    if assessment.outcome == CandidateOutcome.OUTLIER
                    else "row"
                )
                errors.append({"field": field, "message": assessment.message})

        ImportRow.objects.create(
            batch=batch,
            row_number=index,
            raw_data=raw,
            status=status,
            validation_errors=errors,
            observed_on=observed_on,
            normalized_price=normalized,
            original_price=original_price,
            original_unit=raw.get("unit", "")[:40],
            container_weight_kg=container_weight,
            source_reference=source_reference,
            collection_method=raw.get("collection_method", "")[:120],
            notes=raw.get("notes", ""),
            duplicate_of=duplicate_of,
        )

    total = batch.rows.count()
    if total == 0:
        raise ValidationError("CSV contains no data rows.")
    valid = batch.rows.filter(status__in=[ImportRow.Status.VALID, ImportRow.Status.OUTLIER]).count()
    errors = total - valid
    reviewable = batch.rows.filter(
        status__in=[
            ImportRow.Status.VALID,
            ImportRow.Status.OUTLIER,
            ImportRow.Status.CONFLICT,
        ]
    ).exists()
    status = ImportBatch.Status.NEEDS_REVIEW if reviewable else ImportBatch.Status.VALIDATED
    ImportBatch.objects.filter(pk=batch.pk).update(
        total_rows=total,
        valid_rows=valid,
        error_rows=errors,
        status=status,
        validated_at=timezone.now(),
    )
    batch.refresh_from_db()
    return batch


def import_error_report(batch: ImportBatch) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["row_number", "status", "field", "message"])
    for row in batch.rows.exclude(validation_errors=[]).order_by("row_number"):
        for error in row.validation_errors:
            field = str(error.get("field", ""))
            message = str(error.get("message", ""))
            if field.startswith(("=", "+", "-", "@")):
                field = "'" + field
            if message.startswith(("=", "+", "-", "@")):
                message = "'" + message
            writer.writerow([row.row_number, row.status, field, message])
    return output.getvalue()
