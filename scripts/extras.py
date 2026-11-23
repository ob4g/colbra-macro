"""Monthly crude oil data and news headlines.

Crude exports, monthly FOB value in USD and net weight, HS 2709 crude petroleum:
  - Brazil: ComexStat (Ministry of Development, Industry and Trade), official API.
  - Colombia: UN Comtrade public preview API (figures reported by DANE). It allows one
    month per request and has a daily quota, so history is filled in over a few runs.
  (JODI has exports too, but Colombia stopped reporting them there in 2017.)
Crude production: JODI-Oil World Database, thousand barrels per day.
News: Google News RSS search, one query per country in its own language.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from email.utils import parsedate_to_datetime
from pathlib import Path

JODI_URL = "https://www.jodidata.org/_resources/files/downloads/oil-data/world_primary_csv.zip"
JODI_START = "2010-01"
COUNTRIES = {"CO": "co", "BR": "br"}
FLOWS = {"INDPROD": "production"}


def fetch_jodi(get) -> dict[str, list[list]]:
    """Returns {'co_production': [[YYYY-MM, kbd], ...], 'br_production': ...}."""
    raw = get(JODI_URL)
    out: dict[str, dict[str, float]] = {f"{c}_{f}": {} for c in COUNTRIES.values() for f in FLOWS.values()}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(name) as fh:
            rows = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            for r in rows:
                c = COUNTRIES.get(r["REF_AREA"])
                if (
                    not c
                    or r["ENERGY_PRODUCT"] != "CRUDEOIL"
                    or r["UNIT_MEASURE"] != "KBD"
                    or r["FLOW_BREAKDOWN"] not in FLOWS
                    or r["TIME_PERIOD"] < JODI_START
                ):
                    continue
                try:
                    v = float(r["OBS_VALUE"])
                except ValueError:  # "-", "x", "N/A"
                    continue
                out[f"{c}_{FLOWS[r['FLOW_BREAKDOWN']]}"][r["TIME_PERIOD"]] = round(v, 1)
    if not any(out.values()):
        raise ValueError("no Colombia/Brazil crude rows found")
    return {k: [[m, v] for m, v in sorted(s.items())] for k, s in out.items()}


COMTRADE = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"
COMTRADE_REPORTERS = {"co": 170}
EXPORTS_START = (2012, 1)
RECHECK_MONTHS = 18  # recent months not yet published get retried on every run


def _months(start: tuple[int, int], end: tuple[int, int]):
    y, m = start
    while (y, m) <= end:
        yield f"{y}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


BUDGET = 90  # requests per run; the preview API has a daily quota


def fetch_comexstat(post, start_year: int) -> list[list]:
    """Brazil crude exports from ComexStat, one request per year."""
    out = []
    for y in range(start_year, time.gmtime().tm_year + 1):
        body = {
            "flow": "export", "monthDetail": True,
            "period": {"from": f"{y}-01", "to": f"{y}-12"},
            "filters": [{"filter": "ncm", "values": ["27090010", "27090090"]}],
            "details": [], "metrics": ["metricFOB", "metricKG"],
        }
        res = json.loads(post("https://api-comexstat.mdic.gov.br/general", body))
        for r in res["data"]["list"]:
            out.append([f"{r['year']}-{int(r['monthNumber']):02d}", int(float(r["metricFOB"])), int(float(r["metricKG"]))])
        time.sleep(1)
    return sorted(out)


def fetch_exports(get, prev: dict, post=None) -> dict:
    """Returns {'data': {'co': [[YYYY-MM, fob_usd, net_kg], ...], 'br': ...},
                'checked_from': {'co': 'YYYY-MM', ...}}, merged into prev.

    The preview API allows one month per request, about one request per second and a
    daily quota. So each run first retries recent months that aren't published yet,
    then walks backwards toward EXPORTS_START with what's left of its budget.
    Backfilling the full history therefore takes a few runs.
    """
    today = time.gmtime()
    end = (today.tm_year, today.tm_mon - 1) if today.tm_mon > 1 else (today.tm_year - 1, 12)
    all_months = list(_months(EXPORTS_START, end))
    recent = all_months[-RECHECK_MONTHS:]
    data = {c: {r[0]: r for r in rows} for c, rows in prev.get("data", {}).items()}
    checked = dict(prev.get("checked_from", {}))
    budget, stop = BUDGET, None

    for c in COMTRADE_REPORTERS:
        data.setdefault(c, {})
    # Newest first, alternating countries, so both fill in evenly.
    plan = [(c, m) for m in reversed(recent) for c in COMTRADE_REPORTERS if m not in data[c]]
    plan += [(c, m) for m in reversed(all_months) for c in COMTRADE_REPORTERS if m < checked.get(c, recent[0])]

    for c, m in plan:
        if budget <= 0:
            break
        q = urllib.parse.urlencode({
            "reporterCode": COMTRADE_REPORTERS[c], "period": m.replace("-", ""), "cmdCode": "2709",
            "flowCode": "X", "partnerCode": 0, "partner2Code": 0, "customsCode": "C00", "motCode": 0,
        })
        try:
            rows = json.loads(get(f"{COMTRADE}?{q}")).get("data") or []
        except Exception as e:  # noqa: BLE001  quota or outage: keep what we have
            stop = e
            break
        budget -= 1
        if rows:
            r = rows[0]
            data[c][m] = [m, round(r.get("fobvalue") or r.get("primaryValue") or 0), round(r.get("netWgt") or 0)]
        if m < checked.get(c, recent[0]):
            checked[c] = m
        time.sleep(1.5)

    for c in COMTRADE_REPORTERS:
        checked.setdefault(c, recent[0])
        print(f"  {c}: {len(data[c])} months, checked back to {checked[c]}")
    if stop:
        print(f"  stopped early: {stop}")
    out = {c: [rows[m] for m in sorted(rows)] for c, rows in data.items()}
    try:
        out["br"] = fetch_comexstat(post, EXPORTS_START[0])
        print(f"  br: {len(out['br'])} months (ComexStat)")
    except Exception as e:  # noqa: BLE001
        print(f"  br FAILED (ComexStat): {e}; keeping previous")
        out["br"] = prev.get("data", {}).get("br", [])
    if not any(out.values()):
        raise ValueError(f"no export data yet ({stop})")
    return {"data": out, "checked_from": checked}


NEWS = {
    "co": {
        "q": 'Colombia (petróleo OR Ecopetrol OR "peso colombiano" OR dólar) when:2d',
        "hl": "es-419", "gl": "CO", "ceid": "CO:es-419",
    },
    "br": {
        "q": 'Brasil (petróleo OR Petrobras OR dólar OR câmbio) when:2d',
        "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419",
    },
}
MAX_ITEMS = 6


def _norm(t: str) -> str:
    return re.sub(r"\W+", " ", t.lower()).strip()


def fetch_news(get) -> dict[str, list[dict]]:
    out = {}
    for c, p in NEWS.items():
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(p)
        root = ET.fromstring(get(url))
        items, seen = [], set()
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            src_el = it.find("source")
            source = (src_el.text or "").strip() if src_el is not None else ""
            # Google appends " - Source" to titles; drop it since we show the source separately.
            if source and title.endswith(" - " + source):
                title = title[: -len(source) - 3].rstrip()
            key = _norm(title)[:80]
            if not title or key in seen:
                continue
            seen.add(key)
            try:
                when = parsedate_to_datetime(it.findtext("pubDate") or "").isoformat()
            except (TypeError, ValueError):
                when = None
            items.append({"title": title, "source": source, "url": it.findtext("link"), "published": when})
        items.sort(key=lambda x: x["published"] or "", reverse=True)
        out[c] = items[:MAX_ITEMS]
    if not any(out.values()):
        raise ValueError("no headlines")
    return out


def _post(url: str, body: dict) -> bytes:
    import urllib.request

    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "macro-tracker/1.0"},
    )
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except Exception:  # noqa: BLE001
            if i == 3:
                raise
            time.sleep(2 ** (i + 1))
    raise RuntimeError("unreachable")


def run(get, data: Path, now: str) -> int:
    """Writes data/crude_exports.json, data/crude_production.json and data/news.json.

    Returns the number of failures; a failed source keeps its previous file.
    """
    failures = 0

    def prev_of(name):
        try:
            return json.loads((data / f"{name}.json").read_text())
        except (OSError, ValueError):
            return {}

    jobs = (
        ("crude_exports", lambda g: fetch_exports(g, prev_of("crude_exports"), _post)),
        ("crude_production", fetch_jodi),
        ("news", fetch_news),
    )
    for name, fn in jobs:
        path = data / f"{name}.json"
        try:
            print(f"fetching {name}...")
            body = fn(get)
            # Only news carries a timestamp, so the monthly file changes only when JODI does.
            if name == "news":
                doc = {"fetched": now, "data": body}
            elif "data" in body:  # crude_exports already returns the whole document
                doc = body
            else:
                doc = {"data": body}
            path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
            print("  ok: " + ", ".join(f"{k} {len(v)}" for k, v in doc["data"].items()))
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"  FAILED {name}: {e}; keeping previous file", flush=True)
    return failures
