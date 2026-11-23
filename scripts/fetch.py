"""Fetch daily FX and Brent series and write them to data/.

Sources (all free, no API key):
  - COP/USD: Banco de la Republica TRM, published on datos.gov.co (dataset 32sa-8pi3)
  - BRL/USD: Banco Central do Brasil SGS, series 1 (PTAX sell rate)
  - Brent:   FRED series DCOILBRENTEU (EIA Europe Brent spot, USD/bbl)

Output:
  data/cop_usd.csv, data/brl_usd.csv, data/brent.csv  (date,value)
  data/series.json                                     (all three, for the site)
  data/crude_exports.json, data/crude_production.json, data/news.json (see extras.py)

A source that fails keeps its previous CSV, so one outage never blanks the site.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import extras

START = date(2010, 1, 1)
DATA = Path(__file__).resolve().parent.parent / "data"
UA = {"User-Agent": "macro-tracker/1.0 (+github actions)", "Cache-Control": "no-cache"}


def get(url: str, tries: int = 4) -> bytes:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            wait = 2 ** (i + 1)
            print(f"  retry {i + 1} for {url} in {wait}s ({e})", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def fetch_cop() -> dict[str, float]:
    """TRM. Each row has a validity window; expand it so weekends carry the last rate."""
    q = urllib.parse.urlencode({
        "$where": f"vigenciadesde >= '{START.isoformat()}T00:00:00'",
        "$order": "vigenciadesde",
        "$limit": "50000",
    })
    rows = json.loads(get(f"https://www.datos.gov.co/resource/32sa-8pi3.json?{q}"))
    out: dict[str, float] = {}
    for r in rows:
        d = r["vigenciadesde"][:10]
        out[d] = round(float(r["valor"]), 2)
    return out


def fetch_brl() -> dict[str, float]:
    """BCB SGS caps daily series at 10 years per request, so ask in 5-year chunks."""
    out: dict[str, float] = {}
    today = date.today()
    lo = START
    while lo <= today:
        hi = min(date(lo.year + 5, 1, 1) - timedelta(days=1), today)
        url = (
            "https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados?formato=json"
            f"&dataInicial={lo:%d/%m/%Y}&dataFinal={hi:%d/%m/%Y}"
        )
        for r in json.loads(get(url)):
            d = datetime.strptime(r["data"], "%d/%m/%Y").date().isoformat()
            out[d] = round(float(r["valor"]), 4)
        lo = hi + timedelta(days=1)
    return out


def fetch_brent() -> dict[str, float]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU&cosd={START.isoformat()}"
    reader = csv.reader(io.StringIO(get(url).decode()))
    next(reader)  # header: observation_date (or DATE), DCOILBRENTEU
    out: dict[str, float] = {}
    for d, v in reader:
        if v not in (".", ""):
            out[d] = round(float(v), 2)
    return out


def read_csv(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    with path.open() as f:
        return {r["date"]: float(r["value"]) for r in csv.DictReader(f)}


def write_csv(path: Path, series: dict[str, float]) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "value"])
        for d in sorted(series):
            w.writerow([d, series[d]])


SOURCES = {
    "cop_usd": (fetch_cop, "COP per USD", "Banco de la República (TRM) via datos.gov.co"),
    "brl_usd": (fetch_brl, "BRL per USD", "Banco Central do Brasil, SGS series 1 (PTAX)"),
    "brent": (fetch_brent, "USD per barrel", "EIA Brent spot via FRED (DCOILBRENTEU)"),
}


def main() -> int:
    DATA.mkdir(exist_ok=True)
    meta_path = DATA / "series.json"
    old_meta = json.loads(meta_path.read_text())["meta"] if meta_path.exists() else {}
    payload: dict = {"meta": {}, "series": {}}
    failures = 0

    for key, (fn, unit, source) in SOURCES.items():
        path = DATA / f"{key}.csv"
        prev = read_csv(path)
        updated = old_meta.get(key, {}).get("fetched")
        try:
            print(f"fetching {key}...")
            new = fn()
            if not new:
                raise ValueError("empty response")
            series = {**prev, **new}
            write_csv(path, series)
            updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
            print(f"  {len(new)} rows, last {max(new)}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"  FAILED {key}: {e}; keeping {len(prev)} cached rows", file=sys.stderr)
            series = prev
        pts = sorted(series.items())
        payload["series"][key] = [[d, v] for d, v in pts]
        payload["meta"][key] = {
            "unit": unit,
            "source": source,
            "fetched": updated,
            "last": pts[-1][0] if pts else None,
        }

    meta_path.write_text(json.dumps(payload, separators=(",", ":")))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    failures += extras.run(get, DATA, now)
    # Fail the job only if every source failed, so partial updates still deploy.
    return 1 if failures == len(SOURCES) + 3 else 0


if __name__ == "__main__":
    sys.exit(main())
