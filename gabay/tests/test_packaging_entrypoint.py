from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch


def _load_entrypoint():
    path = Path(__file__).resolve().parents[2] / "packaging" / "entrypoint.py"
    spec = spec_from_file_location("gabay_packaging_entrypoint", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_worker_forwards_management_command_once():
    entrypoint = _load_entrypoint()

    with (
        patch.object(entrypoint, "_set_lite_environment"),
        patch("django.core.management.execute_from_command_line") as execute,
    ):
        entrypoint._run_forecast_worker(
            ["run_forecast", "demo-series", "--simulation", "--time-limit", "180"]
        )

    execute.assert_called_once_with(
        ["manage.py", "run_forecast", "demo-series", "--simulation", "--time-limit", "180"]
    )
