# Research method implemented by GABAY

The operational target is a one-week-ahead forecast for a compatible verified M'lang series. Each ISO week uses the median of accepted PHP/kg observations while retaining the range and count.

An eligible experiment uses at least 52 initial training weeks and 12 chronological one-step evaluation folds. All candidates receive identical cutoffs and targets. GABAY compares a last-value naive baseline, fixed four-week Moving Average, Simple Exponential Smoothing, and a small bounded ARIMA candidate set. The champion has the lowest out-of-sample MAE, with RMSE and MAPE as tie-breakers. Prices must remain positive for MAPE.

Forecast reliability is an error band, not a probability of profit. Live forecasts are locked before their target week; retrospective runs are labeled simulations. Later verified observations reconcile the ledger without rewriting the original forecast.

The draft decision table combines expected forecast change, recent trend, price context, freshness, coverage, error, and interval width. Rule values remain versioned and marked draft until adviser approval. Low-quality or conflicting evidence resolves to Monitor.
