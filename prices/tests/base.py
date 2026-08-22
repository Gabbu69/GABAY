from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from prices.models import PriceSeries, Source
from prices.permissions import ENCODER_ROLE, RESEARCH_ADMIN_ROLE, VERIFIER_ROLE


class PriceTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        encoder_group = Group.objects.create(name=ENCODER_ROLE)
        verifier_group = Group.objects.create(name=VERIFIER_ROLE)
        research_group = Group.objects.create(name=RESEARCH_ADMIN_ROLE)
        cls.encoder = user_model.objects.create_user(username="encoder", password="pass-12345")
        cls.encoder.groups.add(encoder_group)
        cls.verifier = user_model.objects.create_user(username="verifier", password="pass-12345")
        cls.verifier.groups.add(verifier_group)
        cls.dual_role = user_model.objects.create_user(username="dual", password="pass-12345")
        cls.dual_role.groups.add(encoder_group, verifier_group)
        cls.research_admin = user_model.objects.create_user(
            username="research-admin", password="pass-12345"
        )
        cls.research_admin.groups.add(research_group)
        cls.source = Source.objects.create(
            name="M'lang market monitor",
            slug="mlang-market-monitor",
            source_type=Source.Type.MUNICIPAL_AGRICULTURE,
            scope=Source.Scope.LOCAL,
            location_name="M'lang, Cotabato",
            publication_allowed=True,
        )
        cls.series = PriceSeries.objects.create(
            name="M'lang dry palay other variety",
            slug="mlang-dry-palay-other",
            location_name="M'lang, Cotabato",
            location_code="MLANG",
            location_level=PriceSeries.LocationLevel.MUNICIPALITY,
            classification=PriceSeries.Classification.OTHER,
            condition=PriceSeries.Condition.DRY,
            moisture_basis=Decimal("14.00"),
            scope=PriceSeries.Scope.LOCAL,
            is_primary_public=True,
        )
