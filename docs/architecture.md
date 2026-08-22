# Architecture

GABAY is a Django modular monolith with three bounded applications:

- `prices`: source provenance, compatible series, imports, observations, verification, revisions, and audit history.
- `forecasting`: immutable dataset snapshots, chronological experiments, forecasts, decision-rule versions, recommendations, and evaluation records.
- `portal`: farmer and management journeys, bilingual presentation, accessible SVG charts, and offline caching.

The hosted profile uses PostgreSQL and Gunicorn. The Windows Lite profile uses SQLite with foreign keys, a 5-second busy timeout, WAL journaling, and normal synchronization, served only on `127.0.0.1` through two Waitress threads.

Scientific imports are isolated from ordinary web module imports. Forecast work is bounded to one compatible series and one candidate at a time, with numerical thread counts fixed to one. Public requests never train a model.

Local and hosted databases are independent. Automatic two-way synchronization and a signed research
bundle format are outside v1. Move evidence only through the documented staged CSV workflow or a
supervised database migration; never copy a live SQLite/WAL set into PostgreSQL.
