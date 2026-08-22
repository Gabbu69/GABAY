import os
import subprocess
import sys
from pathlib import Path


def test_public_service_import_does_not_load_scientific_stack():
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["DJANGO_SETTINGS_MODULE"] = "gabay.settings"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, django; django.setup(); "
                "import forecasting.services; "
                "assert 'numpy' not in sys.modules; "
                "assert 'scipy' not in sys.modules; "
                "assert 'statsmodels' not in sys.modules"
            ),
        ],
        cwd=project_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
