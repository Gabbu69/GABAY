from __future__ import annotations

import os
import secrets
import socket
import sqlite3
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from getpass import getpass
from pathlib import Path


def _set_lite_environment() -> tuple[Path, int]:
    # PyInstaller keeps application code under ``_internal``.  User data must
    # live beside the executable so it survives runtime replacement and stays
    # easy to back up.  A source checkout continues to use the repository root.
    root = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent
    )
    data_dir = Path(os.getenv("GABAY_DATA_DIR", root / "data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in ("backups", "exports", "logs", "uploads"):
        (data_dir / name).mkdir(exist_ok=True)

    secret_file = data_dir / ".secret-key"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gabay.settings")
    os.environ.setdefault("GABAY_PROFILE", "lite")
    os.environ.setdefault("GABAY_DATA_DIR", str(data_dir))
    os.environ.setdefault("GABAY_SECRET_KEY", secret_file.read_text(encoding="utf-8").strip())
    os.environ.setdefault("GABAY_ALLOWED_HOSTS", "127.0.0.1,localhost")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
    return data_dir, int(os.getenv("GABAY_PORT", "8765"))


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _is_gabay_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health/", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def _open_when_ready(url: str) -> None:
    if os.getenv("GABAY_NO_BROWSER") == "1":
        return
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"{url}/health/", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.25)


def _backup_sqlite_before_migration(data_dir: Path) -> Path | None:
    """Create a consistent, bounded pre-migration backup while the app is stopped."""

    database = data_dir / "gabay.sqlite3"
    if not database.exists() or database.stat().st_size == 0:
        return None
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"gabay-pre-migrate-{stamp}-{os.getpid()}.sqlite3"
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as source:
        with sqlite3.connect(target) as destination:
            source.backup(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"Pre-migration backup failed integrity check: {integrity}")

    backups = sorted(
        backup_dir.glob("gabay-pre-migrate-*.sqlite3"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old_backup in backups[7:]:
        if old_backup.resolve().parent == backup_dir.resolve():
            old_backup.unlink()
    print(f"Pre-migration backup: {target}")
    return target


def _ensure_first_run_admin():
    """Create the first local administrator without shipping a default password."""

    from django.contrib.auth import get_user_model
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError

    user_model = get_user_model()
    if user_model.objects.exists():
        return user_model.objects.filter(is_superuser=True).order_by("pk").first()
    if os.getenv("GABAY_SKIP_FIRST_RUN_ADMIN") == "1" or not sys.stdin.isatty():
        print(
            "No staff account exists. Run `python manage.py createsuperuser` "
            "from a source checkout when administrative access is needed."
        )
        return None

    print("\nFirst-run setup: create the private GABAY administrator account.")
    print("The password is not displayed or written to a file.")
    username = input("Administrator username [admin]: ").strip() or "admin"
    while True:
        password = getpass("Administrator password: ")
        confirmation = getpass("Confirm password: ")
        if password != confirmation:
            print("Passwords did not match. Try again.")
            continue
        candidate = user_model(username=username, is_staff=True, is_superuser=True)
        try:
            candidate.full_clean(exclude=("password",))
            validate_password(password, user=candidate)
        except ValidationError as exc:
            print(" ".join(exc.messages))
            continue
        candidate.set_password(password)
        candidate.save()
        print(f"Administrator '{username}' created. Keep this password private.\n")
        return candidate


def _offer_demo_bootstrap(administrator) -> None:
    """Offer an explicitly labeled, optional first-run demonstration."""

    if (
        administrator is None
        or os.getenv("GABAY_SKIP_FIRST_RUN_ADMIN") == "1"
        or not sys.stdin.isatty()
    ):
        return
    from prices.models import PriceObservation, PriceSeries

    if PriceObservation.objects.exists():
        return
    print("GABAY has no price evidence yet.")
    print("A synthetic 104-week demonstration can make the interface immediately explorable.")
    print("It is always labeled DEMONSTRATION and never counts as research evidence.")
    answer = input("Load the demonstration and its sample forecast now? [Y/n]: ").strip().lower()
    if answer not in {"", "y", "yes"}:
        return

    from django.core.management import call_command

    from forecasting.services import run_model_experiment_isolated

    call_command("seed_demo_prices", verbosity=1)
    series = PriceSeries.objects.get(slug="demo-mlang-palay-other-dry")
    try:
        run = run_model_experiment_isolated(
            series,
            user=administrator,
            publish_live=True,
            time_limit_seconds=180,
        )
    except Exception as exc:  # noqa: BLE001 - a failed demo must not block public startup
        print(f"The demo prices were loaded, but the sample forecast failed safely: {exc}")
    else:
        print(f"Demonstration forecast {run.pk} is ready.\n")


def main() -> None:
    data_dir, port = _set_lite_environment()
    url = f"http://127.0.0.1:{port}"
    if _port_is_open(port):
        if _is_gabay_healthy(url):
            webbrowser.open(url)
            print(f"GABAY is already running at {url}")
            return
        raise RuntimeError(
            f"Port {port} is already used by another program. "
            "Close that program or set GABAY_PORT to another local port."
        )

    import django

    _backup_sqlite_before_migration(data_dir)
    django.setup()
    from django.core.management import call_command
    from waitress import serve

    call_command("migrate", interactive=False, verbosity=1)
    call_command("setup_price_roles", verbosity=0)
    administrator = _ensure_first_run_admin()
    _offer_demo_bootstrap(administrator)
    call_command("collectstatic", interactive=False, verbosity=0)
    (data_dir / "gabay.pid").write_text(str(os.getpid()), encoding="ascii")
    threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    print(f"GABAY Lite is starting at {url}")
    print("Keep this window open. Press Ctrl+C to stop the local server.")
    try:
        from gabay.wsgi import application

        serve(application, host="127.0.0.1", port=port, threads=2, ident="GABAY-Lite")
    finally:
        (data_dir / "gabay.pid").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
