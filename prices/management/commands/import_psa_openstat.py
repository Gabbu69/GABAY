from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from prices.psa import (
    PSA_CEREALS_API_URL,
    PSA_COTABATO_CODE,
    build_psa_query,
    ingest_psa_rows,
    normalized_psa_rows,
    request_json,
)


class Command(BaseCommand):
    help = "Import separate monthly Cotabato palay reference series from PSA OpenSTAT."

    def add_arguments(self, parser):
        parser.add_argument("--api-url", default=PSA_CEREALS_API_URL)
        parser.add_argument("--location-code", default=PSA_COTABATO_CODE)
        parser.add_argument(
            "--years", help="Comma-separated years; default uses all available years."
        )
        parser.add_argument(
            "--payload-file",
            help="Offline JSON object with metadata and dataset keys, useful for reproducibility.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        years = [value.strip() for value in (options["years"] or "").split(",") if value.strip()]
        try:
            if options["payload_file"]:
                wrapper = json.loads(Path(options["payload_file"]).read_text(encoding="utf-8-sig"))
                metadata = wrapper["metadata"]
                dataset = wrapper["dataset"]
            else:
                metadata = request_json(options["api_url"])
                query, selection = build_psa_query(
                    metadata,
                    location_code=options["location_code"],
                    years=years or None,
                )
                self.stdout.write(
                    f"Retrieving {len(selection['selected_years'])} available year(s) "
                    "for dry fancy and other-variety palay..."
                )
                dataset = request_json(options["api_url"], query)
            if options["payload_file"]:
                build_psa_query(
                    metadata,
                    location_code=options["location_code"],
                    years=years or None,
                )
            rows = normalized_psa_rows(dataset, metadata)
        except (OSError, KeyError, json.JSONDecodeError, HTTPError, URLError) as exc:
            raise CommandError(f"PSA import could not read its source: {exc}") from exc
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        if not rows:
            raise CommandError("PSA returned no usable monthly price observations.")
        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS(f"Dry run passed: {len(rows)} usable values."))
            return
        result = ingest_psa_rows(
            rows,
            raw_payload={"metadata": metadata, "dataset": dataset},
            endpoint=options["api_url"],
            location_code=options["location_code"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"PSA reference import complete: {result['created']} new, "
                f"{result['revised']} revised, {result['unchanged']} unchanged."
            )
        )
