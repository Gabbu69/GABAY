# Deployment

## Railway

Create a private shared Railway variable named `GABAY_SECRET_KEY` with a long generated value. The
current Railway Infrastructure as Code file, `.railway/railway.ts`, declares the GitHub-backed web
service and PostgreSQL database, connects `GABAY_DATABASE_URL`, collects static files, applies
migrations on start, starts Gunicorn, and checks `/health/`. The generated Railway public domain is
accepted automatically; add explicit `GABAY_ALLOWED_HOSTS` and `GABAY_CSRF_TRUSTED_ORIGINS` only
for custom domains.

Railway IaC is currently beta and is evaluated by the Railway CLI. Review before applying:

```powershell
railway login
railway link
railway config plan
railway config apply
```

Do not restore `railway.toml`: Config as Code is deprecated for new services and conflicts with the
current project-level IaC flow.

Railpack reads `.python-version`; the repository pins the tested Python 3.12 patch release so the
hosted build does not silently move to a different interpreter line.

Create administrative accounts through a one-off management command. Never commit credentials or database URLs.

Generate the required shared secret locally and paste only the output into Railway's private shared
variable editor:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
```

After deployment, run `setup_price_roles` and `createsuperuser` as one-off commands. In the Django
admin, explicitly select one primary public price series for each published scope. New services fail
closed when the secret, PostgreSQL URL, or primary series is missing.

## Routine research operations

```powershell
# Optional official provincial reference; stays separate from local evidence
.\.venv\Scripts\python.exe manage.py import_psa_openstat --years 2025

# Reconcile locked forecasts once verified target-week evidence is available
.\.venv\Scripts\python.exe manage.py reconcile_forecasts
```

Use the management portal for manual/CSV intake and maker-checker verification. Never edit an
accepted observation in place; create a revision. After a correction or new public observation,
GABAY forces the old result to Monitor until a new experiment is locked.

## Windows Lite release

Run `scripts\build-portable.ps1` on Windows x64 after verification. Publish the generated ZIP and its printed SHA-256 as a GitHub Release asset. Reopen the extracted artifact, create a fresh data directory, and smoke-test direct routes, staged imports, forecasting, restart, backups, and offline behavior before publication.

Each Lite start creates a consistent SQLite backup in `data\backups` before migrations and retains
the seven newest pre-migration copies. Close GABAY before restoring, then run
`Restore-GABAY-Backup.cmd` with one of those `.sqlite3` files. The restore helper preserves the
current database as a separate `gabay-before-restore-*` safety copy first.
