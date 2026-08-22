from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import override_settings

from prices.models import PriceObservation, PriceSeries, Source, VerificationEvent
from prices.services import (
    approve_observation,
    create_price_observation,
    create_revision,
    get_public_price_context,
    get_weekly_series,
    normalize_price,
    reject_observation,
)
from prices.tests.base import PriceTestCase


class NormalizationTests(PriceTestCase):
    def test_normalizes_kg_and_explicit_container(self):
        self.assertEqual(normalize_price("21.25", unit="kg"), Decimal("21.2500"))
        self.assertEqual(
            normalize_price("1000", unit="sack", container_weight_kg="50"),
            Decimal("20.0000"),
        )

    def test_rejects_guessed_container_and_non_php(self):
        with self.assertRaises(ValidationError):
            normalize_price("1000", unit="sack")
        with self.assertRaises(ValidationError):
            normalize_price("20", unit="kg", currency="USD")


class MakerCheckerTests(PriceTestCase):
    def make_observation(
        self,
        creator=None,
        observed_on=date(2026, 8, 10),
        price="20",
        reference=None,
    ):
        return create_price_observation(
            series=self.series,
            source=self.source,
            observed_on=observed_on,
            original_price=price,
            original_unit="kg",
            source_reference=reference or f"receipt-{observed_on}-{price}",
            collection_method="Market interview",
            created_by=creator or self.encoder,
        )

    def test_different_verifier_can_approve(self):
        observation = self.make_observation()
        approved = approve_observation(observation, verifier=self.verifier, reason="Checked")
        self.assertEqual(approved.workflow_status, PriceObservation.Status.VERIFIED)
        self.assertTrue(approved.is_current)
        self.assertEqual(
            approved.verification_events.last().action, VerificationEvent.Action.APPROVED
        )

    def test_manual_intake_serializes_assessment_on_series_parent(self):
        with patch.object(
            PriceSeries.objects,
            "select_for_update",
            wraps=PriceSeries.objects.select_for_update,
        ) as lock:
            self.make_observation()
        lock.assert_called()

    def test_creator_cannot_self_approve_even_with_both_roles(self):
        observation = self.make_observation(creator=self.dual_role)
        with self.assertRaisesMessage(ValidationError, "maker cannot approve"):
            approve_observation(observation, verifier=self.dual_role)

    def test_evidence_fields_are_immutable(self):
        observation = self.make_observation()
        observation.normalized_price = Decimal("99")
        with self.assertRaisesMessage(ValidationError, "immutable"):
            observation.save()

    def test_series_and_source_identity_lock_after_evidence_intake(self):
        self.make_observation()
        self.series.scope = PriceSeries.Scope.DEMO
        with self.assertRaisesMessage(ValidationError, "compatibility is locked"):
            self.series.save()
        self.source.scope = Source.Scope.DEMO
        self.source.source_type = Source.Type.DEMONSTRATION
        with self.assertRaisesMessage(ValidationError, "identity is locked"):
            self.source.save()

    def test_public_provenance_cannot_be_relabelled_after_intake(self):
        self.make_observation()
        for field, value in (
            ("name", "Relabelled source"),
            ("reference_url", "https://example.com/changed"),
            ("citation", "Changed citation"),
            ("metadata", {"changed": True}),
        ):
            source = Source.objects.get(pk=self.source.pk)
            setattr(source, field, value)
            with self.assertRaisesMessage(ValidationError, "identity is locked"):
                source.save()

    def test_publication_permission_cannot_be_granted_retroactively(self):
        private_source = Source.objects.create(
            name="Private evidence source",
            slug="private-evidence-source",
            source_type=Source.Type.MARKET_INTERVIEW,
            scope=Source.Scope.LOCAL,
            publication_allowed=False,
        )
        create_price_observation(
            series=self.series,
            source=private_source,
            observed_on=date(2026, 8, 9),
            original_price="20",
            original_unit="kg",
            source_reference="private-consent",
            created_by=self.encoder,
        )
        private_source.publication_allowed = True
        with self.assertRaisesMessage(ValidationError, "retroactively"):
            private_source.save()

    def test_second_decision_does_not_append_another_approval(self):
        observation = approve_observation(self.make_observation(), verifier=self.verifier)
        with self.assertRaisesMessage(ValidationError, "Only pending"):
            approve_observation(observation, verifier=self.verifier)
        self.assertEqual(
            observation.verification_events.filter(
                action=VerificationEvent.Action.APPROVED
            ).count(),
            1,
        )

    def test_revision_replaces_verified_value_only_after_approval(self):
        original = approve_observation(self.make_observation(), verifier=self.verifier)
        revision = create_revision(
            original=original,
            created_by=self.encoder,
            observed_on=original.observed_on,
            original_price="21",
            original_unit="kg",
            source_reference="corrected-receipt",
            collection_method="Market interview",
            reason="Transcription correction",
        )
        original.refresh_from_db()
        self.assertTrue(original.is_current)
        self.assertFalse(revision.is_current)
        approve_observation(revision, verifier=self.verifier)
        original.refresh_from_db()
        revision.refresh_from_db()
        self.assertFalse(original.is_current)
        self.assertTrue(revision.is_current)
        self.assertEqual(revision.revision, 2)

    def test_metadata_only_revision_has_a_distinct_content_fingerprint(self):
        original = approve_observation(self.make_observation(), verifier=self.verifier)
        revision = create_revision(
            original=original,
            created_by=self.encoder,
            observed_on=original.observed_on,
            original_price=original.original_price,
            original_unit=original.original_unit,
            source_reference=original.source_reference,
            collection_method="Corrected collection method",
            notes="Metadata-only correction",
            reason="The collection method label was corrected.",
        )
        self.assertNotEqual(original.fingerprint, revision.fingerprint)
        self.assertEqual(revision.normalized_price, original.normalized_price)

    def test_manual_duplicate_is_rejected_and_conflict_is_quarantined(self):
        original = self.make_observation(reference="same-logical-evidence")
        with self.assertRaisesMessage(ValidationError, "duplicate"):
            self.make_observation(reference="same-logical-evidence")
        conflict = self.make_observation(price="21", reference="same-logical-evidence")
        self.assertTrue(conflict.is_conflict)
        with self.assertRaisesMessage(ValidationError, "quarantined"):
            approve_observation(conflict, verifier=self.verifier, reason="Checked")
        with self.assertRaisesMessage(ValidationError, "competing pending"):
            approve_observation(
                conflict,
                verifier=self.verifier,
                reason="Conflicting receipt was reconciled with the source.",
                allow_conflict=True,
            )
        reject_observation(
            original,
            verifier=self.verifier,
            reason="The competing transcription was rejected after source review.",
        )
        approved = approve_observation(
            conflict,
            verifier=self.verifier,
            reason="Conflicting receipt was reconciled with the source.",
            allow_conflict=True,
        )
        self.assertEqual(approved.workflow_status, PriceObservation.Status.VERIFIED)
        original.refresh_from_db()
        self.assertEqual(original.workflow_status, PriceObservation.Status.REJECTED)
        later_conflict = self.make_observation(
            price="22", reference="same-logical-evidence"
        )
        with self.assertRaisesMessage(ValidationError, "submit a revision"):
            approve_observation(
                later_conflict,
                verifier=self.verifier,
                reason="Attempted direct correction.",
                allow_conflict=True,
            )

    def test_extreme_manual_value_is_quarantined_as_outlier(self):
        start = date(2026, 7, 6)
        for index in range(4):
            observation = self.make_observation(
                observed_on=start + timedelta(weeks=index),
                price=str(Decimal("20") + Decimal(index) / 10),
            )
            approve_observation(observation, verifier=self.verifier)
        outlier = self.make_observation(observed_on=date(2026, 8, 10), price="40")
        self.assertTrue(outlier.is_outlier)
        with self.assertRaisesMessage(ValidationError, "outlier is quarantined"):
            approve_observation(outlier, verifier=self.verifier, reason="Checked")
        approve_observation(
            outlier,
            verifier=self.verifier,
            reason="Verified exceptional source evidence.",
            allow_outlier=True,
        )

    def test_future_manual_observation_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "future"):
            self.make_observation(observed_on=date(2099, 1, 1))


class PublicSeriesTests(PriceTestCase):
    def _verified(self, observed_on, price, reference):
        observation = create_price_observation(
            series=self.series,
            source=self.source,
            observed_on=observed_on,
            original_price=price,
            original_unit="kg",
            source_reference=reference,
            created_by=self.encoder,
        )
        return approve_observation(observation, verifier=self.verifier)

    def test_weekly_series_uses_median_range_and_count(self):
        monday = date(2026, 8, 10)
        self._verified(monday, "18", "a")
        self._verified(monday + timedelta(days=2), "22", "b")
        self._verified(monday + timedelta(days=4), "20", "c")
        weeks = get_weekly_series(self.series)
        self.assertEqual(weeks[0]["price"], Decimal("20.0000"))
        self.assertEqual(weeks[0]["low"], Decimal("18.0000"))
        self.assertEqual(weeks[0]["high"], Decimal("22.0000"))
        self.assertEqual(weeks[0]["count"], 3)

    def test_week_and_section_expose_every_contributing_source(self):
        second_source = Source.objects.create(
            name="M'lang cooperative",
            slug="mlang-cooperative",
            source_type=Source.Type.COOPERATIVE,
            scope=Source.Scope.LOCAL,
            location_name="M'lang, Cotabato",
            publication_allowed=True,
        )
        self._verified(date(2026, 8, 10), "20", "market-a")
        second = create_price_observation(
            series=self.series,
            source=second_source,
            observed_on=date(2026, 8, 12),
            original_price="22",
            original_unit="kg",
            source_reference="cooperative-b",
            created_by=self.encoder,
        )
        approve_observation(second, verifier=self.verifier)
        context = get_public_price_context(today=date(2026, 8, 13))
        latest = context["latest"]
        self.assertEqual(latest["source_count"], 2)
        self.assertEqual({source.name for source in latest["sources"]}, {
            "M'lang market monitor",
            "M'lang cooperative",
        })
        self.assertEqual(len(context["sources"]), 2)
        self.assertIsNone(context["source"])

    def test_public_context_requires_explicit_primary_and_exposes_metadata(self):
        self.series.is_primary_public = False
        self.series.save()
        self._verified(date(2026, 8, 10), "20", "not-selected")
        unavailable = get_public_price_context(today=date(2026, 8, 12))
        self.assertFalse(unavailable["local"]["available"])
        self.assertEqual(unavailable["local"]["selection_status"], "no_primary_series")
        self.series.is_primary_public = True
        self.series.save()
        selected = get_public_price_context(today=date(2026, 8, 12))
        self.assertEqual(selected["series_metadata"]["classification"], "other")
        self.assertEqual(selected["series_metadata"]["condition"], "dry")

    def test_only_one_primary_series_is_allowed_per_scope(self):
        with self.assertRaises(ValidationError):
            PriceSeries.objects.create(
                name="Second local series",
                slug="second-local-series",
                location_name="M'lang, Cotabato",
                location_level=PriceSeries.LocationLevel.MUNICIPALITY,
                classification=PriceSeries.Classification.FANCY,
                condition=PriceSeries.Condition.DRY,
                scope=PriceSeries.Scope.LOCAL,
                is_primary_public=True,
            )

    def test_null_moisture_does_not_allow_duplicate_compatible_series(self):
        PriceSeries.objects.create(
            name="Null moisture demo",
            slug="null-moisture-demo",
            location_name="M'lang, Cotabato",
            location_level=PriceSeries.LocationLevel.MUNICIPALITY,
            classification=PriceSeries.Classification.FANCY,
            condition=PriceSeries.Condition.DRY,
            moisture_basis=None,
            scope=PriceSeries.Scope.DEMO,
        )
        with self.assertRaisesMessage(ValidationError, "compatible series"):
            PriceSeries.objects.create(
                name="Duplicate null moisture demo",
                slug="duplicate-null-moisture-demo",
                location_name="M'lang, Cotabato",
                location_level=PriceSeries.LocationLevel.MUNICIPALITY,
                classification=PriceSeries.Classification.FANCY,
                condition=PriceSeries.Condition.DRY,
                moisture_basis=None,
                scope=PriceSeries.Scope.DEMO,
            )

    @override_settings(IS_PRODUCTION=True)
    def test_production_does_not_substitute_demo_or_reference(self):
        demo_source = Source.objects.create(
            name="Demo",
            slug="demo",
            source_type=Source.Type.DEMONSTRATION,
            scope=Source.Scope.DEMO,
            publication_allowed=True,
        )
        demo_series = PriceSeries.objects.create(
            name="Demo series",
            slug="demo-series",
            location_name="M'lang, Cotabato",
            location_level=PriceSeries.LocationLevel.MUNICIPALITY,
            classification=PriceSeries.Classification.UNSPECIFIED,
            condition=PriceSeries.Condition.UNSPECIFIED,
            scope=PriceSeries.Scope.DEMO,
            is_primary_public=True,
        )
        PriceObservation.objects.create(
            series=demo_series,
            source=demo_source,
            observed_on=date(2026, 8, 10),
            normalized_price=Decimal("20"),
            original_price=Decimal("20"),
            source_reference="demo",
            workflow_status=PriceObservation.Status.VERIFIED,
        )
        context = get_public_price_context(today=date(2026, 8, 12))
        self.assertIsNone(context["selected_scope"])
        self.assertTrue(context["demo"]["available"])
        self.assertFalse(context["has_verified_local_data"])

    def test_source_without_publication_permission_is_not_public(self):
        self.source.publication_allowed = False
        self.source.save()
        self._verified(date(2026, 8, 10), "20", "private-evidence")
        context = get_public_price_context(today=date(2026, 8, 12))
        self.assertFalse(context["local"]["available"])

    def test_private_source_is_excluded_from_mixed_public_history(self):
        self._verified(date(2026, 8, 10), "20", "public-evidence")
        private_source = Source.objects.create(
            name="Private interview",
            slug="private-interview",
            source_type=Source.Type.MARKET_INTERVIEW,
            scope=Source.Scope.LOCAL,
            location_name="M'lang, Cotabato",
            publication_allowed=False,
        )
        private = create_price_observation(
            series=self.series,
            source=private_source,
            observed_on=date(2026, 8, 11),
            original_price="99",
            original_unit="kg",
            source_reference="private",
            created_by=self.encoder,
        )
        approve_observation(private, verifier=self.verifier)
        context = get_public_price_context(today=date(2026, 8, 12))
        self.assertEqual(context["latest"]["price"], Decimal("20.0000"))
        self.assertEqual(context["latest"]["count"], 1)
