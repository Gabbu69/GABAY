from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from prices.models import AuditEvent, ImportBatch, ImportRow, PriceObservation, PriceSeries
from prices.services import approve_observation, create_price_observation
from prices.tests.base import PriceTestCase


class PriceManagementViewTests(PriceTestCase):
    def test_dashboard_lists_and_filters_for_authorized_roles(self):
        self.client.force_login(self.encoder)
        response = self.client.get(reverse("prices:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Price data review")
        response = self.client.get(
            reverse("prices:observation-list"), {"status": PriceObservation.Status.PENDING}
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("prices:import-list"))
        self.assertEqual(response.status_code, 200)

    def test_regular_user_cannot_open_evidence_workspace(self):
        user = get_user_model().objects.create_user(username="farmer", password="pass-12345")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("prices:dashboard")).status_code, 403)

    def test_research_admin_explicitly_switches_primary_series(self):
        second = PriceSeries.objects.create(
            name="M'lang fancy dry palay",
            slug="mlang-fancy-dry-palay",
            location_name="M'lang, Cotabato",
            location_level=PriceSeries.LocationLevel.MUNICIPALITY,
            classification=PriceSeries.Classification.FANCY,
            condition=PriceSeries.Condition.DRY,
            scope=PriceSeries.Scope.LOCAL,
        )
        self.client.force_login(self.encoder)
        self.assertEqual(
            self.client.get(reverse("prices:series-selection")).status_code,
            403,
        )
        self.client.force_login(self.research_admin)
        self.assertContains(
            self.client.get(reverse("prices:series-selection")),
            "Choose a primary public series",
        )
        response = self.client.post(
            reverse("prices:series-selection"), {"series": second.pk}
        )
        self.assertEqual(response.status_code, 302)
        self.series.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(self.series.is_primary_public)
        self.assertTrue(second.is_primary_public)
        self.assertTrue(
            AuditEvent.objects.filter(action="price_series.primary_selected").exists()
        )

    def test_manual_entry_approve_and_revision_flow(self):
        self.client.force_login(self.encoder)
        response = self.client.post(
            reverse("prices:observation-create"),
            {
                "series": self.series.pk,
                "source": self.source.pk,
                "observed_on": "2026-08-10",
                "original_price": "20.25",
                "original_unit": "kg",
                "container_weight_kg": "",
                "source_reference": "receipt-view-1",
                "collection_method": "Interview",
                "notes": "Entered in view test",
            },
        )
        self.assertEqual(response.status_code, 302)
        observation = PriceObservation.objects.get(source_reference="receipt-view-1")
        detail_url = reverse("prices:observation-detail", args=[observation.public_id])
        self.assertContains(self.client.get(detail_url), "PHP 20.25")

        self.client.force_login(self.verifier)
        response = self.client.post(
            reverse("prices:observation-approve", args=[observation.public_id]),
            {"reason": "Source checked"},
        )
        self.assertEqual(response.status_code, 302)
        observation.refresh_from_db()
        self.assertEqual(observation.workflow_status, PriceObservation.Status.VERIFIED)

        self.client.force_login(self.encoder)
        response = self.client.post(
            reverse("prices:observation-revise", args=[observation.public_id]),
            {
                "observed_on": "2026-08-10",
                "original_price": "20.50",
                "original_unit": "kg",
                "container_weight_kg": "",
                "source_reference": "receipt-view-1-corrected",
                "collection_method": "Interview",
                "notes": "Correction",
                "reason": "Decimal transcription was corrected",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            PriceObservation.objects.filter(
                supersedes=observation, workflow_status=PriceObservation.Status.PENDING
            ).exists()
        )

    def test_rejection_requires_and_records_reason(self):
        observation = create_price_observation(
            series=self.series,
            source=self.source,
            observed_on=date(2026, 8, 11),
            original_price="19",
            original_unit="kg",
            source_reference="reject-me",
            created_by=self.encoder,
        )
        self.client.force_login(self.verifier)
        response = self.client.post(
            reverse("prices:observation-reject", args=[observation.public_id]),
            {"reason": "Evidence image is unreadable"},
        )
        self.assertEqual(response.status_code, 302)
        observation.refresh_from_db()
        self.assertEqual(observation.workflow_status, PriceObservation.Status.REJECTED)

    def test_csv_template_stage_review_and_error_download(self):
        csv_text = (
            "observation_date,price_value,currency,unit,location_name,location_level,"
            "palay_classification,condition,source_name,source_type,source_reference,"
            "container_weight_kg,collection_method,notes\n"
            "2026-08-10,1000,PHP,sack,\"M'lang, Cotabato\",municipality,other,dry,"
            "M'lang market monitor,municipal_agriculture,view-row-1,50,interview,ok\n"
            "2026-08-11,19,PHP,kg,\"M'lang, Cotabato\",municipality,other,dry,"
            "M'lang market monitor,municipal_agriculture,view-row-2,,interview,ok\n"
        )
        self.client.force_login(self.encoder)
        template_response = self.client.get(reverse("prices:csv-template"))
        self.assertEqual(template_response.status_code, 200)
        self.assertIn("observation_date", template_response.content.decode())
        response = self.client.post(
            reverse("prices:import-create"),
            {
                "series": self.series.pk,
                "source": self.source.pk,
                "csv_file": SimpleUploadedFile(
                    "prices.csv", csv_text.encode(), content_type="text/csv"
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        batch = ImportBatch.objects.get(original_filename="prices.csv")
        detail_url = reverse("prices:import-detail", args=[batch.public_id])
        self.assertContains(self.client.get(detail_url), "Ready for verification", count=2)
        error_response = self.client.get(reverse("prices:import-errors", args=[batch.public_id]))
        self.assertEqual(error_response.status_code, 200)
        self.assertEqual(error_response["Content-Type"], "text/csv; charset=utf-8")

        first, second = list(batch.rows.order_by("row_number"))
        self.client.force_login(self.verifier)
        response = self.client.post(
            reverse(
                "prices:import-row-approve", args=[batch.public_id, first.row_number]
            ),
            {"reason": "Checked"},
        )
        self.assertEqual(response.status_code, 302)
        first.refresh_from_db()
        self.assertEqual(first.status, ImportRow.Status.APPROVED)
        response = self.client.post(
            reverse("prices:import-row-reject", args=[batch.public_id, second.row_number]),
            {"reason": "Source reference needs clarification"},
        )
        self.assertEqual(response.status_code, 302)
        second.refresh_from_db()
        self.assertEqual(second.status, ImportRow.Status.REJECTED)

    def test_maker_cannot_use_approval_endpoint_on_own_record(self):
        observation = create_price_observation(
            series=self.series,
            source=self.source,
            observed_on=date(2026, 8, 12),
            original_price="20",
            original_unit="kg",
            source_reference="dual-role-own",
            created_by=self.dual_role,
        )
        self.client.force_login(self.dual_role)
        response = self.client.post(
            reverse("prices:observation-approve", args=[observation.public_id]),
            {"reason": "Attempted self approval"},
        )
        self.assertEqual(response.status_code, 302)
        observation.refresh_from_db()
        self.assertEqual(observation.workflow_status, PriceObservation.Status.PENDING)

    def test_verified_detail_shows_revision_action(self):
        observation = create_price_observation(
            series=self.series,
            source=self.source,
            observed_on=date(2026, 8, 13),
            original_price="20",
            original_unit="kg",
            source_reference="verified-detail",
            created_by=self.encoder,
        )
        approve_observation(observation, verifier=self.verifier)
        self.client.force_login(self.encoder)
        response = self.client.get(
            reverse("prices:observation-detail", args=[observation.public_id])
        )
        self.assertContains(response, "Create correction")
