import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError

from prices.models import PriceObservation, PriceSeries
from prices.permissions import ENCODER_ROLE, RESEARCH_ADMIN_ROLE, VERIFIER_ROLE
from prices.psa import build_psa_query, ingest_psa_rows, normalized_psa_rows
from prices.tests.base import PriceTestCase


class DemoCommandTests(PriceTestCase):
    def test_fixture_is_deterministic_and_idempotent(self):
        output = StringIO()
        call_command("seed_demo_prices", stdout=output)
        series = PriceSeries.objects.get(slug="demo-mlang-palay-other-dry")
        self.assertEqual(series.scope, PriceSeries.Scope.DEMO)
        self.assertEqual(series.observations.count(), 104)
        first_values = list(
            series.observations.order_by("observed_on").values_list(
                "observed_on", "normalized_price"
            )
        )
        call_command("seed_demo_prices", stdout=output)
        self.assertEqual(series.observations.count(), 104)
        self.assertEqual(
            first_values,
            list(
                series.observations.order_by("observed_on").values_list(
                    "observed_on", "normalized_price"
                )
            ),
        )
        self.assertTrue(
            PriceObservation.objects.filter(notes__icontains="Demonstration data").exists()
        )

    def test_fixture_rejects_ambiguous_end_date(self):
        with self.assertRaisesMessage(CommandError, "must be a Monday"):
            call_command("seed_demo_prices", end_date="2026-08-18")

    def test_role_setup_is_idempotent(self):
        call_command("setup_price_roles", stdout=StringIO())
        call_command("setup_price_roles", stdout=StringIO())
        self.assertEqual(
            Group.objects.filter(
                name__in=[ENCODER_ROLE, VERIFIER_ROLE, RESEARCH_ADMIN_ROLE]
            ).count(),
            3,
        )


class PSAParsingTests(PriceTestCase):
    def setUp(self):
        self.metadata = {
            "variables": [
                {
                    "code": "Geolocation",
                    "text": "Geolocation",
                    "values": ["124700000"],
                    "valueTexts": ["Cotabato"],
                },
                {
                    "code": "Commodity",
                    "text": "Commodity",
                    "values": ["F", "O"],
                    "valueTexts": [
                        "Palay [Paddy] Fancy, dry (conv. to 14% mc)",
                        "Palay [Paddy] Other Variety, dry (conv. to 14% mc)",
                    ],
                },
                {
                    "code": "Year",
                    "text": "Year",
                    "values": ["2025", "2026"],
                    "valueTexts": ["2025", "2026"],
                },
                {
                    "code": "Period",
                    "text": "Period",
                    "values": ["JAN", "ANN"],
                    "valueTexts": ["January", "Annual"],
                },
            ]
        }

    def test_query_discovers_available_years_and_excludes_annual(self):
        query, selection = build_psa_query(self.metadata, years=["2026"])
        self.assertEqual(selection["selected_years"], ["2026"])
        self.assertEqual(query["query"][-1]["selection"]["values"], ["JAN"])

    def test_jsonstat_values_become_monthly_rows(self):
        dataset = {
            "id": ["Geolocation", "Commodity", "Year", "Period"],
            "size": [1, 2, 1, 1],
            "dimension": {
                "Geolocation": {
                    "category": {
                        "index": {"124700000": 0},
                        "label": {"124700000": "Cotabato"},
                    }
                },
                "Commodity": {
                    "category": {
                        "index": {"F": 0, "O": 1},
                        "label": {
                            "F": "Palay Fancy, dry",
                            "O": "Palay Other Variety, dry",
                        },
                    }
                },
                "Year": {
                    "category": {"index": {"2026": 0}, "label": {"2026": "2026"}}
                },
                "Period": {
                    "category": {"index": {"JAN": 0}, "label": {"JAN": "January"}}
                },
            },
            "value": [21.5, 19.25],
        }
        rows = normalized_psa_rows(dataset)
        self.assertEqual(len(rows), 2)
        self.assertEqual(str(rows[0]["normalized_price"]), "21.5000")

    def pxweb_dataset(self, fancy="21.50", other="19.25"):
        return {
            "columns": [
                {"code": "Geolocation", "text": "Geolocation", "type": "d"},
                {"code": "Commodity", "text": "Commodity", "type": "d"},
                {"code": "Year", "text": "Year", "type": "t"},
                {"code": "Period", "text": "Period", "type": "d"},
                {"code": "Farmgate price", "text": "Farmgate price", "type": "c"},
            ],
            "comments": [],
            "data": [
                {"key": ["124700000", "F", "2026", "JAN"], "values": [fancy]},
                {"key": ["124700000", "O", "2026", "JAN"], "values": [other]},
            ],
        }

    def test_row_oriented_data_ingests_without_mixing_reference_scope(self):
        dataset = self.pxweb_dataset()
        rows = normalized_psa_rows(dataset, self.metadata)
        first = ingest_psa_rows(rows, raw_payload={"dataset": dataset})
        self.assertEqual(first["created"], 2)
        self.assertEqual(
            PriceObservation.objects.filter(series__scope=PriceSeries.Scope.REFERENCE).count(),
            2,
        )
        repeated = ingest_psa_rows(rows, raw_payload={"dataset": dataset})
        self.assertEqual(repeated["unchanged"], 2)

        revised_dataset = self.pxweb_dataset(fancy="22.00")
        revised_rows = normalized_psa_rows(revised_dataset, self.metadata)
        revised = ingest_psa_rows(revised_rows, raw_payload={"dataset": revised_dataset})
        self.assertEqual(revised["revised"], 1)
        fancy = PriceSeries.objects.get(slug="psa-cotabato-palay-fancy-dry")
        self.assertEqual(fancy.observations.current().count(), 1)
        self.assertEqual(fancy.observations.count(), 2)

    def test_psa_parser_rejects_future_month(self):
        metadata = json.loads(json.dumps(self.metadata))
        year = next(item for item in metadata["variables"] if item["code"] == "Year")
        year["values"] = ["2099"]
        year["valueTexts"] = ["2099"]
        dataset = self.pxweb_dataset()
        for item in dataset["data"]:
            item["key"][2] = "2099"
        with self.assertRaisesMessage(ValidationError, "future"):
            normalized_psa_rows(dataset, metadata)

    def test_offline_payload_command_supports_dry_run_and_import(self):
        wrapper = {"metadata": self.metadata, "dataset": self.pxweb_dataset()}
        with TemporaryDirectory() as directory:
            payload = Path(directory) / "psa.json"
            payload.write_text(json.dumps(wrapper), encoding="utf-8")
            output = StringIO()
            call_command(
                "import_psa_openstat",
                payload_file=str(payload),
                years="2026",
                dry_run=True,
                stdout=output,
            )
            self.assertIn("Dry run passed", output.getvalue())
            call_command(
                "import_psa_openstat",
                payload_file=str(payload),
                years="2026",
                stdout=output,
            )
        self.assertEqual(
            PriceObservation.objects.filter(series__scope=PriceSeries.Scope.REFERENCE).count(),
            2,
        )

    def test_command_rejects_unavailable_year(self):
        wrapper = {"metadata": self.metadata, "dataset": self.pxweb_dataset()}
        with TemporaryDirectory() as directory:
            payload = Path(directory) / "psa.json"
            payload.write_text(json.dumps(wrapper), encoding="utf-8")
            with self.assertRaisesMessage(CommandError, "unavailable"):
                call_command(
                    "import_psa_openstat",
                    payload_file=str(payload),
                    years="1999",
                )
