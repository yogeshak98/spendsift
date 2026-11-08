"""Benchmark selection and the blended benchmark.

Pure over a `BenchmarksConfig`: no IO, no network. A benchmark is just an
annual return in percent that the user keeps in config/benchmarks.yaml.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from backend.app.core.errors.errors import BENCHMARK_UNKNOWN
from backend.app.models.storage.benchmarks import EQUITY_SLOT, BenchmarksConfig
from backend.app.persistence.benchmarks_store import BenchmarksStore

BLENDED = "blended"
DEFAULT_KEY = "nifty50"
_PCT = Decimal("0.01")


def load_config(storage_dir: Path) -> BenchmarksConfig:
    """The user's benchmarks file (config/benchmarks.yaml), seeded with the
    defaults on first use. Raises E1097 when the file cannot be used."""
    return BenchmarksStore(storage_dir / "config").load()


def pct_str(value: Decimal) -> str:
    return str(value.quantize(_PCT, rounding=ROUND_HALF_UP))


def default_selection(
    config: BenchmarksConfig, benchmark: str | None, equity_index: str | None
) -> tuple[str, str | None]:
    """The benchmark and equity index to use when the caller named none:
    `nifty50` if the file still has it, else the first configured benchmark
    (the first equity one for the index). With no benchmark at all, the
    selection is `blended` and the index None."""
    if benchmark is None:
        benchmark = (
            DEFAULT_KEY
            if DEFAULT_KEY in config.benchmarks
            else next(iter(config.benchmarks), BLENDED)
        )
    if equity_index is None:
        equity = [k for k, b in config.benchmarks.items() if b.kind == "equity"]
        equity_index = (
            DEFAULT_KEY if DEFAULT_KEY in equity else next(iter(equity), None)
        )
    return benchmark, equity_index


def check_selection(
    config: BenchmarksConfig, selected: str, equity_index: str | None
) -> None:
    """Raise E1096 unless `selected` is a configured benchmark or `blended`, and
    `equity_index` is a configured equity benchmark."""
    if selected != BLENDED and selected not in config.benchmarks:
        raise BENCHMARK_UNKNOWN.exc({"benchmark": selected})
    if equity_index is None:
        return
    index = config.benchmarks.get(equity_index)
    if index is None or index.kind != "equity":
        raise BENCHMARK_UNKNOWN.exc({"benchmark": equity_index})


def _return(config: BenchmarksConfig, key: str) -> Decimal:
    return Decimal(config.benchmarks[key].annual_return_pct)


def vehicle_benchmark_key(
    config: BenchmarksConfig, vehicle_key: str, equity_index: str | None
) -> str | None:
    """The benchmark a vehicle is judged against in the blend (None when the
    vehicle is not in the blend)."""
    target = config.blend.get(vehicle_key)
    if target is None:
        return None
    if target == EQUITY_SLOT:
        return equity_index
    return target


def benchmark_for_vehicle(
    config: BenchmarksConfig, selected: str, equity_index: str | None, vehicle_key: str
) -> tuple[str | None, Decimal | None]:
    """(benchmark key, annual return) the vehicle is compared with: the
    selected benchmark for everyone, or under `blended` the vehicle's own."""
    key = (
        vehicle_benchmark_key(config, vehicle_key, equity_index)
        if selected == BLENDED
        else selected
    )
    if key is None:
        return None, None
    return key, _return(config, key)


def blended_return(
    config: BenchmarksConfig,
    values: dict[str, Decimal],
    equity_index: str | None,
) -> tuple[Decimal | None, list[dict]]:
    """The blended annual return and its weights.

    Each vehicle's benchmark is weighted by its share of current value among
    the vehicles the blend covers (weights sum to 100; a vehicle outside the
    blend is left out, not counted as zero). (None, []) when nothing is
    covered."""
    covered = {
        v: (key, value)
        for v, value in values.items()
        if value > 0
        and (key := vehicle_benchmark_key(config, v, equity_index)) is not None
    }
    total = sum((value for _, value in covered.values()), Decimal(0))
    if total <= 0:
        return None, []
    result = Decimal(0)
    weights: list[dict] = []
    for vehicle, (key, value) in sorted(
        covered.items(), key=lambda kv: kv[1][1], reverse=True
    ):
        weight = value / total
        result += weight * _return(config, key)
        weights.append(
            {
                "vehicle": vehicle,
                "benchmark": key,
                "weight_pct": pct_str(weight * 100),
                "annual_return_pct": pct_str(_return(config, key)),
            }
        )
    return result, weights


def options(config: BenchmarksConfig) -> list[dict]:
    return [
        {
            "key": key,
            "label": b.label,
            "kind": b.kind,
            "annual_return_pct": pct_str(Decimal(b.annual_return_pct)),
        }
        for key, b in config.benchmarks.items()
    ]
