# Colombia–Brazil Macro Tracker

A side-by-side dashboard of the Colombian peso, the Brazilian real and Brent crude, rebuilt every weekday by a GitHub Action and published on GitHub Pages.

## What it shows

- Latest COP/USD, BRL/USD and Brent, with the change over 30 days
- Each currency's exchange rate over the selected range (6M to Max, data from 2010)
- Each currency's USD value indexed against Brent (start of range = 100)
- Correlation of daily moves between each currency and Brent

## Data sources (free, no API keys)

| Series | Source |
|---|---|
| COP/USD (TRM) | Banco de la República, via [datos.gov.co](https://www.datos.gov.co/resource/32sa-8pi3.json) |
| BRL/USD (PTAX) | Banco Central do Brasil, [SGS series 1](https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados?formato=json) |
| Brent | EIA spot price, via [FRED DCOILBRENTEU](https://fred.stlouisfed.org/series/DCOILBRENTEU) |

## How it works

```
scripts/fetch.py      -> data/*.csv + data/series.json  (committed by the Action)
site/index.html       -> static page, reads data/series.json (Chart.js)
.github/workflows/update.yml  -> weekdays 23:30 UTC: fetch, commit, deploy to Pages
```

If one source is down, its previous data is kept and the other series still update.

## Setup

1. Settings → Pages → Source: **GitHub Actions**.
2. Actions → "Update data and deploy" → **Run workflow** for the first fetch.

Run locally:

```bash
python scripts/fetch.py
mkdir -p _site && cp -r site/. _site/ && cp -r data _site/data
python -m http.server -d _site 8000
```

## Roadmap

- Monthly oil exports: DANE (Colombia), ComexStat (Brazil)
- Top headlines per country (GDELT)
