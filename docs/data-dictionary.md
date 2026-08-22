# Data dictionary and provenance rules

## Price identity

A forecast-compatible v1 series is identified by location and geographic level, commodity, palay classification, wet/dry condition, moisture basis, normalized unit, and source scope. Weekly local aggregates and monthly provincial reference observations remain separate by scope and presentation; an explicit cadence field is deferred beyond v1.

## Observation fields

Every accepted observation preserves its date, original price and unit, normalized PHP/kg value, location, classification, source, collection method, import/manual provenance, verification state, encoder, verifier, timestamps, and revision relationship.

Nonpositive prices are rejected. A sack or container price requires an explicit kilogram weight; the system never assumes one. Exact duplicate rows may be collapsed with an audit event. Conflicting duplicates and statistical outliers are quarantined for verification rather than silently removed.

## Evidence states

- `DRAFT`: entered but not submitted.
- `PENDING`: awaiting a different verifier.
- `VERIFIED`: eligible for public aggregates and snapshots.
- `REJECTED`: retained with a reason but excluded.
- `SUPERSEDED`: retained as immutable history after a correction.

`LOCAL`, `OFFICIAL_REFERENCE`, and `DEMO` scopes remain distinct in storage, calculations, and user-facing labels.
