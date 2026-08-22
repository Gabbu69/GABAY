from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse


def price_fixture():
    local_history = [
        {
            "week_start": date(2026, 8, 3),
            "week_end": date(2026, 8, 9),
            "price": Decimal("21.00"),
            "low": Decimal("20.00"),
            "high": Decimal("22.00"),
            "count": 4,
            "latest_observed_on": date(2026, 8, 8),
        },
        {
            "week_start": date(2026, 8, 10),
            "week_end": date(2026, 8, 16),
            "price": Decimal("22.25"),
            "low": Decimal("21.50"),
            "high": Decimal("23.00"),
            "count": 5,
            "latest_observed_on": date(2026, 8, 15),
        },
    ]
    latest = local_history[-1]
    return {
        "selected_scope": "local",
        "has_verified_local_data": True,
        "latest": latest,
        "history": local_history,
        "series": {"name": "Dry fancy palay"},
        "source": {"name": "M’lang verified field observations"},
        "local": {
            "available": True,
            "latest": latest,
            "history": local_history,
            "source": {"name": "M’lang verified field observations"},
        },
        "reference": {
            "available": True,
            "latest": {**latest, "price": Decimal("20.75")},
            "history": [{**latest, "price": Decimal("20.75")}],
            "source": {"name": "PSA OpenSTAT — Cotabato"},
        },
        "demo": {"available": False, "history": []},
        "is_demo": False,
        "is_stale": False,
        "data_age_days": 7,
        "notice": "Verified local series selected.",
    }


def forecast_fixture(**overrides):
    result = {
        "available": True,
        "status": "available",
        "decision": "WAIT",
        "reliability": "HIGH",
        "point_forecast": Decimal("23.10"),
        "interval_lower": Decimal("21.90"),
        "interval_upper": Decimal("24.30"),
        "current_price": Decimal("22.25"),
        "target_week": date(2026, 8, 24),
        "data_age_days": 7,
        "reason_codes": ["FORECAST_UP", "TREND_UP"],
        "message": "Forecast and recent trend both support waiting.",
        "is_demo": False,
    }
    result.update(overrides)
    return result


class PublicPortalTests(TestCase):
    @patch("portal.views._fetch_forecast_context", return_value=forecast_fixture())
    @patch("portal.views._fetch_price_context", return_value=price_fixture())
    def test_home_renders_evidence_and_accessible_chart(self, _prices, _forecast):
        response = self.client.get(reverse("portal:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Palay prices")
        self.assertContains(response, "₱22.25")
        self.assertContains(response, "WAIT")
        self.assertContains(response, 'role="img"')
        self.assertNotContains(response, "Demonstration data")

    @patch("portal.views._fetch_forecast_context", return_value=forecast_fixture())
    @patch("portal.views._fetch_price_context", return_value={**price_fixture(), "is_demo": True})
    def test_home_labels_synthetic_data_even_outside_development(self, _prices, _forecast):
        response = self.client.get(reverse("portal:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Demonstration data")

    @patch("portal.views._fetch_price_context", return_value=price_fixture())
    def test_history_requires_explicit_reference_selection(self, _prices):
        response = self.client.get(reverse("portal:history"), {"scope": "reference"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Separate official reference")
        self.assertContains(response, "PSA OpenSTAT")
        self.assertContains(response, "₱20.75")

    @patch("portal.views._fetch_price_context", return_value=price_fixture())
    @patch("portal.views._fetch_offer_evaluation", return_value=forecast_fixture())
    def test_offer_check_is_posted_to_stateless_service(self, evaluate, _prices):
        response = self.client.post(reverse("portal:check-offer"), {"offer_price": "22.50"})

        self.assertEqual(response.status_code, 200)
        evaluate.assert_called_once_with(Decimal("22.50"))
        self.assertContains(response, "The ₱22.50 offer results in")
        self.assertContains(response, "NOT SAVED")

    @patch("portal.views._fetch_price_context", return_value=price_fixture())
    def test_offer_check_rejects_nonpositive_value_without_service_call(self, _prices):
        with patch("portal.views._fetch_offer_evaluation") as evaluate:
            response = self.client.post(
                reverse("portal:check-offer"),
                {"offer_price": "0"},
            )

        self.assertEqual(response.status_code, 200)
        evaluate.assert_not_called()
        self.assertContains(response, "Ensure this value is greater than or equal to 0.01")

    def test_language_switch_is_persisted_and_rejects_external_redirect(self):
        response = self.client.post(
            reverse("portal:set-language"),
            {"language": "tl", "next": "https://malicious.invalid/"},
        )

        self.assertRedirects(response, reverse("portal:home"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["gabay_language"], "tl")

        response = self.client.get(reverse("portal:methodology"))
        self.assertContains(response, "Bukas na pamamaraan")

    def test_offline_shell_and_root_scoped_worker_are_available(self):
        offline = self.client.get(reverse("portal:offline"))
        worker = self.client.get(reverse("portal:service-worker"))

        self.assertEqual(offline.status_code, 200)
        self.assertContains(offline, "Treat every price shown offline as stale")
        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker["Content-Type"], "application/javascript")
        self.assertEqual(worker["Service-Worker-Allowed"], "/")
        self.assertContains(worker, 'const PUBLIC_PATHS = new Set(["/"')


class ManagementPortalTests(TestCase):
    def test_management_dashboard_requires_authentication(self):
        response = self.client.get(reverse("portal:manage-dashboard"))

        expected = f"{reverse('portal:login')}?next={reverse('portal:manage-dashboard')}"
        self.assertRedirects(response, expected, fetch_redirect_response=False)

    @patch("portal.views._fetch_forecast_context", return_value=forecast_fixture())
    @patch("portal.views._fetch_price_context", return_value=price_fixture())
    def test_authenticated_user_sees_management_workflows(self, _prices, _forecast):
        user = get_user_model().objects.create_user(username="encoder", password="safe-pass-123")
        user.groups.add(Group.objects.create(name="encoder"))
        self.client.force_login(user)

        response = self.client.get(reverse("portal:manage-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Price records")
        self.assertNotContains(response, "Forecast laboratory")
        self.assertContains(response, "encoder")

    def test_authenticated_user_without_a_role_is_forbidden(self):
        user = get_user_model().objects.create_user(username="viewer", password="safe-pass-123")
        self.client.force_login(user)

        response = self.client.get(reverse("portal:manage-dashboard"))

        self.assertEqual(response.status_code, 403)
