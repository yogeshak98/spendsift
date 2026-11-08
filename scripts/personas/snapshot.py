"""The offline NAV and close-price snapshot, and the demo data-dir seed.

The personas trade real public instruments (`instruments.SCHEMES`, AMFI scheme
codes, and `instruments.TICKERS`, NSE symbols). Their historical values for
every trading day of the period live in two small committed CSVs, so builds
and QA runs never touch the network:

- `tests/fixtures/personas/nav_snapshot.csv`: `scheme_code,date,nav`, from
  mfapi.in (`https://api.mfapi.in/mf/<code>`), which republishes AMFI's daily
  NAVs.
- `tests/fixtures/personas/price_snapshot.csv`: `symbol,date,close`, the NSE
  daily close from Yahoo Finance's public chart endpoint
  (`https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS`), rounded
  to the paisa.

`refresh()` re-fetches both. A developer runs it by hand, once; tests never
call it. Only scheme codes and ticker symbols leave the machine. Responses are
untrusted data: they are parsed as JSON, every value goes through `Decimal`
and a range check, and a trading day with no value is an error rather than a
gap the loaders would paper over.

`seed_data_dir` prepares a fresh app data dir for the personas: the import
templates under `templates/` (CSV/XLSX) and `templates/pdf/`, the NAV
snapshot as `prices/nav_history.parquet`, the exact-NAV history the import
pipeline reads (see `backend/app/service/wiring.py`), and a six-scheme AMFI
scheme master at `scheme_master/amfi_cache.csv`, the app's default
`scheme_master_path`, without which the Groww MF import cannot resolve a
scheme name and refuses the file.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from scripts.personas.instruments import SCHEMES, TICKERS, is_trading_day
from scripts.personas.model import PERIOD_END, PERIOD_START, NavLookup, PriceLookup

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "personas"
NAV_CSV = FIXTURES_DIR / "nav_snapshot.csv"
PRICE_CSV = FIXTURES_DIR / "price_snapshot.csv"
# CSV/XLSX `Template` YAMLs directly inside, `PdfTemplate` YAMLs under `pdf/`.
TEMPLATES_DIR = FIXTURES_DIR / "templates"

NAV_HEADER = ("scheme_code", "date", "nav")
PRICE_HEADER = ("symbol", "date", "close")

# The app's NAV parquet stores decimal128(12, 4); the snapshot keeps the same
# precision so the seeded history and the CSV agree to the last digit.
NAV_PLACES = Decimal("0.0001")
CLOSE_PLACES = Decimal("0.01")
_NAV_RANGE = (Decimal("1"), Decimal("100000"))
_CLOSE_RANGE = (Decimal("1"), Decimal("1000000"))
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun")
_MONTHS += ("Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_IST = timezone(timedelta(hours=5, minutes=30))

# The scheme names AMFI publishes (as mfapi.in's `meta.scheme_name` returned
# them on refresh). The app resolves a Groww order's scheme name against these.
AMFI_NAMES: dict[str, str] = {
    "118834": "Mirae Asset Large & Midcap Fund - Direct Plan - Growth",
    "118989": "HDFC Mid Cap Fund - Direct Plan - Growth Option",
    "120503": "Axis ELSS- Tax Saver Fund - Direct Plan - Growth Option",
    "120716": "UTI Nifty 50 Index Fund - Direct Plan - Growth",
    "122639": "Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
    "125497": "SBI SMALL CAP FUND - Direct Plan - Growth",
}
# All six are equity-oriented schemes (the index fund too) for capital gains.
SCHEME_CATEGORY = "equity"

MFAPI_URL = "https://api.mfapi.in/mf/{code}"
YAHOO_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS"
    "?period1={start}&period2={end}&interval=1d"
)


def trading_days() -> list[date]:
    """Every trading day of the period, in order."""
    days, day = [], PERIOD_START
    while day <= PERIOD_END:
        if is_trading_day(day):
            days.append(day)
        day += timedelta(days=1)
    return days


# --- loaders -----------------------------------------------------------------


def _read(path: Path, header: tuple[str, str, str]) -> dict[tuple[str, date], Decimal]:
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows or tuple(rows[0]) != header:
        raise ValueError(f"{path.name}: header must be {','.join(header)}")
    values: dict[tuple[str, date], Decimal] = {}
    for line, row in enumerate(rows[1:], start=2):
        if len(row) != 3:
            raise ValueError(f"{path.name}:{line}: expected 3 fields")
        key = (row[0], date.fromisoformat(row[1]))
        if key in values:
            raise ValueError(f"{path.name}:{line}: duplicate {key[0]} {key[1]}")
        values[key] = Decimal(row[2])
    return values


def _lookup(values: dict[tuple[str, date], Decimal], what: str):
    def lookup(key: str, on: date) -> Decimal:
        try:
            return values[(key, on)]
        except KeyError:
            raise KeyError(f"no {what} for {key} on {on} in the snapshot") from None

    return lookup


def load_nav() -> NavLookup:
    """(AMFI scheme code, date) -> the NAV published for that day."""
    return _lookup(_read(NAV_CSV, NAV_HEADER), "NAV")


def load_prices() -> PriceLookup:
    """(NSE symbol, date) -> that day's closing price."""
    return _lookup(_read(PRICE_CSV, PRICE_HEADER), "close")


def nav_rows() -> list[tuple[str, date, Decimal]]:
    """The NAV snapshot as sorted (scheme_code, date, nav) rows."""
    values = _read(NAV_CSV, NAV_HEADER)
    return [(code, day, values[(code, day)]) for code, day in sorted(values)]


# --- seeding an app data dir -------------------------------------------------


def _inside(data_dir: Path, target: Path) -> Path:
    resolved = target.resolve()
    if not resolved.is_relative_to(data_dir.resolve()):
        raise ValueError(f"{target} is outside the data dir")
    return target


def write_nav_parquet(path: Path) -> None:
    """The NAV snapshot in the schema of the app's NAV store
    (`backend/app/persistence/nav_store.py`, mirrored here: the store is not
    imported by the persona scripts): scheme_code string, date date32, nav
    decimal128(12, 4), sorted by (scheme_code, date)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = nav_rows()
    nav_type = pa.decimal128(12, 4)
    schema = pa.schema(
        [
            pa.field("scheme_code", pa.string()),
            pa.field("date", pa.date32()),
            pa.field("nav", nav_type),
        ]
    )
    table = pa.table(
        {
            "scheme_code": pa.array([r[0] for r in rows], pa.string()),
            "date": pa.array([r[1] for r in rows], pa.date32()),
            "nav": pa.array([r[2] for r in rows], nav_type),
        },
        schema=schema,
    )
    pq.write_table(table, path)


def scheme_master_text() -> str:
    """The app's 5-column scheme-master cache format,
    `code;name;nav;date;category`, with each scheme's last snapshot NAV."""
    last: dict[str, tuple[date, Decimal]] = {}
    for code, day, nav in nav_rows():
        last[code] = (day, nav)
    lines = []
    for code in sorted(AMFI_NAMES):
        day, nav = last[code]
        # Not strftime("%b"): the month name must not follow the locale.
        stamp = f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"
        lines.append(f"{code};{AMFI_NAMES[code]};{nav};{stamp};{SCHEME_CATEGORY}")
    return "\n".join(lines) + "\n"


def seed_data_dir(data_dir: Path, *, templates: bool = True) -> None:
    """Copy the persona import templates into `<data_dir>/templates/` (and
    `templates/pdf/`), write the NAV snapshot to
    `<data_dir>/prices/nav_history.parquet` and the scheme master to
    `<data_dir>/scheme_master/amfi_cache.csv`. Writes nothing outside
    `data_dir`, and refuses to replace an existing NAV history, scheme master
    or a different template of the same name: it seeds a fresh data dir,
    never a real one. With `templates=False` the import templates are left out
    (the live demo, where the agent proves the formats itself)."""
    if not data_dir.is_dir():
        raise NotADirectoryError(data_dir)
    jobs: list[tuple[Path, Path]] = []
    if templates:
        jobs = [
            (src, data_dir / "templates" / src.name)
            for src in sorted(TEMPLATES_DIR.glob("*.yaml"))
        ] + [
            (src, data_dir / "templates" / "pdf" / src.name)
            for src in sorted((TEMPLATES_DIR / "pdf").glob("*.yaml"))
        ]
    nav_path = _inside(data_dir, data_dir / "prices" / "nav_history.parquet")
    master = _inside(data_dir, data_dir / "scheme_master" / "amfi_cache.csv")
    for path in (nav_path, master):
        if path.exists():
            raise FileExistsError(f"{path} exists; seed a fresh data dir")
    for src, dst in jobs:
        _inside(data_dir, dst)
        if dst.exists() and dst.read_bytes() != src.read_bytes():
            raise FileExistsError(f"{dst} exists with different content")
    for src, dst in jobs:
        _inside(data_dir, dst.parent).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    _inside(data_dir, nav_path.parent).mkdir(parents=True, exist_ok=True)
    write_nav_parquet(nav_path)
    _inside(data_dir, master.parent).mkdir(parents=True, exist_ok=True)
    master.write_text(scheme_master_text(), encoding="utf-8")


# --- refresh (by hand, network) ----------------------------------------------

Fetch = Callable[[str], bytes]


def _http_get(url: str) -> bytes:
    if not url.startswith(
        ("https://api.mfapi.in/", "https://query1.finance.yahoo.com/")
    ):
        raise ValueError(f"refusing to fetch {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(5_000_000)


def _checked(
    value: object, places: Decimal, bounds: tuple[Decimal, Decimal]
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"not a number: {value!r}")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"not a number: {value!r}") from None
    if not number.is_finite() or not bounds[0] <= number <= bounds[1]:
        raise ValueError(f"out of range: {value!r}")
    return number.quantize(places, rounding=ROUND_HALF_UP)


def _fetch_navs(code: str, fetch: Fetch, days: set[date]) -> dict[date, Decimal]:
    payload = json.loads(fetch(MFAPI_URL.format(code=code)))
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict) or str(meta.get("scheme_code")) != code:
        raise ValueError(f"mfapi: response for {code} names another scheme")
    if meta.get("scheme_name") != AMFI_NAMES[code]:
        raise ValueError(f"mfapi: scheme {code} is no longer named as recorded")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError(f"mfapi: no data for {code}")
    out: dict[date, Decimal] = {}
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("date"), str):
            raise ValueError(f"mfapi: malformed row for {code}")
        day = datetime.strptime(item["date"], "%d-%m-%Y").date()
        if day in days:
            out[day] = _checked(item.get("nav"), NAV_PLACES, _NAV_RANGE)
    return out


def _fetch_closes(symbol: str, fetch: Fetch, days: set[date]) -> dict[date, Decimal]:
    start = int(datetime(2025, 9, 25, tzinfo=UTC).timestamp())
    end = int(datetime(2026, 4, 5, tzinfo=UTC).timestamp())
    payload = json.loads(fetch(YAHOO_URL.format(symbol=symbol, start=start, end=end)))
    try:
        result = payload["chart"]["result"][0]
        stamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        volumes = result["indicators"]["quote"][0]["volume"]
        listed = result["meta"]["symbol"]
    except (KeyError, IndexError, TypeError):
        raise ValueError(f"yahoo: malformed response for {symbol}") from None
    if listed != f"{symbol}.NS" or not len(stamps) == len(closes) == len(volumes):
        raise ValueError(f"yahoo: response for {symbol} does not match")
    out: dict[date, Decimal] = {}
    for stamp, close, volume in zip(stamps, closes, volumes):
        if isinstance(stamp, bool) or not isinstance(stamp, int):
            raise ValueError(f"yahoo: bad timestamp for {symbol}")
        day = datetime.fromtimestamp(stamp, _IST).date()
        # A zero-volume bar repeats the previous close on an exchange
        # holiday (15 Jan 2026 is one): no trading, so no close.
        if day in days and close is not None and volume:
            out[day] = _checked(close, CLOSE_PLACES, _CLOSE_RANGE)
    return out


def _render(header: tuple[str, str, str], rows: list[tuple[str, date, Decimal]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for key, day, value in rows:
        writer.writerow([key, day.isoformat(), str(value)])
    return buf.getvalue()


def refresh(fetch: Fetch = _http_get) -> None:
    """Fetch every instrument's value for every trading day of the period and
    rewrite both snapshot CSVs. Raises, writing nothing, when any trading day
    has no value or any value fails validation."""
    days = trading_days()
    wanted = set(days)
    navs: list[tuple[str, date, Decimal]] = []
    closes: list[tuple[str, date, Decimal]] = []
    missing: list[str] = []
    for code in sorted(SCHEMES):
        got = _fetch_navs(code, fetch, wanted)
        missing += [f"NAV {code} {d}" for d in days if d not in got]
        navs += [(code, d, got[d]) for d in days if d in got]
    for symbol in sorted(TICKERS):
        got = _fetch_closes(symbol, fetch, wanted)
        missing += [f"close {symbol} {d}" for d in days if d not in got]
        closes += [(symbol, d, got[d]) for d in days if d in got]
    if missing:
        raise ValueError("no value for: " + ", ".join(missing))
    NAV_CSV.write_text(_render(NAV_HEADER, navs), encoding="utf-8")
    PRICE_CSV.write_text(_render(PRICE_HEADER, closes), encoding="utf-8")


if __name__ == "__main__":
    refresh()
