# GABAY

**GABAY: A Palay Price Forecasting and Decision-Support Framework for M'lang, Cotabato** is a mobile-first thesis application for organizing verified palay farmgate prices, comparing time-series forecasting methods, and presenting explainable **Sell**, **Wait**, or **Monitor** guidance.

GABAY supports farmer judgment. It does not trade palay, guarantee profit, or make an automatic selling decision.

## What the system includes

- Public English/Filipino farmer dashboard with current verified price, history, forecast, uncertainty, source, freshness, and explanation.
- Stateless “Check My Offer” comparison.
- Encoder/verifier administration with CSV staging, row-level validation, maker-checker approval, immutable corrections, and auditing.
- Chronological comparison of naive, Moving Average, Simple Exponential Smoothing, and ARIMA forecasts using MAE, RMSE, and MAPE.
- Versioned decision rules and a forecast-to-observation ledger for thesis evaluation.
- Read-only offline PWA behavior and a Windows Lite profile for a 64-bit Windows 10/11 Pentium laptop with 4 GB RAM.

## Windows Lite quick start

The preferred release requires no Docker, PostgreSQL, XAMPP, Node.js, or globally installed Python.
For a normal GitHub checkout:

```powershell
git clone https://github.com/Gabbu69/GABAY.git
cd GABAY
.\Start-GABAY-Lite.cmd
```

The [Windows Lite v0.1.0 evaluation release](https://github.com/Gabbu69/GABAY/releases/tag/v0.1.0) provides the package used by this launcher. The launcher downloads the SHA-256-verified Windows Lite release on its first run, checks for a
newer release on later starts, then opens GABAY locally. Project data stays outside the replaceable
runtime. On a new database, the console asks you to create the first private administrator;
no default password is shipped or saved to a file. It then offers an optional, explicitly labeled
synthetic demonstration so a fresh checkout is immediately explorable. Alternatively, download the
release directly:

1. Download and extract `GABAY-Windows-x64-Lite.zip` from GitHub Releases.
2. Run `GABAY.exe` and keep its console window open.
3. The application opens at `http://127.0.0.1:8765`.
4. On the target laptop, run `Check-GABAY-Lite.cmd` and keep its JSON acceptance report.

For a source checkout with Python 3.12 x64:

```powershell
.\scripts\setup-lite.ps1
.\Start-GABAY-Lite.cmd
```

The local database and user files stay under `data/`, which is ignored by Git and preserved across `git pull` operations.

## Development

```powershell
$env:GABAY_PYTHON = 'C:\path\to\python3.12.exe'
.\scripts\setup-lite.ps1
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

For a source checkout that did not use the interactive Lite launcher:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py setup_price_roles
.\.venv\Scripts\python.exe manage.py createsuperuser
```

Assign ordinary staff to exactly the roles they need (`encoder`, `verifier`, or
`research_admin`) through `/django-admin/`. A maker cannot verify their own observation.

To explore the system without presenting synthetic values as real evidence:

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo_prices
.\.venv\Scripts\python.exe manage.py run_forecast demo-mlang-palay-other-dry
```

The demo remains visually labeled. For real publication, define the compatible series and sources,
mark exactly one series per scope as the primary public series, collect and independently verify
observations, freeze the adviser-approved rule version, then run the forecast laboratory.

Run verification with:

```powershell
.\scripts\verify.ps1
.\scripts\verify-lite.ps1
```

The second command records startup, route, forecast-time, and process-tree memory measurements in
`reports/runtime/`. Run it again on the actual Lenovo Pentium 4 GB laptop before thesis acceptance;
measurements from a faster development computer do not prove target-hardware performance.

See `docs/` for the data dictionary, research method, acceptance boundary, and deployment guide.

## Evidence boundary

The included demonstration dataset is synthetic and is visibly labeled throughout the product. It is not evidence of real M'lang prices or forecast accuracy. Real recommendations require sufficient verified, classification-compatible local observations. PSA Cotabato monthly prices are stored and displayed as a separate provincial reference series.

## Release distribution

`.github/workflows/release.yml` publishes the ZIP and checksum required by the
launcher when a `v*` tag is pushed. It uses the existing verified Windows build
and extracted-archive smoke test before publication. The first public package was
promoted without rebuilding from the successful exact-source Windows CI artifact
for commit `5bf81cf139c55e810570e549a0a92982703dd91f`. Its ZIP and checksum now
satisfy the launcher's asset contract.

The CI run passed 124 tests and the extracted package's migration, synthetic
forecast, startup, and route checks. A later local launch was blocked before
execution by Windows Application Control (Win32 4551). Do not disable security
settings to run the demonstration: use the Python 3.12 source setup or an
administrator-approved distribution process on managed systems. Fresh-account
and physical 4 GB laptop acceptance remain pending. See the release notes for
the precise source, checksum, and verification boundaries.

The portfolio includes a fictional, read-only forecast journey. It does not
replace a held-out evaluation on independently verified local observations.
