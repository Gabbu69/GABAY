from __future__ import annotations

import hashlib
import itertools
import json
from calendar import month_name
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    AuditEvent,
    ImportBatch,
    PriceObservation,
    PriceSeries,
    Source,
    VerificationEvent,
)

PSA_CEREALS_API_URL = (
    "https://openstat.psa.gov.ph/PXWeb/api/v1/en/DB/2M/NFG/0032M4AFN01.px"
)
PSA_COTABATO_CODE = "124700000"


def request_json(url: str, payload: dict | None = None, *, timeout: int = 45) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "GABAY-Thesis/0.1 (+research data importer)",
        },
        method="POST" if body is not None else "GET",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed/explicit admin URL
        return json.loads(response.read().decode("utf-8-sig"))


def _variable(metadata: dict, name: str) -> dict:
    target = name.casefold()
    for variable in metadata.get("variables", []):
        if target in str(variable.get("code", "")).casefold() or target in str(
            variable.get("text", "")
        ).casefold():
            return variable
    raise ValidationError(f"PSA metadata does not contain the {name} dimension.")


def _matching_values(variable: dict, predicates) -> list[str]:
    result = []
    for value, label in zip(
        variable.get("values", []), variable.get("valueTexts", []), strict=True
    ):
        if any(predicate(str(value), str(label)) for predicate in predicates):
            result.append(str(value))
    return result


def build_psa_query(
    metadata: dict, *, location_code: str = PSA_COTABATO_CODE, years: list[str] | None = None
) -> tuple[dict, dict]:
    geolocation = _variable(metadata, "geolocation")
    commodity = _variable(metadata, "commodity")
    year = _variable(metadata, "year")
    period = _variable(metadata, "period")

    locations = _matching_values(
        geolocation,
        [
            lambda value, _label: value == location_code,
            lambda _value, label: label.strip(". ").casefold() in {"cotabato", "north cotabato"},
        ],
    )
    commodities = _matching_values(
        commodity,
        [
            lambda _value, label: "palay" in label.casefold()
            and "fancy" in label.casefold()
            and "dry" in label.casefold(),
            lambda _value, label: "palay" in label.casefold()
            and "other variety" in label.casefold()
            and "dry" in label.casefold(),
        ],
    )
    year_pairs = list(
        zip(year.get("values", []), year.get("valueTexts", []), strict=True)
    )
    available_years = [str(label) for _value, label in year_pairs]
    selected_year_labels = (
        available_years if years is None else [y for y in years if y in available_years]
    )
    selected_year_values = [
        str(value) for value, label in year_pairs if str(label) in selected_year_labels
    ]
    periods = _matching_values(
        period, [lambda value, label: "annual" not in value.casefold() + label.casefold()]
    )
    if not locations:
        raise ValidationError(
            f"Cotabato location code {location_code} is unavailable in PSA metadata."
        )
    if len(commodities) < 2:
        raise ValidationError("Both dry fancy and dry other-variety palay are required.")
    if years and len(selected_year_labels) != len(set(years)):
        missing = sorted(set(years) - set(selected_year_labels))
        raise ValidationError(f"Requested PSA years are unavailable: {', '.join(missing)}")

    selections = {
        geolocation["code"]: locations[:1],
        commodity["code"]: commodities,
        year["code"]: selected_year_values,
        period["code"]: periods,
    }
    query = {
        "query": [
            {"code": code, "selection": {"filter": "item", "values": values}}
            for code, values in selections.items()
        ],
        # PXWeb's compact JSON-stat2 response can truncate sparse values. The
        # row-oriented JSON format retains explicit missing-value markers.
        "response": {"format": "json"},
    }
    return query, {
        "available_years": available_years,
        "selected_years": selected_year_labels,
        "location_values": locations[:1],
        "commodity_values": commodities,
    }


def decode_jsonstat2(dataset: dict) -> list[dict]:
    dimension_ids = list(dataset.get("id", []))
    sizes = list(dataset.get("size", []))
    dimensions = dataset.get("dimension", {})
    values = dataset.get("value", [])
    if not dimension_ids or len(dimension_ids) != len(sizes):
        raise ValidationError("PSA returned an unsupported JSON-stat2 dataset.")

    dimension_values = []
    for dimension_id in dimension_ids:
        category = dimensions[dimension_id]["category"]
        index = category.get("index", {})
        if isinstance(index, dict):
            codes = [code for code, _position in sorted(index.items(), key=lambda item: item[1])]
        else:
            codes = list(index)
        labels = category.get("label", {})
        dimension_values.append([(code, labels.get(code, code)) for code in codes])

    records = []
    for flat_index, combination in enumerate(itertools.product(*dimension_values)):
        value = values.get(str(flat_index)) if isinstance(values, dict) else values[flat_index]
        record = {dimension_ids[i]: combination[i][1] for i in range(len(dimension_ids))}
        record["_codes"] = {dimension_ids[i]: combination[i][0] for i in range(len(dimension_ids))}
        record["value"] = value
        records.append(record)
    return records


def _record_field(record: dict, contains: str) -> str:
    for key, value in record.items():
        if key != "_codes" and contains.casefold() in key.casefold():
            return str(value)
    raise ValidationError(f"PSA result is missing the {contains} dimension.")


def decode_pxweb_json(dataset: dict, metadata: dict) -> list[dict]:
    dimensions = [column["code"] for column in dataset.get("columns", []) if column["type"] != "c"]
    lookup = {}
    for variable in metadata.get("variables", []):
        lookup[variable["code"]] = dict(
            zip(variable.get("values", []), variable.get("valueTexts", []), strict=True)
        )
    records = []
    for item in dataset.get("data", []):
        record = {
            dimension: lookup.get(dimension, {}).get(code, code)
            for dimension, code in zip(dimensions, item.get("key", []), strict=True)
        }
        values = item.get("values", [])
        record["value"] = values[0] if values else None
        records.append(record)
    return records


def normalized_psa_rows(dataset: dict, metadata: dict | None = None) -> list[dict]:
    rows = []
    months = {name.casefold(): index for index, name in enumerate(month_name) if name}
    if "data" in dataset:
        if metadata is None:
            raise ValidationError("PSA row-oriented JSON requires its matching metadata.")
        records = decode_pxweb_json(dataset, metadata)
    else:
        records = decode_jsonstat2(dataset)
    for record in records:
        if record["value"] in (None, "", "..", "..."):
            continue
        commodity = _record_field(record, "commodity")
        period = _record_field(record, "period")
        if "annual" in period.casefold():
            continue
        try:
            year = int(_record_field(record, "year"))
            month = months[period.strip().casefold()]
            value = Decimal(str(record["value"]))
        except (ValueError, KeyError, InvalidOperation) as exc:
            raise ValidationError("PSA returned an invalid year, period, or price value.") from exc
        classification = (
            PriceSeries.Classification.FANCY
            if "fancy" in commodity.casefold()
            else PriceSeries.Classification.OTHER
        )
        observed_on = date(year, month, 1)
        if observed_on > timezone.localdate():
            raise ValidationError("PSA observation date cannot be in the future.")
        rows.append(
            {
                "observed_on": observed_on,
                "normalized_price": value.quantize(Decimal("0.0001")),
                "classification": classification,
                "commodity_label": commodity,
                "period_label": period,
            }
        )
    return rows


@transaction.atomic
def ingest_psa_rows(
    rows: list[dict],
    *,
    raw_payload: dict,
    endpoint: str = PSA_CEREALS_API_URL,
    location_code: str = PSA_COTABATO_CODE,
) -> dict:
    payload_bytes = json.dumps(raw_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    checksum = hashlib.sha256(payload_bytes).hexdigest()
    retrieved_at = timezone.now()
    source, _ = Source.objects.get_or_create(
        slug="psa-openstat-cotabato-farmgate",
        defaults={
            "name": "PSA OpenSTAT — Cotabato farmgate prices",
            "source_type": Source.Type.PSA_OPENSTAT,
            "scope": Source.Scope.PROVINCIAL,
            "location_name": "Cotabato",
            "reference_url": endpoint,
            "citation": "Philippine Statistics Authority OpenSTAT, matrix 2M4AFN01.",
            "publication_allowed": True,
            "metadata": {
                "matrix": "2M4AFN01",
                "location_code": location_code,
                "retrieved_at": retrieved_at.isoformat(),
                "checksum_sha256": checksum,
            },
        },
    )
    created = revised = unchanged = 0
    batches = []
    for classification in (PriceSeries.Classification.FANCY, PriceSeries.Classification.OTHER):
        series, _ = PriceSeries.objects.get_or_create(
            slug=f"psa-cotabato-palay-{classification}-dry",
            defaults={
                "name": f"PSA Cotabato dry palay ({classification})",
                "location_name": "Cotabato",
                "location_code": location_code,
                "location_level": PriceSeries.LocationLevel.PROVINCE,
                "classification": classification,
                "condition": PriceSeries.Condition.DRY,
                "moisture_basis": Decimal("14.00"),
                "scope": PriceSeries.Scope.REFERENCE,
                "is_primary_public": classification == PriceSeries.Classification.OTHER,
            },
        )
        series = PriceSeries.objects.select_for_update().get(pk=series.pk)
        if (
            classification == PriceSeries.Classification.OTHER
            and not PriceSeries.objects.filter(
                scope=PriceSeries.Scope.REFERENCE, is_primary_public=True
            ).exists()
        ):
            series.is_primary_public = True
            series.save(update_fields=["is_primary_public"])
        selected = [row for row in rows if row["classification"] == classification]
        batch = ImportBatch.objects.create(
            kind=ImportBatch.Kind.PSA,
            status=ImportBatch.Status.APPROVED,
            series=series,
            source=source,
            original_filename="PSA OpenSTAT 2M4AFN01",
            checksum_sha256=checksum,
            source_payload={
                "endpoint": endpoint,
                "retrieved_at": retrieved_at.isoformat(),
                "checksum_sha256": checksum,
                "record_count": len(selected),
            },
            total_rows=len(selected),
            valid_rows=len(selected),
            approved_rows=len(selected),
            validated_at=retrieved_at,
            completed_at=retrieved_at,
        )
        batches.append(batch)
        for row in selected:
            if row["observed_on"] > timezone.localdate():
                raise ValidationError("PSA observation date cannot be in the future.")
            reference = (
                f"PSA OpenSTAT 2M4AFN01; {row['commodity_label']}; "
                f"{row['period_label']} {row['observed_on'].year}"
            )
            current = (
                PriceObservation.objects.current()
                .verified()
                .filter(series=series, source=source, observed_on=row["observed_on"])
                .first()
            )
            if current and current.normalized_price == row["normalized_price"]:
                unchanged += 1
                continue
            if current:
                PriceObservation.objects.filter(pk=current.pk).update(
                    is_current=False, superseded_at=retrieved_at
                )
            observation = PriceObservation.objects.create(
                series=series,
                source=source,
                import_batch=batch,
                observed_on=row["observed_on"],
                normalized_price=row["normalized_price"],
                original_price=row["normalized_price"],
                original_unit="PHP/kg",
                source_reference=reference,
                collection_method="PSA monthly provincial farmgate price",
                notes=(
                    "Official provincial monthly reference; not a local M'lang weekly observation."
                ),
                workflow_status=PriceObservation.Status.VERIFIED,
                revision=(current.revision + 1 if current else 1),
                supersedes=current,
                verified_at=retrieved_at,
            )
            VerificationEvent.objects.create(
                observation=observation,
                action=VerificationEvent.Action.APPROVED,
                from_status=PriceObservation.Status.PENDING,
                to_status=PriceObservation.Status.VERIFIED,
                reason="Reproducible official-source import.",
            )
            if current:
                VerificationEvent.objects.create(
                    observation=current,
                    action=VerificationEvent.Action.SUPERSEDED,
                    from_status=PriceObservation.Status.VERIFIED,
                    to_status=PriceObservation.Status.VERIFIED,
                    reason=f"PSA revision imported as {observation.public_id}.",
                )
                revised += 1
            else:
                created += 1

    AuditEvent.objects.create(
        action="psa_openstat.imported",
        entity_type=Source._meta.label,
        entity_id=str(source.pk),
        details={
            "created": created,
            "revised": revised,
            "unchanged": unchanged,
            "checksum_sha256": checksum,
            "batch_ids": [str(batch.public_id) for batch in batches],
        },
    )
    return {"created": created, "revised": revised, "unchanged": unchanged, "checksum": checksum}
