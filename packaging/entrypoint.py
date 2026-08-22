from __future__ import annotations

import sys

from gabay.launcher import _set_lite_environment, main


def _run_forecast_worker(arguments: list[str]) -> None:
    """Dispatch a Django command inside the frozen executable.

    The web process starts this mode only for an authorized research request.
    Keeping it in a disposable child lets Windows reclaim the scientific stack
    immediately after the experiment.
    """

    _set_lite_environment()
    from django.core.management import execute_from_command_line

    # ``arguments`` already starts with ``run_forecast`` because the parent
    # service passes the complete Django management command.  Do not prepend
    # it a second time in the frozen process.
    execute_from_command_line(["manage.py", *arguments])

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--forecast-worker":
        _run_forecast_worker(sys.argv[2:])
    else:
        main()
