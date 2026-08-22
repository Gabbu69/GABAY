from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError

from prices.csv_import import stage_csv_import
from prices.models import ImportRow
from prices.services import (
    approve_import_row,
    approve_observation,
    create_price_observation,
)
from prices.tests.base import PriceTestCase

HEADERS = (
    "observation_date,price_value,currency,unit,location_name,location_level,"
    "palay_classification,condition,source_name,source_type,source_reference,"
    "container_weight_kg,collection_method,notes\n"
)


class CSVImportTests(PriceTestCase):
    def test_stages_and_separately_approves_normalized_row(self):
        csv_data = HEADERS + (
            "2026-08-10,1000,PHP,sack,\"M'lang, Cotabato\",municipality,other,dry,"
            "M'lang market monitor,municipal_agriculture,receipt-1,50,interview,checked\n"
        )
        batch = stage_csv_import(
            csv_data,
            series=self.series,
            source=self.source,
            uploaded_by=self.encoder,
        )
        row = batch.rows.get()
        self.assertEqual(row.status, ImportRow.Status.VALID)
        self.assertEqual(row.normalized_price, Decimal("20.0000"))
        observation = approve_import_row(row, verifier=self.verifier)
        self.assertEqual(observation.normalized_price, Decimal("20.0000"))
        row.refresh_from_db()
        self.assertEqual(row.status, ImportRow.Status.APPROVED)

    def test_csv_staging_serializes_assessment_on_series_parent(self):
        csv_data = HEADERS + (
            "2026-08-10,20,PHP,kg,\"M'lang, Cotabato\",municipality,other,dry,"
            "M'lang market monitor,municipal_agriculture,serialized-row,,interview,\n"
        )
        with patch.object(
            self.series.__class__.objects,
            "select_for_update",
            wraps=self.series.__class__.objects.select_for_update,
        ) as lock:
            stage_csv_import(
                csv_data,
                series=self.series,
                source=self.source,
                uploaded_by=self.encoder,
            )
        lock.assert_called()

    def test_missing_container_weight_is_row_error(self):
        csv_data = HEADERS + (
            "2026-08-10,1000,PHP,sack,\"M'lang, Cotabato\",municipality,other,dry,"
            "M'lang market monitor,municipal_agriculture,receipt-1,,interview,\n"
        )
        batch = stage_csv_import(
            csv_data,
            series=self.series,
            source=self.source,
            uploaded_by=self.encoder,
        )
        self.assertEqual(batch.rows.get().status, ImportRow.Status.ERROR)

    def test_exact_existing_evidence_is_collapsed(self):
        observation = create_price_observation(
            series=self.series,
            source=self.source,
            observed_on=date(2026, 8, 10),
            original_price="20",
            original_unit="kg",
            source_reference="receipt-1",
            created_by=self.encoder,
        )
        approve_observation(observation, verifier=self.verifier)
        csv_data = HEADERS + (
            "2026-08-10,20,PHP,kg,\"M'lang, Cotabato\",municipality,other,dry,"
            "M'lang market monitor,municipal_agriculture,receipt-1,,interview,\n"
        )
        batch = stage_csv_import(
            csv_data,
            series=self.series,
            source=self.source,
            uploaded_by=self.encoder,
        )
        row = batch.rows.get()
        self.assertEqual(row.status, ImportRow.Status.DUPLICATE)
        self.assertEqual(row.duplicate_of, observation)

    def test_uploader_cannot_approve_own_row_even_when_dual_role(self):
        csv_data = HEADERS + (
            "2026-08-10,20,PHP,kg,\"M'lang, Cotabato\",municipality,other,dry,"
            "M'lang market monitor,municipal_agriculture,receipt-1,,interview,\n"
        )
        batch = stage_csv_import(
            csv_data,
            series=self.series,
            source=self.source,
            uploaded_by=self.dual_role,
        )
        with self.assertRaisesMessage(ValidationError, "uploader cannot approve"):
            approve_import_row(batch.rows.get(), verifier=self.dual_role)

    def test_csv_publication_permission_must_match_source_record(self):
        headers = HEADERS.replace(
            "collection_method", "source_publication_permission,collection_method"
        )
        csv_data = headers + (
            "2026-08-10,20,PHP,kg,\"M'lang, Cotabato\",municipality,other,dry,"
            "M'lang market monitor,municipal_agriculture,receipt-1,,false,interview,\n"
        )
        batch = stage_csv_import(
            csv_data,
            series=self.series,
            source=self.source,
            uploaded_by=self.encoder,
        )
        row = batch.rows.get()
        self.assertEqual(row.status, ImportRow.Status.ERROR)
        self.assertTrue(
            any(
                error["field"] == "source_publication_permission"
                for error in row.validation_errors
            )
        )

    def test_future_csv_date_is_a_row_error(self):
        csv_data = HEADERS + (
            "2099-01-01,20,PHP,kg,\"M'lang, Cotabato\",municipality,other,dry,"
            "M'lang market monitor,municipal_agriculture,future-row,,interview,\n"
        )
        batch = stage_csv_import(
            csv_data,
            series=self.series,
            source=self.source,
            uploaded_by=self.encoder,
        )
        row = batch.rows.get()
        self.assertEqual(row.status, ImportRow.Status.ERROR)
        self.assertTrue(
            any("future" in error["message"].lower() for error in row.validation_errors)
        )
