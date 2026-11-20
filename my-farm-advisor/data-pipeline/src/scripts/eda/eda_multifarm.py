#!/usr/bin/env python3
"""eda_multifarm.py - cross-grower field-level exploratory analysis.

Reads real field boundaries, NASA POWER weather tables, and USDA CDL history
for a set of grower/farm pairs and writes static figures, tidy CSV tables, and a
reproducibility manifest. It does not assemble a report.

Usage:
  export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/runtime
  cd "$DATA_PIPELINE_DATA_ROOT/data-pipeline/src"
  .venv/bin/python scripts/eda/eda_multifarm.py \
      --farm il-grower/illinois-demo-farm:IL \
      --farm ia-grower/iowa-demo-farm:IA \
      --farm ne-grower/nebraska-demo-farm:NE \
      --out "$DATA_PIPELINE_DATA_ROOT/data-pipeline/eda/assignment-2" \
      --check-inputs

Add --units us to write tables and figures in U.S. customary units. The
calculations stay in the metric source; conversion happens at presentation only.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import ticker
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from pyproj import Transformer
from scipy import stats

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR / "lib"))

from lib.paths import (  # noqa: E402
    farm_boundary_path,
    farm_cdl_rotation_path,
    farm_table_path,
    farm_weather_path,
    shared_geoadmin_counties_dir,
    shared_geoadmin_states_dir,
)

PALETTE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9"]
CROP_COLORS = {"Corn": "#E69F00", "Soybeans": "#56B4E9", "Other": "#B7B7B7"}
YEAR_COLORS = ["#3B4CC0", "#6E8FD4", "#9FB8E8", "#E8B36C", "#C25E3A"]
GROWER_ORDER: list[str] = []
GROWER_COLORS: dict[str, str] = {}

# --------------------------------------------------------------------------
# Units
#
# All calculations stay in the source metric. Unit conversion is applied only
# when tables and figures are written, so the statistical tests are identical
# under either presentation and the metric run remains reproducible.
# --------------------------------------------------------------------------

HA_TO_ACRES = 2.471053814671653
KM_TO_MILES = 0.621371192237334
MM_TO_INCHES = 1.0 / 25.4
MS_TO_MPH = 2.2369362920544
C_TO_F_SCALE = 9.0 / 5.0
C_TO_F_OFFSET = 32.0
# One degree-day Fahrenheit equals 5/9 degree-day Celsius. A slope reported
# per 100 GDD_C becomes a slope per 100 GDD_F by multiplying by (5/9).
MM_PER_100GDD_TO_IN_PER_100GDD_F = (1.0 / 25.4) * (5.0 / 9.0)


@dataclass(frozen=True)
class UnitSystem:
    key: str
    area: str
    area_short: str
    distance: str
    distance_short: str
    precip: str
    precip_short: str
    temp: str
    temp_short: str
    gdd: str
    gdd_short: str
    wind: str
    wind_short: str
    area_factor: float
    distance_factor: float
    precip_factor: float
    wind_factor: float
    gdd_factor: float
    temp_scale: float
    temp_offset: float

    @property
    def is_us(self) -> bool:
        return self.key == "us"

    def temperature(self, celsius: float) -> float:
        return celsius * self.temp_scale + self.temp_offset


METRIC_UNITS = UnitSystem(
    key="metric",
    area="hectares",
    area_short="ha",
    distance="kilometres",
    distance_short="km",
    precip="millimetres",
    precip_short="mm",
    temp="degrees Celsius",
    temp_short="°C",
    gdd="degree-days Celsius",
    gdd_short="°C·d",
    wind="metres per second",
    wind_short="m/s",
    area_factor=1.0,
    distance_factor=1.0,
    precip_factor=1.0,
    wind_factor=1.0,
    gdd_factor=1.0,
    temp_scale=1.0,
    temp_offset=0.0,
)

US_UNITS = UnitSystem(
    key="us",
    area="acres",
    area_short="ac",
    distance="miles",
    distance_short="mi",
    precip="inches",
    precip_short="in",
    temp="degrees Fahrenheit",
    temp_short="°F",
    gdd="degree-days Fahrenheit",
    gdd_short="°F·d",
    wind="miles per hour",
    wind_short="mph",
    area_factor=HA_TO_ACRES,
    distance_factor=KM_TO_MILES,
    precip_factor=MM_TO_INCHES,
    wind_factor=MS_TO_MPH,
    gdd_factor=C_TO_F_SCALE,
    temp_scale=C_TO_F_SCALE,
    temp_offset=C_TO_F_OFFSET,
)

UNITS: UnitSystem = METRIC_UNITS


def set_units(key: str) -> UnitSystem:
    """Select the presentation unit system (calculations stay metric)."""
    global UNITS
    UNITS = US_UNITS if key == "us" else METRIC_UNITS
    return UNITS


def _convert_columns(frame: pd.DataFrame, specs) -> pd.DataFrame:
    """Rename and rescale columns in place on a copy.

    Each spec is (source, destination, factor, offset). Offset is applied after
    the multiplicative factor and is used only for absolute temperatures.
    """
    converted = frame.copy()
    for source, destination, factor, offset in specs:
        if source not in converted.columns:
            continue
        converted[destination] = converted[source].astype(float) * factor + offset
        if destination != source:
            converted = converted.drop(columns=[source])
    return converted


def _map_metric_labels(series: pd.Series) -> pd.Series:
    if not UNITS.is_us:
        return series
    return series.replace(
        {
            "gdd": "gdd_f",
            "precip_mm": "precip_in",
            "max_30d_precip": "max_30d_precip_in",
        }
    )


def _metric_factor(metric: str) -> float:
    if metric == "gdd":
        return UNITS.gdd_factor
    if metric in {"precip_mm", "max_30d_precip"}:
        return UNITS.precip_factor
    return 1.0


def presentation_table(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    """Return a presentation copy of a table in the selected unit system."""
    if not UNITS.is_us or frame.empty:
        return frame

    if name == "boundary_fields.csv":
        return _convert_columns(
            frame,
            [
                ("area_ha", "area_ac", UNITS.area_factor, 0.0),
                ("perimeter_km", "perimeter_mi", UNITS.distance_factor, 0.0),
            ],
        )
    if name == "boundary_size_stats.csv":
        return _convert_columns(
            frame,
            [
                ("mean_ha", "mean_ac", UNITS.area_factor, 0.0),
                ("median_ha", "median_ac", UNITS.area_factor, 0.0),
                ("median_ci_low", "median_ci_low_ac", UNITS.area_factor, 0.0),
                ("median_ci_high", "median_ci_high_ac", UNITS.area_factor, 0.0),
                ("q1_ha", "q1_ac", UNITS.area_factor, 0.0),
                ("q3_ha", "q3_ac", UNITS.area_factor, 0.0),
                ("sd_ha", "sd_ac", UNITS.area_factor, 0.0),
                ("min_ha", "min_ac", UNITS.area_factor, 0.0),
                ("max_ha", "max_ac", UNITS.area_factor, 0.0),
            ],
        )
    if name in {"boundary_size_test.csv", "boundary_shape_corr.csv", "weather_grower_tests.csv"}:
        converted = frame.copy()
        for column in ("metric", "metric_x"):
            if column in converted.columns:
                converted[column] = converted[column].replace({"area_ha": "area_ac"})
        if name == "weather_grower_tests.csv":
            converted["metric"] = _map_metric_labels(converted["metric"])
        return converted
    if name == "boundary_spatial.csv":
        return _convert_columns(
            frame,
            [
                (column, column.replace("_km", "_mi"), UNITS.distance_factor, 0.0)
                for column in frame.columns
                if column.endswith("_km")
            ],
        )
    if name == "cdl_area_mix.csv":
        return _convert_columns(
            frame, [("crop_area_ha", "crop_area_ac", UNITS.area_factor, 0.0)]
        )
    if name in {"cdl_primary_crop.csv", "map_field_index.csv"}:
        return _convert_columns(
            frame, [("area_ha", "area_ac", UNITS.area_factor, 0.0)]
        )
    if name == "weather_seasonal_metrics.csv":
        return _convert_columns(
            frame,
            [
                ("gdd", "gdd_f", UNITS.gdd_factor, 0.0),
                ("precip_mm", "precip_in", UNITS.precip_factor, 0.0),
                ("tmean_c", "tmean_f", UNITS.temp_scale, UNITS.temp_offset),
                ("tmax_mean_c", "tmax_mean_f", UNITS.temp_scale, UNITS.temp_offset),
                ("tmin_mean_c", "tmin_mean_f", UNITS.temp_scale, UNITS.temp_offset),
                ("max_30d_precip", "max_30d_precip_in", UNITS.precip_factor, 0.0),
                ("wind_mean", "wind_mean_mph", UNITS.wind_factor, 0.0),
            ],
        )
    if name == "weather_grower_year.csv":
        return _convert_columns(
            frame,
            [
                ("gdd_mean", "gdd_f_mean", UNITS.gdd_factor, 0.0),
                ("gdd_sd", "gdd_f_sd", UNITS.gdd_factor, 0.0),
                ("gdd_anomaly", "gdd_f_anomaly", UNITS.gdd_factor, 0.0),
                ("precip_mean", "precip_in_mean", UNITS.precip_factor, 0.0),
                ("precip_sd", "precip_in_sd", UNITS.precip_factor, 0.0),
                ("precip_anomaly", "precip_in_anomaly", UNITS.precip_factor, 0.0),
            ],
        )
    if name == "weather_grower_stats.csv":
        converted = frame.copy()
        converted["metric"] = _map_metric_labels(converted["metric"])
        for index, metric in frame["metric"].items():
            factor = _metric_factor(metric)
            if factor != 1.0:
                for column in ("mean", "sd", "min", "max"):
                    converted.loc[index, column] = float(frame.loc[index, column]) * factor
        return converted
    if name == "weather_year_summary.csv":
        return _convert_columns(
            frame,
            [
                ("gdd_mean", "gdd_f_mean", UNITS.gdd_factor, 0.0),
                ("precip_mean", "precip_in_mean", UNITS.precip_factor, 0.0),
                ("precip_sd", "precip_in_sd", UNITS.precip_factor, 0.0),
            ],
        )
    if name == "weather_year_variability.csv":
        converted = frame.copy()
        converted["metric"] = _map_metric_labels(converted["metric"])
        for index, metric in frame["metric"].items():
            factor = _metric_factor(metric)
            if factor != 1.0:
                for column in ("year_mean", "year_sd", "min_year_mean", "max_year_mean"):
                    converted.loc[index, column] = float(frame.loc[index, column]) * factor
            converted.loc[index, "year_cv_pct"] = float(frame.loc[index, "year_cv_pct"])
        return converted
    if name == "weather_variance_components.csv":
        converted = frame.copy()
        converted["metric"] = _map_metric_labels(converted["metric"])
        for index, metric in frame["metric"].items():
            linear = _metric_factor(metric)
            if linear != 1.0:
                for column in (
                    "ss_total",
                    "ss_grower",
                    "ss_field_within_grower",
                    "ss_year",
                    "ss_residual",
                ):
                    converted.loc[index, column] = float(frame.loc[index, column]) * linear**2
        return converted
    if name == "weather_gdd_precip_corr.csv":
        return _convert_columns(
            frame,
            [
                (
                    "slope_mm_per_100gdd",
                    "slope_in_per_100f_gdd",
                    MM_PER_100GDD_TO_IN_PER_100GDD_F,
                    0.0,
                )
            ],
        )
    return frame


def write_table(frame: pd.DataFrame, path: Path) -> None:
    """Write a table after applying the selected presentation units."""
    presentation_table(frame, path.name).to_csv(path, index=False)


@dataclass(frozen=True)
class FarmSpec:
    grower: str
    farm: str
    label: str

    @property
    def key(self) -> str:
        return f"{self.grower}/{self.farm}"


# --------------------------------------------------------------------------
# CLI and loading
# --------------------------------------------------------------------------


def parse_farm(value: str) -> FarmSpec:
    label = ""
    if ":" in value:
        value, label = value.rsplit(":", 1)
    parts = value.split("/")
    if len(parts) != 2 or not all(parts):
        raise argparse.ArgumentTypeError(
            f"expected grower/farm[:LABEL], got {value!r}"
        )
    grower, farm = parts
    return FarmSpec(grower=grower, farm=farm, label=label or farm)


def parse_md(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{2})-(\d{2})", value.strip())
    if not match:
        raise argparse.ArgumentTypeError(f"expected MM-DD, got {value!r}")
    month, day = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        raise argparse.ArgumentTypeError(f"invalid month-day, got {value!r}")
    return month, day


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_weather_path(spec: FarmSpec, start_year: int, end_year: int) -> Path:
    candidate = farm_weather_path(spec.grower, spec.farm, start_year, end_year)
    if candidate.exists():
        return candidate
    tables = farm_table_path(spec.grower, spec.farm, "_").parent
    matches = sorted(tables.glob("*_weather_*.csv"))
    if not matches:
        raise FileNotFoundError(f"no weather table under {tables}")
    return matches[0]


def load_boundary(spec: FarmSpec) -> gpd.GeoDataFrame:
    path = farm_boundary_path(spec.grower, spec.farm)
    if not path.exists():
        raise FileNotFoundError(f"missing boundary: {path}")
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    gdf = gdf.to_crs("EPSG:4326")
    if "field_id" not in gdf.columns:
        raise ValueError(f"boundary lacks field_id: {path}")
    if gdf["field_id"].duplicated().any():
        dupes = gdf.loc[gdf["field_id"].duplicated(), "field_id"].tolist()
        raise ValueError(f"duplicate field_id in {path}: {dupes}")

    proj = gdf.to_crs("EPSG:5070")
    area_m2 = proj.geometry.area.to_numpy()
    perim_m = proj.geometry.length.to_numpy()
    bounds = proj.bounds
    bbox_area = (bounds["maxx"] - bounds["minx"]) * (bounds["maxy"] - bounds["miny"])

    gdf["area_ha"] = area_m2 / 10_000.0
    gdf["perimeter_km"] = perim_m / 1_000.0
    gdf["polsby_popper"] = np.divide(
        4.0 * np.pi * area_m2,
        perim_m**2,
        out=np.zeros_like(area_m2),
        where=perim_m > 0,
    )
    gdf["bbox_fill"] = np.divide(
        area_m2, bbox_area.to_numpy(), out=np.zeros_like(area_m2), where=bbox_area > 0
    )
    gdf["n_parts"] = proj.geometry.apply(
        lambda geom: len(geom.geoms) if geom.geom_type.startswith("Multi") else 1
    )
    centroids = proj.geometry.centroid.to_crs("EPSG:4326")
    gdf["centroid_lat"] = centroids.y
    gdf["centroid_lon"] = centroids.x
    gdf["grower"] = spec.label
    gdf["_grower_slug"] = spec.grower
    gdf["_farm_slug"] = spec.farm
    gdf["_proj_geometry"] = proj.geometry
    for column, fallback in (("county", ""), ("state", ""), ("state_fips", "")):
        if column in gdf.columns:
            gdf[column] = gdf[column].fillna(fallback).astype(str)
        else:
            gdf[column] = fallback
    gdf["state_label"] = gdf["state"]

    stored = pd.to_numeric(gdf.get("area_ha_est"), errors="coerce")
    if stored is not None and stored.notna().any():
        rel = ((gdf["area_ha"] - stored).abs() / stored).dropna()
        if not rel.empty and float(rel.max()) > 0.005:
            raise ValueError(
                f"{spec.key}: recomputed area differs from area_ha_est by "
                f"{float(rel.max()):.4%}; refusing to continue"
            )
    return gdf


def load_weather(spec: FarmSpec, start_year: int, end_year: int) -> pd.DataFrame:
    path = find_weather_path(spec, start_year, end_year)
    weather = pd.read_csv(path, parse_dates=["date"])
    required = {
        "field_id",
        "date",
        "T2M",
        "T2M_MAX",
        "T2M_MIN",
        "PRECTOTCORR",
        "ALLSKY_SFC_SW_DWN",
        "RH2M",
        "WS10M",
    }
    missing = required.difference(weather.columns)
    if missing:
        raise ValueError(f"{path} missing columns: {sorted(missing)}")
    weather["field_id"] = weather["field_id"].astype(str)
    weather["grower"] = spec.label
    weather["_source"] = str(path)
    return weather


def load_cdl(spec: FarmSpec) -> tuple[pd.DataFrame, Path]:
    tables = farm_table_path(spec.grower, spec.farm, "_").parent
    files = sorted(tables.glob("*_20??_cdl.csv"))
    pattern = re.compile(r"_(\d{4})_cdl\.csv$")
    files = [p for p in files if pattern.search(p.name)]
    if not files:
        raise FileNotFoundError(f"no per-year CDL tables under {tables}")
    frames = []
    for path in files:
        year = int(pattern.search(path.name).group(1))
        frame = pd.read_csv(path)
        if "year" in frame.columns:
            frame["year"] = pd.to_numeric(frame["year"], errors="coerce").fillna(year)
        else:
            frame["year"] = year
        frame["year"] = frame["year"].astype(int)
        frames.append(frame)
    cdl = pd.concat(frames, ignore_index=True)
    required = {"field_id", "year", "crop_name", "pct"}
    missing = required.difference(cdl.columns)
    if missing:
        raise ValueError(f"CDL tables missing columns: {sorted(missing)}")
    cdl["field_id"] = cdl["field_id"].astype(str)
    cdl["crop_name"] = cdl["crop_name"].fillna("Unknown").astype(str)
    cdl["pct"] = pd.to_numeric(cdl["pct"], errors="coerce").fillna(0.0)
    cdl["pixel_count"] = pd.to_numeric(
        cdl.get("pixel_count", 0), errors="coerce"
    ).fillna(0)
    cdl["grower"] = spec.label
    return cdl, tables


def load_rotation(spec: FarmSpec) -> pd.DataFrame:
    path = farm_cdl_rotation_path(spec.grower, spec.farm)
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    frame["grower"] = spec.label
    frame["field_id"] = frame["field_id"].astype(str)
    return frame


# --------------------------------------------------------------------------
# Numeric helpers
# --------------------------------------------------------------------------


def season_mask(dates: pd.Series, start_md: int, end_md: int) -> pd.Series:
    month_day = dates.dt.month * 100 + dates.dt.day
    return (month_day >= start_md) & (month_day <= end_md)


def longest_true_run(values: np.ndarray) -> int:
    best = current = 0
    for value in values:
        current = current + 1 if value else 0
        best = max(best, current)
    return best


def gini(values: np.ndarray) -> float:
    x = np.sort(np.asarray(values, dtype=float))
    n = len(x)
    if n == 0 or x.sum() == 0:
        return float("nan")
    index = np.arange(1, n + 1)
    return float((2.0 * (index * x).sum()) / (n * x.sum()) - (n + 1) / n)


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if not len(a) or not len(b):
        return float("nan")
    greater = float((a[:, None] > b[None, :]).sum())
    lower = float((a[:, None] < b[None, :]).sum())
    return (greater - lower) / (len(a) * len(b))


def holm(pvalues: list[float]) -> list[float]:
    if not pvalues:
        return []
    order = np.argsort(pvalues)
    adjusted = np.empty(len(pvalues), dtype=float)
    previous = 0.0
    for rank, index in enumerate(order):
        value = (len(pvalues) - rank) * pvalues[index]
        previous = max(previous, value)
        adjusted[index] = min(1.0, previous)
    return adjusted.tolist()


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054):
    if total <= 0:
        return (float("nan"), float("nan"))
    phat = successes / total
    denom = 1 + z**2 / total
    centre = (phat + z**2 / (2 * total)) / denom
    spread = z * np.sqrt(phat * (1 - phat) / total + z**2 / (4 * total**2)) / denom
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def power_grid_summary(frame: pd.DataFrame) -> tuple[int, int]:
    """Count field centroids and the distinct NASA POWER nodes they map to.

    Weather ingest snaps each field centroid to a MERRA-2 node with
    ``round(lat / 0.5) * 0.5`` and ``round(lon / 0.625) * 0.625``. Fields that
    share a node receive identical daily values, so the node count — not the
    field count — bounds the independent weather locations in a farm.
    """
    coords = frame[["field_id", "lat", "lon"]].drop_duplicates("field_id")
    grid_lat = (coords["lat"] / 0.5).round() * 0.5
    grid_lon = (coords["lon"] / 0.625).round() * 0.625
    nodes = set(zip(grid_lat.round(3).tolist(), grid_lon.round(3).tolist()))
    return (int(len(coords)), int(len(nodes)))


def bootstrap_median_ci(values: np.ndarray, seed: int = 42, reps: int = 10_000):
    values = np.asarray(values, dtype=float)
    if len(values) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    draws = rng.choice(values, size=(reps, len(values)), replace=True)
    medians = np.median(draws, axis=1)
    return (float(np.percentile(medians, 2.5)), float(np.percentile(medians, 97.5)))


# --------------------------------------------------------------------------
# Derived tables
# --------------------------------------------------------------------------


def seasonal_metrics(
    weather: pd.DataFrame,
    start_md: int,
    end_md: int,
    base: float,
    cap: float,
) -> pd.DataFrame:
    frame = weather.copy()
    frame["gdd"] = np.maximum(
        0.0, (np.minimum(frame["T2M_MAX"], cap) + frame["T2M_MIN"]) / 2.0 - base
    )
    frame["year"] = frame["date"].dt.year
    frame["in_season"] = season_mask(frame["date"], start_md, end_md)
    rows: list[dict] = []
    for (field_id, grower, year), group in frame.groupby(
        ["field_id", "grower", "year"], sort=True
    ):
        group = group.sort_values("date")
        season = group[group["in_season"]]
        if season.empty:
            continue
        precip = season["PRECTOTCORR"].to_numpy(dtype=float)
        dry = precip < 1.0
        rolling = pd.Series(precip).rolling(30, min_periods=30).sum()
        spring = group[(group["date"].dt.month <= 6) & (group["T2M_MIN"] <= 0.0)]
        fall = group[(group["date"].dt.month >= 7) & (group["T2M_MIN"] <= 0.0)]
        last_spring = spring["date"].max() if not spring.empty else pd.NaT
        first_fall = fall["date"].min() if not fall.empty else pd.NaT
        frost_free = (
            (first_fall - last_spring).days
            if pd.notna(last_spring) and pd.notna(first_fall)
            else np.nan
        )
        rows.append(
            {
                "field_id": field_id,
                "grower": grower,
                "year": int(year),
                "gdd": float(season["gdd"].sum()),
                "precip_mm": float(precip.sum()),
                "tmean_c": float(season["T2M"].mean()),
                "tmax_mean_c": float(season["T2M_MAX"].mean()),
                "tmin_mean_c": float(season["T2M_MIN"].mean()),
                "solar_mean": float(season["ALLSKY_SFC_SW_DWN"].mean()),
                "rh_mean": float(season["RH2M"].mean()),
                "wind_mean": float(season["WS10M"].mean()),
                "dry_days": int(dry.sum()),
                "longest_dry_run": int(longest_true_run(dry)),
                "max_30d_precip": (
                    float(rolling.max()) if rolling.notna().any() else np.nan
                ),
                "last_spring_frost": (
                    last_spring.date().isoformat() if pd.notna(last_spring) else ""
                ),
                "first_fall_frost": (
                    first_fall.date().isoformat() if pd.notna(first_fall) else ""
                ),
                "frost_free_days": frost_free,
            }
        )
    return pd.DataFrame(rows)


def cumulative_curves(
    weather: pd.DataFrame,
    start_md: int,
    end_md: int,
    base: float,
    cap: float,
    column: str,
) -> pd.DataFrame:
    frame = weather.copy()
    frame["gdd"] = np.maximum(
        0.0, (np.minimum(frame["T2M_MAX"], cap) + frame["T2M_MIN"]) / 2.0 - base
    )
    frame["in_season"] = season_mask(frame["date"], start_md, end_md)
    frame = frame[frame["in_season"]].copy()
    frame["year"] = frame["date"].dt.year
    start_month, start_day = start_md // 100, start_md % 100
    frame["season_start"] = pd.to_datetime(
        frame["date"].dt.year.astype(str) + f"-{start_month:02d}-{start_day:02d}"
    )
    frame["day_index"] = (frame["date"] - frame["season_start"]).dt.days
    frame = frame.sort_values(["field_id", "year", "date"])
    frame["value"] = frame["gdd"] if column == "gdd" else frame["PRECTOTCORR"]
    frame["cumulative"] = frame.groupby(["field_id", "year"])["value"].cumsum()
    return frame


def primary_crop_table(cdl: pd.DataFrame, area: pd.DataFrame) -> pd.DataFrame:
    frame = cdl.copy()
    idx = frame.groupby(["field_id", "year"])["pct"].idxmax()
    primary = frame.loc[idx].copy()
    sums = (
        frame.assign(
            _corn=np.where(frame["crop_name"].str.lower() == "corn", frame["pct"], 0.0),
            _soy=np.where(
                frame["crop_name"].str.lower() == "soybeans", frame["pct"], 0.0
            ),
            _classes=1,
        )
        .groupby(["field_id", "year"], as_index=False)
        .agg(corn_pct=("_corn", "sum"), soy_pct=("_soy", "sum"), n_classes=("_classes", "sum"))
    )
    primary = primary.merge(sums, on=["field_id", "year"], how="left")
    primary = primary.drop(columns=["grower"]).merge(
        area[["field_id", "area_ha", "grower"]].drop_duplicates("field_id"),
        on="field_id",
        how="left",
    )
    primary["purity"] = primary["pct"]
    primary["other_pct"] = (100.0 - primary["corn_pct"] - primary["soy_pct"]).clip(lower=0)
    primary = primary.rename(columns={"crop_name": "primary_crop"})
    return primary


def transition_table(primary: pd.DataFrame) -> pd.DataFrame:
    pivot = primary.pivot(index="field_id", columns="year", values="primary_crop")
    years = sorted(int(year) for year in pivot.columns)
    rows: list[dict] = []
    for field_id, row in pivot.iterrows():
        for from_year, to_year in zip(years[:-1], years[1:]):
            from_crop = row.get(from_year)
            to_crop = row.get(to_year)
            if pd.isna(from_crop) or pd.isna(to_crop):
                continue
            rows.append(
                {
                    "field_id": field_id,
                    "from_year": from_year,
                    "to_year": to_year,
                    "from_crop": from_crop,
                    "to_crop": to_crop,
                }
            )
    transitions = pd.DataFrame(rows)
    if transitions.empty:
        return transitions
    grower_map = (
        primary[["field_id", "grower"]].drop_duplicates("field_id").set_index("field_id")["grower"]
    )
    transitions["grower"] = transitions["field_id"].map(grower_map)
    transitions["type"] = transitions["from_crop"] + "→" + transitions["to_crop"]
    return transitions


def variance_components(frame: pd.DataFrame, value: str) -> dict:
    data = frame.dropna(subset=[value]).copy()
    if data.empty:
        return {}
    grand = float(data[value].mean())
    total = float(((data[value] - grand) ** 2).sum())
    grower_means = data.groupby("grower")[value].mean()
    ss_grower = float(
        sum(len(group) * (group[value].mean() - grand) ** 2 for _, group in data.groupby("grower"))
    )
    ss_field = float(
        sum(
            len(group) * (group[value].mean() - grower_means[grower]) ** 2
            for (grower, _field), group in data.groupby(["grower", "field_id"])
        )
    )
    ss_year = float(
        sum(len(group) * (group[value].mean() - grand) ** 2 for _, group in data.groupby("year"))
    )
    ss_resid = max(total - ss_grower - ss_field - ss_year, 0.0)
    return {
        "metric": value,
        "n_observations": int(len(data)),
        "ss_total": total,
        "ss_grower": ss_grower,
        "ss_field_within_grower": ss_field,
        "ss_year": ss_year,
        "ss_residual": ss_resid,
        "pct_grower": 100.0 * ss_grower / total if total else np.nan,
        "pct_field_within_grower": 100.0 * ss_field / total if total else np.nan,
        "pct_year": 100.0 * ss_year / total if total else np.nan,
        "pct_residual": 100.0 * ss_resid / total if total else np.nan,
    }


def spearman_exact(a, b) -> tuple[float, float, int, str]:
    """Spearman rho with an exact two-sided permutation p for small n.

    The asymptotic approximation is anti-conservative for n <= 8 (for example,
    rho = -0.90 at n = 5 returns p = 0.037 asymptotically but p = 0.083 exactly),
    so small samples use the full permutation distribution instead.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    mask = ~(np.isnan(a) | np.isnan(b))
    a, b = a[mask], b[mask]
    n = len(a)
    if n < 3:
        return (float("nan"), float("nan"), n, "insufficient")
    rank_a = stats.rankdata(a)
    rank_b = stats.rankdata(b)
    rho = float(np.corrcoef(rank_a, rank_b)[0, 1])
    if n <= 8:
        hits = 0
        total = 0
        for perm in itertools.permutations(rank_b.tolist()):
            total += 1
            value = float(np.corrcoef(rank_a, perm)[0, 1])
            if abs(value) >= abs(rho) - 1e-12:
                hits += 1
        return (rho, hits / total, n, "exact permutation")
    return (rho, float(stats.spearmanr(a, b).pvalue), n, "asymptotic")


def cluster_permutation_p(
    x, y, groups, permutations: int, seed: int
) -> tuple[float, float, int, str]:
    """Pearson r for grower-demeaned values with a within-grower year shuffle.

    Shuffling years inside each grower preserves the farm means, so the test
    asks whether the observed anomaly alignment is unusual within farms.
    Shared growing seasons make this p-value optimistic; report it as a
    descriptive sensitivity check, not as independent replication.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    groups = np.asarray(groups)
    observed = float(np.corrcoef(x, y)[0, 1])
    rng = np.random.default_rng(seed)
    hits = 0
    unique = list(dict.fromkeys(groups.tolist()))
    for _ in range(permutations):
        permuted = y.copy()
        for group in unique:
            index = np.where(groups == group)[0]
            permuted[index] = rng.permutation(y[index])
        value = float(np.corrcoef(x, permuted)[0, 1])
        if abs(value) >= abs(observed) - 1e-12:
            hits += 1
    return observed, (hits + 1) / (permutations + 1), len(x), "cluster permutation"


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------


def apply_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 110,
            "savefig.dpi": 200,
            "font.size": 9,
            "axes.titlesize": 11,
            "axes.titleweight": "bold",
            "axes.labelsize": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "grid.linewidth": 0.6,
            "legend.frameon": False,
            "legend.fontsize": 8,
        }
    )


def save_figure(fig, path: Path, dpi: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def figure_boundary_size(boundary: pd.DataFrame, stats_table: pd.DataFrame, tests: pd.DataFrame, out: Path, dpi: int) -> None:
    fig, ax = plt.subplots(figsize=(9.2, 4.2))
    rng = np.random.default_rng(7)
    growers = list(GROWER_ORDER)
    positions = {grower: index for index, grower in enumerate(growers)}
    area_factor = UNITS.area_factor
    area_unit = UNITS.area_short
    areas = boundary["area_ha"] * area_factor
    for grower in growers:
        values = (
            boundary.loc[boundary["grower"] == grower, "area_ha"].to_numpy() * area_factor
        )
        x = values + rng.normal(0, 2.5 * area_factor, len(values))
        y = positions[grower] + rng.normal(0, 0.045, len(values))
        ax.scatter(
            x,
            y,
            s=34,
            color=GROWER_COLORS[grower],
            edgecolor="white",
            linewidth=0.8,
            zorder=3,
            alpha=0.92,
        )
    box = ax.boxplot(
        [
            boundary.loc[boundary["grower"] == grower, "area_ha"].to_numpy() * area_factor
            for grower in growers
        ],
        positions=[positions[grower] for grower in growers],
        orientation="horizontal",
        widths=0.32,
        showfliers=False,
        patch_artist=True,
        medianprops={"color": "#222222", "linewidth": 1.6},
        whiskerprops={"color": "#666666"},
        capprops={"color": "#666666"},
    )
    for patch, grower in zip(box["boxes"], growers):
        patch.set_facecolor(GROWER_COLORS[grower])
        patch.set_alpha(0.12)
        patch.set_edgecolor(GROWER_COLORS[grower])
    for grower in growers:
        row = stats_table[stats_table["grower"] == grower]
        if row.empty:
            continue
        row = row.iloc[0]
        median = float(row["median_ha"]) * area_factor
        ax.text(
            float(areas.max()) * 1.06,
            positions[grower],
            f"n={int(row['n'])} · CV {row['cv_pct']:.1f} % · median {median:.0f} {area_unit}",
            ha="left",
            va="center",
            fontsize=8,
            color="#333333",
        )
    ax.set_yticks([positions[grower] for grower in growers])
    ax.set_yticklabels(growers)
    ax.set_xlabel(f"Field area ({area_unit}, equal-area recomputation)")
    ax.set_title("Field size spread differs sharply across the three growers")
    ax.set_xlim(0, float(areas.max()) * 1.55)
    kw = tests[(tests["comparison"] == "overall") & (tests["metric"] == "area_ha")]
    if not kw.empty:
        ax.text(
            0.01,
            0.02,
            f"Kruskal–Wallis p = {float(kw.iloc[0]['p_raw']):.2g} (n=10 fields per grower)",
            transform=ax.transAxes,
            fontsize=8,
            color="#333333",
        )
    band = (
        "Nebraska fields were selected inside a 100–170 ac band; the narrow spread is a sampling choice, not a regional property."
        if UNITS.is_us
        else "Nebraska fields were selected inside a 40–70 ha band; the narrow spread is a sampling choice, not a regional property."
    )
    fig.text(
        0.01,
        -0.06,
        f"Points are jittered horizontally for visibility. {band}",
        fontsize=7.5,
        color="#555555",
    )
    save_figure(fig, out / "figures" / "fig_boundary_size_by_grower.png", dpi)


def figure_boundary_shape(boundary: pd.DataFrame, corr: pd.DataFrame, out: Path, dpi: int) -> None:
    fig, ax = plt.subplots(figsize=(8.4, 4.8))
    area_factor = UNITS.area_factor
    area_unit = UNITS.area_short
    areas = boundary["area_ha"] * area_factor
    for grower in GROWER_ORDER:
        subset = boundary[boundary["grower"] == grower]
        row = corr[corr["grower"] == grower]
        label = grower
        if not row.empty:
            pvalue = float(row.iloc[0]["p_value"])
            ptext = "p<0.001" if pvalue < 0.001 else f"p={pvalue:.2f}"
            label = f"{grower} (ρ={row.iloc[0]['rho']:.2f}, {ptext})"
        ax.scatter(
            subset["area_ha"] * area_factor,
            subset["polsby_popper"],
            s=48,
            color=GROWER_COLORS[grower],
            edgecolor="white",
            linewidth=0.8,
            label=label,
            zorder=3,
        )
    ax.set_xscale("log")
    ax.set_xlabel(f"Field area ({area_unit}, log scale)")
    ax.set_ylabel("Polsby–Popper compactness  (4πA / P²)")
    ax.set_ylim(0, 0.85)
    if UNITS.is_us:
        ax.set_xticks([200, 300, 500, 700, 1000])
        ax.set_xticklabels(["200", "300", "500", "700", "1000"])
    else:
        ax.set_xticks([70, 100, 150, 200, 300, 400])
        ax.set_xticklabels(["70", "100", "150", "200", "300", "400"])
    ax.xaxis.set_minor_formatter(ticker.NullFormatter())
    ax.axhline(np.pi / 4, color="#888888", linestyle="--", linewidth=0.9)
    ax.text(
        float(areas.max()) * 0.55,
        np.pi / 4 + 0.012,
        "square (π/4)",
        fontsize=7.5,
        color="#777777",
        ha="right",
    )
    ax.set_title("Within each grower, compactness is unrelated to size — except in Iowa")
    ax.legend(loc="upper right")
    fig.text(
        0.01,
        -0.05,
        "Compactness is scale-free but sensitive to digitised slivers; FIBOA boundaries are not survey-grade.",
        fontsize=7.5,
        color="#555555",
    )
    save_figure(fig, out / "figures" / "fig_boundary_shape_vs_size.png", dpi)


def season_day_ticks(start_md: int, end_md: int) -> tuple[list[int], list[str]]:
    start_month, start_day = start_md // 100, start_md % 100
    end_month, end_day = end_md // 100, end_md % 100
    start = pd.Timestamp(2023, start_month, start_day)
    end = pd.Timestamp(2023, end_month, end_day)
    ticks, labels = [], []
    cursor = start
    while cursor <= end:
        ticks.append(int((cursor - start).days))
        labels.append(cursor.strftime("%b"))
        cursor = (cursor.replace(day=1) + pd.offsets.MonthBegin(1))
    ticks.append(int((end - start).days))
    labels.append(end.strftime("%b %d"))
    return ticks, labels


def format_md(md: int) -> str:
    month, day = md // 100, md % 100
    return pd.Timestamp(2023, month, day).strftime("%d %b").lstrip("0")


def figure_weather_gdd(
    curves: pd.DataFrame,
    metrics: pd.DataFrame,
    out: Path,
    dpi: int,
    start_md: int,
    end_md: int,
    gdd_base: float,
    gdd_cap: float,
) -> None:
    years = sorted(metrics["year"].unique())
    fig, axes = plt.subplots(
        1, len(years), figsize=(15.5, 4.2), sharey=True, constrained_layout=True
    )
    if len(years) == 1:
        axes = [axes]
    gdd_factor = UNITS.gdd_factor
    gdd_unit = UNITS.gdd_short
    display_base = UNITS.temperature(gdd_base)
    display_cap = UNITS.temperature(gdd_cap)
    for ax, year in zip(axes, years):
        ends = []
        for grower in GROWER_ORDER:
            subset = curves[(curves["grower"] == grower) & (curves["date"].dt.year == year)]
            if subset.empty:
                continue
            agg = subset.groupby("day_index")["cumulative"].agg(["mean", "min", "max"])
            agg = agg * gdd_factor
            ax.plot(
                agg.index,
                agg["mean"],
                color=GROWER_COLORS[grower],
                linewidth=1.8,
                label=grower,
            )
            ax.fill_between(
                agg.index,
                agg["min"],
                agg["max"],
                color=GROWER_COLORS[grower],
                alpha=0.15,
                linewidth=0,
            )
            total = (
                metrics[(metrics["grower"] == grower) & (metrics["year"] == year)]["gdd"].mean()
                * gdd_factor
            )
            ends.append(
                (
                    float(agg["mean"].iloc[-1]),
                    grower,
                    float(total),
                    int(agg.index.max()),
                )
            )
        ends.sort(key=lambda item: item[0])
        placed = []
        previous = None
        for value, grower, total, last_day in ends:
            ypos = value if previous is None else max(value, previous + 55.0 * gdd_factor)
            placed.append((value, ypos, grower, total, last_day))
            previous = ypos
        if placed:
            shift = (sum(item[0] for item in placed) - sum(item[1] for item in placed)) / len(placed)
            for value, ypos, grower, total, last_day in placed:
                ax.annotate(
                    f"{total:,.0f}",
                    xy=(last_day, value),
                    xytext=(last_day + 3, ypos + shift),
                    textcoords="data",
                    color=GROWER_COLORS[grower],
                    fontsize=7.5,
                    va="center",
                    fontweight="bold",
                    arrowprops=dict(arrowstyle="-", color=GROWER_COLORS[grower], lw=0.5, alpha=0.7),
                )
        ax.set_title(str(year))
        ax.set_xlabel("Day of growing season")
    axes[0].set_ylabel(f"Cumulative GDD ({gdd_unit})")
    ticks, labels = season_day_ticks(start_md, end_md)
    for ax in axes:
        ax.set_xticks(ticks)
        ax.set_xticklabels(labels)
        ax.set_xlim(0, float(curves["day_index"].max()) + 20)
        ax.set_ylim(0, float(curves["cumulative"].max()) * gdd_factor * 1.08)
    handles = [Line2D([0], [0], color=GROWER_COLORS[g], lw=2, label=g) for g in GROWER_ORDER]
    fig.legend(handles=handles, loc="upper right", ncol=len(GROWER_ORDER), bbox_to_anchor=(0.99, 1.06))
    fig.suptitle(
        f"Cumulative growing-season GDD (base {display_base:g} {UNITS.temp_short}, "
        f"cap {display_cap:g} {UNITS.temp_short}; "
        f"{format_md(start_md)} – {format_md(end_md)})",
        y=1.10,
        fontsize=12,
        fontweight="bold",
    )
    fig.text(
        0.01,
        -0.04,
        "Lines are field means; bands are the field min–max. The 10 fields per farm map to only 2–4 NASA POWER cells (0.5° × 0.625°), so same-cell fields are identical. End labels are grower-year mean seasonal totals.",
        fontsize=7.5,
        color="#555555",
    )
    save_figure(fig, out / "figures" / "fig_weather_gdd_accumulation.png", dpi)


def figure_weather_precip(
    curves: pd.DataFrame, metrics: pd.DataFrame, out: Path, dpi: int, start_md: int, end_md: int
) -> None:
    fig, (left, right) = plt.subplots(
        1, 2, figsize=(12.4, 4.6), gridspec_kw={"width_ratios": [1.7, 1]}, constrained_layout=True
    )
    precip_factor = UNITS.precip_factor
    precip_unit = UNITS.precip_short
    for grower in GROWER_ORDER:
        subset = curves[curves["grower"] == grower]
        if subset.empty:
            continue
        year_means = subset.groupby(["year", "day_index"], as_index=False)["cumulative"].mean()
        agg = year_means.groupby("day_index")["cumulative"].agg(["mean", "min", "max"]) * precip_factor
        left.plot(agg.index, agg["mean"], color=GROWER_COLORS[grower], linewidth=1.9, label=grower)
        left.fill_between(
            agg.index, agg["min"], agg["max"], color=GROWER_COLORS[grower], alpha=0.12, linewidth=0
        )
    ticks, labels = season_day_ticks(start_md, end_md)
    left.set_xticks(ticks)
    left.set_xticklabels(labels)
    left.set_xlabel("Day of growing season")
    left.set_ylabel(f"Cumulative precipitation ({precip_unit})")
    left.set_title(
        f"Seasonal rainfall accumulation ({metrics['year'].nunique()}-year mean, year min–max band)"
    )
    left.legend(loc="upper left")

    years = sorted(metrics["year"].unique())
    year_color = dict(zip(years, YEAR_COLORS[: len(years)]))
    rng = np.random.default_rng(3)
    for index, grower in enumerate(GROWER_ORDER):
        subset = metrics[metrics["grower"] == grower].sort_values("year")
        values = subset["precip_mm"].to_numpy() * precip_factor
        offsets = np.linspace(-0.16, 0.16, len(values)) if len(values) > 1 else np.zeros(len(values))
        right.scatter(
            index + offsets,
            values,
            s=44,
            c=[year_color.get(int(year), "#777777") for year in subset["year"]],
            edgecolor="white",
            linewidth=0.8,
            zorder=3,
        )
        right.plot([index - 0.22, index + 0.22], [np.median(values)] * 2, color="#333333", lw=1.6, zorder=4)
    right.set_xticks(range(len(GROWER_ORDER)))
    right.set_xticklabels(GROWER_ORDER)
    right.set_ylabel(f"Seasonal precipitation ({precip_unit})")
    right.set_title("Season totals by year (one point per field-year)")
    right.set_xlim(-0.5, len(GROWER_ORDER) - 0.5)
    handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=year_color[year], markersize=6, label=str(year))
        for year in years
    ]
    right.legend(
        handles=handles,
        title="Year",
        loc="upper left",
        ncol=2,
        fontsize=7,
        title_fontsize=7,
    )
    fig.suptitle(
        "Growing-season precipitation: Nebraska is drier, Iowa is more variable",
        y=1.03,
        fontsize=12,
        fontweight="bold",
    )
    fig.text(
        0.01,
        -0.05,
        "Precipitation is summed, not averaged. NASA POWER cells are 0.5° × 0.625°; fields in the same cell share identical weather, so within-farm spread is not microclimate. No irrigation, ET, or soil-water data — low rainfall does not prove drought stress.",
        fontsize=7.5,
        color="#555555",
    )
    save_figure(fig, out / "figures" / "fig_weather_seasonal_precip.png", dpi)


def figure_weather_coupling(
    grower_year: pd.DataFrame, corr: pd.DataFrame, out: Path, dpi: int
) -> None:
    plot_frame = grower_year.copy()
    plot_frame["gdd_mean"] = plot_frame["gdd_mean"] * UNITS.gdd_factor
    plot_frame["gdd_anomaly"] = plot_frame["gdd_anomaly"] * UNITS.gdd_factor
    plot_frame["precip_mean"] = plot_frame["precip_mean"] * UNITS.precip_factor
    plot_frame["precip_anomaly"] = plot_frame["precip_anomaly"] * UNITS.precip_factor
    fig, axes = plt.subplots(2, 2, figsize=(11.4, 8.6), constrained_layout=True)
    axes = axes.ravel()
    panels = [(grower, grower) for grower in GROWER_ORDER] + [
        ("pooled_within_grower", "All farms — grower-demeaned")
    ]
    for ax, (scope, title) in zip(axes, panels):
        if scope == "pooled_within_grower":
            for grower in GROWER_ORDER:
                subset = plot_frame[plot_frame["grower"] == grower]
                ax.scatter(
                    subset["gdd_anomaly"],
                    subset["precip_anomaly"],
                    s=42,
                    color=GROWER_COLORS[grower],
                    edgecolor="white",
                    linewidth=0.8,
                    label=grower,
                    zorder=3,
                )
                for row in subset.itertuples():
                    ax.annotate(
                        str(int(row.year)),
                        (row.gdd_anomaly, row.precip_anomaly),
                        xytext=(4, 4),
                        textcoords="offset points",
                        fontsize=6.5,
                        color="#4A463F",
                    )
            ax.axhline(0, color="#B9B2A6", lw=0.8, zorder=1)
            ax.axvline(0, color="#B9B2A6", lw=0.8, zorder=1)
            coefficient = np.polyfit(
                plot_frame["gdd_anomaly"], plot_frame["precip_anomaly"], 1
            )
            grid = np.linspace(
                plot_frame["gdd_anomaly"].min(),
                plot_frame["gdd_anomaly"].max(),
                40,
            )
            ax.plot(grid, np.polyval(coefficient, grid), color="#3A3630", lw=1.2, zorder=2)
            row = corr.loc[corr["scope"] == scope].iloc[0]
            title = (
                "All farms — grower-demeaned\n"
                f"r = {row['pearson_r']:+.2f} · cluster p = {row['pearson_p']:.3f} · "
                f"n = {int(row['n'])}"
            )
            ax.set_xlabel(f"GDD anomaly ({UNITS.gdd_short})")
            ax.set_ylabel(f"Precipitation anomaly ({UNITS.precip_short})")
            ax.margins(x=0.07, y=0.12)
            ax.legend(loc="lower left", fontsize=7.5)
        else:
            subset = plot_frame[plot_frame["grower"] == scope].sort_values("year")
            ax.plot(
                subset["gdd_mean"],
                subset["precip_mean"],
                color=GROWER_COLORS[scope],
                lw=1.2,
                alpha=0.55,
                zorder=2,
            )
            ax.scatter(
                subset["gdd_mean"],
                subset["precip_mean"],
                s=46,
                color=GROWER_COLORS[scope],
                edgecolor="white",
                linewidth=0.8,
                zorder=3,
            )
            for row in subset.itertuples():
                offset = 5 if row.precip_mean >= subset["precip_mean"].median() else -12
                ax.annotate(
                    str(int(row.year)),
                    (row.gdd_mean, row.precip_mean),
                    xytext=(5, offset),
                    textcoords="offset points",
                    fontsize=7,
                    color="#4A463F",
                )
            row = corr.loc[corr["scope"] == scope].iloc[0]
            x_values = subset["gdd_mean"]
            y_values = subset["precip_mean"]
            x_pad = 0.08 * (x_values.max() - x_values.min())
            y_pad = 0.14 * (y_values.max() - y_values.min())
            ax.set_xlim(x_values.min() - x_pad, x_values.max() + x_pad)
            ax.set_ylim(y_values.min() - y_pad, y_values.max() + y_pad)
            top_x = float(x_values.loc[y_values.idxmax()])
            relative_top = (top_x - x_values.min()) / (x_values.max() - x_values.min())
            anchor_right = relative_top < 0.45
            ax.text(
                0.97 if anchor_right else 0.03,
                0.97,
                f"ρ = {row['spearman_rho']:+.2f}\nexact p = {row['spearman_p']:.3f} "
                f"(n = {int(row['n'])})",
                transform=ax.transAxes,
                va="top",
                ha="right" if anchor_right else "left",
                fontsize=8.5,
                bbox=dict(boxstyle="round,pad=0.35", facecolor="white", edgecolor="#DDD8CE", lw=0.7),
            )
            ax.set_xlabel(f"Seasonal GDD ({UNITS.gdd_short})")
            ax.set_ylabel(f"Seasonal precipitation ({UNITS.precip_short})")
        ax.set_title(title, loc="left")
    fig.suptitle(
        "In these five seasons, hot and dry went together in Iowa and Nebraska — not Illinois",
        fontsize=13,
        fontweight="bold",
        y=1.03,
    )
    fig.text(
        0.01,
        -0.03,
        "Each point is one grower-year mean over 10 fields. Those fields map to only 2–4 NASA POWER cells (0.5° × 0.625°), so they are not independent climate observations. "
        "Post hoc exploratory analysis. Farm panels use independent axis ranges: compare direction, not slope. Five seasons cannot establish a climate trend.",
        fontsize=7.3,
        color="#555555",
    )
    save_figure(fig, out / "figures" / "fig_weather_gdd_precip_coupling.png", dpi)


def figure_cdl_sequence(primary: pd.DataFrame, rotation: pd.DataFrame, out: Path, dpi: int) -> None:
    years = sorted(int(year) for year in primary["year"].unique())
    growers = list(GROWER_ORDER)
    rows: list[tuple[str, str]] = []
    for grower in growers:
        field_ids = sorted(
            primary.loc[primary["grower"] == grower, "field_id"].unique(),
            key=lambda value: (len(value), value),
        )
        rows.extend((grower, field_id) for field_id in field_ids)
    crop_lookup = {
        (row["field_id"], int(row["year"])): row["primary_crop"]
        for _, row in primary.iterrows()
    }
    next_lookup = {}
    if not rotation.empty:
        next_lookup = dict(zip(rotation["field_id"], rotation.get("predicted_next_crop", "")))

    def class_of(crop: str) -> str:
        crop = str(crop)
        if crop.lower() == "corn":
            return "Corn"
        if crop.lower() == "soybeans":
            return "Soybeans"
        return "Other"

    fig, ax = plt.subplots(figsize=(10.4, 9.2))
    n_rows = len(rows)
    for y_index, (grower, field_id) in enumerate(rows):
        for x_index, year in enumerate(years):
            crop = crop_lookup.get((field_id, year), "Unknown")
            color = CROP_COLORS[class_of(crop)]
            ax.add_patch(
                Rectangle(
                    (x_index - 0.46, y_index - 0.42),
                    0.92,
                    0.84,
                    facecolor=color,
                    edgecolor="white",
                    linewidth=1.0,
                )
            )
        predicted = next_lookup.get(field_id, "")
        if predicted:
            ax.add_patch(
                Rectangle(
                    (len(years) - 0.46, y_index - 0.42),
                    0.92,
                    0.84,
                    facecolor=CROP_COLORS[class_of(predicted)],
                    edgecolor="#444444",
                    linewidth=0.7,
                    hatch="///",
                    alpha=0.65,
                )
            )
    ax.set_xlim(-1.35, len(years) + 0.5)
    ax.set_ylim(n_rows - 0.5, -0.5)
    ax.set_xticks(list(range(len(years))) + [len(years)])
    ax.set_xticklabels([str(year) for year in years] + ["next*"])
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels([field_id for _, field_id in rows], fontsize=7.3)
    ax.grid(False)
    ax.tick_params(length=0)

    boundaries = []
    start = 0
    for grower in growers:
        count = sum(1 for row_grower, _ in rows if row_grower == grower)
        boundaries.append((start, start + count, grower))
        start += count
    for start, end, grower in boundaries:
        if start > 0:
            ax.axhline(start - 0.5, color="#444444", linewidth=0.9)
        ax.text(
            -1.16,
            (start + end - 1) / 2,
            grower,
            rotation=90,
            va="center",
            ha="center",
            fontsize=11,
            fontweight="bold",
            color=GROWER_COLORS[grower],
        )
        ax.add_patch(
            Rectangle(
                (-1.02, start - 0.45),
                0.08,
                (end - start) - 0.1,
                facecolor=GROWER_COLORS[grower],
                edgecolor="none",
                clip_on=False,
            )
        )
    handles = [
        Patch(facecolor=CROP_COLORS["Corn"], label="Corn"),
        Patch(facecolor=CROP_COLORS["Soybeans"], label="Soybeans"),
        Patch(facecolor=CROP_COLORS["Other"], label="Other"),
        Patch(facecolor="#EEEEEE", edgecolor="#444444", hatch="///", label="heuristic next crop"),
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.005),
        ncol=4,
        fontsize=8,
    )
    ax.set_title(f"Crop sequence by field, {years[0]}–{years[-1]} (primary CDL class)", pad=10)
    fig.text(
        0.01,
        -0.015,
        "* next-crop column is the rotation table's heuristic outlook, not an observation. 30 m CDL; minority classes are not shown.",
        fontsize=7.5,
        color="#555555",
    )
    save_figure(fig, out / "figures" / "fig_cdl_crop_sequence_grid.png", dpi)


def figure_cdl_mix(mix: pd.DataFrame, out: Path, dpi: int) -> None:
    years = sorted(int(year) for year in mix["year"].unique())
    fig, axes = plt.subplots(1, 3, figsize=(12.6, 4.2), sharey=True, constrained_layout=True)
    for ax, grower in zip(axes, GROWER_ORDER):
        subset = mix[mix["grower"] == grower]
        bottom = np.zeros(len(years))
        for crop_class in ["Corn", "Soybeans", "Other"]:
            values = np.array(
                [float(subset[(subset["year"] == year) & (subset["crop_class"] == crop_class)]["share"].sum()) for year in years]
            )
            ax.bar(years, values, bottom=bottom, color=CROP_COLORS[crop_class], width=0.72, edgecolor="white", linewidth=0.8)
            bottom += values
        corn_values = np.array(
            [float(subset[(subset["year"] == year) & (subset["crop_class"] == "Corn")]["share"].sum()) for year in years]
        )
        for year, value in zip(years, corn_values):
            ax.text(year, 101, f"{value:.0f}%", ha="center", va="bottom", fontsize=7.5, color="#8A5A00")
        ax.set_title(grower)
        ax.set_ylim(0, 112)
        ax.set_xticks(years)
        ax.set_xlabel("CDL year")
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("Share of grower field area (%)")
    handles = [Patch(facecolor=CROP_COLORS[key], label=key) for key in ["Corn", "Soybeans", "Other"]]
    fig.legend(handles=handles, loc="upper center", ncol=3, bbox_to_anchor=(0.5, 1.04))
    fig.suptitle("Area-weighted crop mix: corn dominates Nebraska field area", y=1.10, fontsize=12, fontweight="bold")
    fig.text(
        0.01,
        -0.05,
        "Crop area = field area × CDL class share. Boundary area, not planted area; edge pixels inflate 'Other' in small fields.",
        fontsize=7.5,
        color="#555555",
    )
    save_figure(fig, out / "figures" / "fig_cdl_area_weighted_crop_mix.png", dpi)


def figure_corn_after_corn(
    by_transition: pd.DataFrame, persistence: pd.DataFrame, out: Path, dpi: int
) -> None:
    fig, (left, right) = plt.subplots(
        1,
        2,
        figsize=(12.8, 5.0),
        constrained_layout=True,
        gridspec_kw={"width_ratios": [1.65, 1.0]},
    )
    transitions = sorted(
        by_transition["transition"].unique(), key=lambda value: int(value.split("->")[0])
    )
    positions = np.arange(len(transitions))
    width = 0.23
    offsets = {
        grower: (index - (len(GROWER_ORDER) - 1) / 2) * width
        for index, grower in enumerate(GROWER_ORDER)
    }
    for grower in GROWER_ORDER:
        subset = (
            by_transition[by_transition["grower"] == grower]
            .set_index("transition")
            .reindex(transitions)
        )
        values = subset["proportion"].to_numpy(dtype=float)
        lower = np.clip(values - subset["wilson_low"].to_numpy(dtype=float), 0.0, None)
        upper = np.clip(subset["wilson_high"].to_numpy(dtype=float) - values, 0.0, None)
        pooled = subset["corn_after_corn"].sum() / subset["n_eligible"].sum()
        left.errorbar(
            positions + offsets[grower],
            values,
            yerr=np.vstack([lower, upper]),
            fmt="o",
            color=GROWER_COLORS[grower],
            ecolor=GROWER_COLORS[grower],
            elinewidth=1.3,
            capsize=3,
            markersize=6,
            label=f"{grower} (pooled {pooled:.0%})",
            zorder=3,
        )
        for x_value, y_value, total, success in zip(
            positions + offsets[grower],
            values,
            subset["n_eligible"],
            subset["corn_after_corn"],
        ):
            if np.isfinite(y_value):
                left.annotate(
                    f"{success}/{total}",
                    (x_value, y_value),
                    xytext=(0, 9),
                    textcoords="offset points",
                    ha="center",
                    fontsize=6.5,
                    color="#4A463F",
                )
    left.set_xticks(positions)
    left.set_xticklabels([value.replace("->", "→") for value in transitions])
    left.set_ylim(-0.05, 1.08)
    left.yaxis.set_major_formatter(ticker.PercentFormatter(1.0, decimals=0))
    left.set_xlabel("CDL transition (from-year → to-year)")
    left.set_ylabel("Share of corn fields that stay corn")
    left.set_title("Corn→corn by transition year", loc="left")
    left.legend(
        loc="upper center", ncol=3, fontsize=7, frameon=False, bbox_to_anchor=(0.5, 1.0)
    )

    for index, row in enumerate(persistence.itertuples()):
        low = row.null_q025 / row.eligible_transitions
        high = row.null_q975 / row.eligible_transitions
        right.plot(
            [index, index],
            [low, high],
            color="#9E988C",
            lw=7,
            solid_capstyle="round",
            alpha=0.5,
            zorder=2,
        )
        right.scatter(
            [index],
            [row.observed_rate],
            s=62,
            color=GROWER_COLORS[row.grower],
            edgecolor="white",
            linewidth=0.8,
            zorder=4,
        )
        right.annotate(
            f"obs {row.observed_corn_after_corn}/{row.eligible_transitions}\n"
            f"expected {row.null_mean / row.eligible_transitions:.0%}\n"
            f"p = {row.p_two_sided:.2g}",
            (index, row.observed_rate),
            xytext=(13, 0),
            textcoords="offset points",
            va="center",
            fontsize=7.5,
        )
    right.set_xticks(range(len(persistence)))
    right.set_xticklabels(persistence["grower"])
    right.set_ylim(-0.03, 1.05)
    right.set_xlim(-0.55, len(persistence) - 0.05)
    right.yaxis.set_major_formatter(ticker.PercentFormatter(1.0, decimals=0))
    right.set_ylabel("Corn→corn share of eligible transitions")
    right.set_title("Observed vs expected adjacency\n(within-field year shuffle)", loc="left")
    right.grid(axis="x", visible=False)

    fig.suptitle(
        "Nebraska's corn-on-corn is steady across all four transitions",
        fontsize=13,
        fontweight="bold",
        y=1.03,
    )
    fig.text(
        0.01,
        -0.03,
        "Eligibility = fields with corn in the from-year. Transitions within a field are not independent; the right panel tests adjacency against a "
        "within-field year shuffle that preserves each field's corn-year count. CDL 2019–2023 primary class.",
        fontsize=7.3,
        color="#555555",
    )
    save_figure(fig, out / "figures" / "fig_cdl_corn_after_corn_by_year.png", dpi)


def figure_map(
    boundary: pd.DataFrame, out: Path, dpi: int, warnings: list[str], crop_year: int
) -> None:
    states_path = shared_geoadmin_states_dir() / "states_usa.geojson"
    counties_path = shared_geoadmin_counties_dir() / "counties_usa.geojson"
    transformer_to_wgs = Transformer.from_crs("EPSG:5070", "EPSG:4326", always_xy=True)

    fig = plt.figure(figsize=(14.5, 8.8))
    grid = fig.add_gridspec(2, 3, height_ratios=[0.85, 1.3], hspace=0.16, wspace=0.08)
    locator = fig.add_subplot(grid[0, :])
    panels = [fig.add_subplot(grid[1, index]) for index in range(3)]

    states = gpd.read_file(states_path)
    if states.crs is None:
        states = states.set_crs("EPSG:4326")
    states = states.to_crs("EPSG:5070")
    if "state_code" in states.columns:
        states = states[~states["state_code"].isin(["AK", "HI", "PR", "VI", "GU", "MP", "AS"])]
    states.plot(ax=locator, facecolor="#F3F1EC", edgecolor="#B9B4AA", linewidth=0.5)
    locator.set_axis_off()
    locator.set_title("Grower locations (CONUS locator)", fontsize=11, fontweight="bold", pad=4)

    extents = {}
    for grower in GROWER_ORDER:
        subset = boundary[boundary["grower"] == grower]
        proj = gpd.GeoSeries(subset["_proj_geometry"], crs="EPSG:5070")
        centroid = proj.union_all().centroid
        half = 30_000.0
        extents[grower] = (
            centroid.x - half,
            centroid.y - half,
            centroid.x + half,
            centroid.y + half,
        )

    for grower in GROWER_ORDER:
        xmin, ymin, xmax, ymax = extents[grower]
        locator.add_patch(
            Rectangle(
                (xmin, ymin),
                xmax - xmin,
                ymax - ymin,
                facecolor=GROWER_COLORS[grower],
                edgecolor=GROWER_COLORS[grower],
                linewidth=1.4,
                alpha=0.85,
            )
        )
        locator.annotate(
            grower,
            ((xmin + xmax) / 2, ymax + 60_000),
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
            color=GROWER_COLORS[grower],
        )
    total_bounds = states.total_bounds
    locator.set_xlim(total_bounds[0] - 300_000, total_bounds[2] + 300_000)
    locator.set_ylim(total_bounds[1] - 300_000, total_bounds[3] + 300_000)
    locator.set_aspect("equal")

    for ax, grower in zip(panels, GROWER_ORDER):
        subset = boundary[boundary["grower"] == grower].copy()
        xmin, ymin, xmax, ymax = extents[grower]
        county_name = str(subset["county"].iloc[0])
        state_fips = str(subset["state_fips"].iloc[0]).zfill(2)
        area_factor = UNITS.area_factor
        panel_title = (
            f"{grower} · {county_name} County · "
            f"n={len(subset)} · {subset['area_ha'].min() * area_factor:.0f}–"
            f"{subset['area_ha'].max() * area_factor:.0f} {UNITS.area_short}"
        )
        lon_min, lat_min = transformer_to_wgs.transform(xmin, ymin)
        lon_max, lat_max = transformer_to_wgs.transform(xmax, ymax)
        try:
            counties = gpd.read_file(
                counties_path,
                bbox=(lon_min, lat_min, lon_max, lat_max),
                engine="pyogrio",
            )
            counties = counties[
                (counties["state_fips"].astype(str).str.zfill(2) == state_fips)
                & (counties["county_name"].str.lower() == county_name.lower())
            ].to_crs("EPSG:5070")
            if not counties.empty:
                counties.plot(ax=ax, facecolor="#F7F4ED", edgecolor="#A8A297", linewidth=0.8, zorder=1)
        except Exception as exc:  # pragma: no cover - depends on optional geoadmin
            warnings.append(f"county outline unavailable for {grower}: {exc}")
            ax.set_facecolor("#F7F4ED")

        colors = []
        for _, row in subset.iterrows():
            crop = str(row.get("crop_latest", "") or row.get("crop", ""))
            colors.append(CROP_COLORS["Corn"] if crop.lower() == "corn" else CROP_COLORS["Soybeans"] if crop.lower() == "soybeans" else CROP_COLORS["Other"])
        subset["_plot_geometry"] = subset["_proj_geometry"]
        plot_gdf = gpd.GeoDataFrame(subset, geometry="_plot_geometry", crs="EPSG:5070")
        plot_gdf.plot(
            ax=ax,
            color=colors,
            edgecolor="#3A3A3A",
            linewidth=0.8,
            alpha=0.9,
            zorder=2,
        )
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.grid(False)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_title(panel_title, fontsize=9.5, pad=4)
        bar_x = xmin + 0.06 * (xmax - xmin)
        bar_y = ymin + 0.05 * (ymax - ymin)
        bar_length = 16_093.4 if UNITS.is_us else 10_000.0
        bar_label = "10 mi" if UNITS.is_us else "10 km"
        ax.plot([bar_x, bar_x + bar_length], [bar_y, bar_y], color="#222222", linewidth=2.2, solid_capstyle="butt", zorder=5)
        ax.text(bar_x + bar_length / 2, bar_y + 900, bar_label, ha="center", va="bottom", fontsize=7, color="#222222", zorder=5)
        ax.annotate(
            "N",
            xy=(xmin + 0.10 * (xmax - xmin), ymax - 0.08 * (ymax - ymin)),
            xytext=(xmin + 0.10 * (xmax - xmin), ymax - 0.17 * (ymax - ymin)),
            ha="center",
            va="center",
            fontsize=8,
            fontweight="bold",
            arrowprops=dict(arrowstyle="-|>", color="#222222", lw=1.0),
            zorder=5,
        )

    handles = [Patch(facecolor=CROP_COLORS[key], edgecolor="#3A3A3A", label=key) for key in ["Corn", "Soybeans", "Other"]]
    fig.legend(
        handles=handles,
        loc="center right",
        bbox_to_anchor=(0.99, 0.865),
        title=f"CDL {crop_year}",
        fontsize=9,
        title_fontsize=9,
    )
    fig.suptitle(
        "Three growers, real USDA FIBOA field boundaries — all panels at the same "
        + ("37 × 37 mi scale" if UNITS.is_us else "60 × 60 km scale"),
        fontsize=13,
        fontweight="bold",
        y=0.985,
    )
    fig.text(
        0.01,
        0.012,
        f"Field colour = CDL {crop_year} primary class. Boundaries are FIBOA polygons, not survey records. Equal-area EPSG:5070.",
        fontsize=7.5,
        color="#555555",
    )
    save_figure(fig, out / "figures" / "fig_map_three_growers.png", dpi)


# --------------------------------------------------------------------------
# Analyses
# --------------------------------------------------------------------------


def boundary_analyses(boundary: pd.DataFrame, out: Path, warnings: list[str]) -> None:
    summary_rows = []
    for _, row in boundary.iterrows():
        summary_rows.append(
            {
                "grower": row["grower"],
                "field_id": row["field_id"],
                "county": row["county"],
                "state": row["state_label"],
                "state_fips": row["state_fips"],
                "area_ha": float(row["area_ha"]),
                "perimeter_km": float(row["perimeter_km"]),
                "polsby_popper": float(row["polsby_popper"]),
                "bbox_fill": float(row["bbox_fill"]),
                "n_parts": int(row["n_parts"]),
                "centroid_lat": float(row["centroid_lat"]),
                "centroid_lon": float(row["centroid_lon"]),
                "crop_boundary": str(row.get("crop", "")),
            }
        )
    summary = pd.DataFrame(summary_rows).sort_values(["grower", "field_id"])
    write_table(summary, out / "tables" / "boundary_fields.csv")

    stats_rows = []
    for grower in GROWER_ORDER:
        values = summary.loc[summary["grower"] == grower, "area_ha"].to_numpy()
        low, high = bootstrap_median_ci(values)
        stats_rows.append(
            {
                "grower": grower,
                "n": len(values),
                "mean_ha": float(np.mean(values)),
                "median_ha": float(np.median(values)),
                "median_ci_low": low,
                "median_ci_high": high,
                "q1_ha": float(np.percentile(values, 25)),
                "q3_ha": float(np.percentile(values, 75)),
                "sd_ha": float(np.std(values, ddof=1)),
                "cv_pct": float(100.0 * np.std(values, ddof=1) / np.mean(values)),
                "gini": gini(values),
                "min_ha": float(np.min(values)),
                "max_ha": float(np.max(values)),
            }
        )
    stats_table = pd.DataFrame(stats_rows)
    write_table(stats_table, out / "tables" / "boundary_size_stats.csv")

    groups = [summary.loc[summary["grower"] == grower, "area_ha"].to_numpy() for grower in GROWER_ORDER]
    kw = stats.kruskal(*groups)
    test_rows = [
        {
            "metric": "area_ha",
            "comparison": "overall",
            "group_a": "all",
            "group_b": "all",
            "n_a": sum(len(group) for group in groups),
            "n_b": 0,
            "statistic": float(kw.statistic),
            "p_raw": float(kw.pvalue),
            "p_holm": float(kw.pvalue),
            "cliffs_delta": np.nan,
        }
    ]
    pairs = [(GROWER_ORDER[i], GROWER_ORDER[j]) for i in range(len(GROWER_ORDER)) for j in range(i + 1, len(GROWER_ORDER))]
    raw_p = []
    pair_rows = []
    for left, right in pairs:
        a = summary.loc[summary["grower"] == left, "area_ha"].to_numpy()
        b = summary.loc[summary["grower"] == right, "area_ha"].to_numpy()
        result = stats.mannwhitneyu(a, b, alternative="two-sided")
        raw_p.append(float(result.pvalue))
        pair_rows.append(
            {
                "metric": "area_ha",
                "comparison": f"{left}_vs_{right}",
                "group_a": left,
                "group_b": right,
                "n_a": len(a),
                "n_b": len(b),
                "statistic": float(result.statistic),
                "p_raw": float(result.pvalue),
                "p_holm": np.nan,
                "cliffs_delta": cliffs_delta(a, b),
            }
        )
    adjusted = holm(raw_p)
    for row, value in zip(pair_rows, adjusted):
        row["p_holm"] = float(value)
    tests = pd.DataFrame(test_rows + pair_rows)
    write_table(tests, out / "tables" / "boundary_size_test.csv")

    corr_rows = []
    for grower in GROWER_ORDER:
        subset = summary[summary["grower"] == grower]
        rho, pvalue = stats.spearmanr(subset["area_ha"], subset["polsby_popper"])
        corr_rows.append(
            {
                "grower": grower,
                "n": len(subset),
                "rho": float(rho),
                "p_value": float(pvalue),
                "metric_x": "area_ha",
                "metric_y": "polsby_popper",
            }
        )
    rho_all, p_all = stats.spearmanr(summary["area_ha"], summary["polsby_popper"])
    corr_rows.append(
        {
            "grower": "pooled",
            "n": len(summary),
            "rho": float(rho_all),
            "p_value": float(p_all),
            "metric_x": "area_ha",
            "metric_y": "polsby_popper",
        }
    )
    corr = pd.DataFrame(corr_rows)
    write_table(corr, out / "tables" / "boundary_shape_corr.csv")

    spatial_rows = []
    for grower in GROWER_ORDER:
        subset = boundary[boundary["grower"] == grower]
        geoms = list(subset["_proj_geometry"])
        points = [geom.centroid for geom in geoms]
        n = len(points)
        distances = np.zeros((n, n))
        for i in range(n):
            for j in range(n):
                distances[i, j] = points[i].distance(points[j]) / 1000.0
        nearest = np.where(distances > 0, distances, np.inf).min(axis=1)
        overlaps = 0
        touches = 0
        for i in range(n):
            for j in range(i + 1, n):
                if geoms[i].intersection(geoms[j]).area > 1.0:
                    overlaps += 1
                if geoms[i].touches(geoms[j]) or geoms[i].distance(geoms[j]) < 1.0:
                    touches += 1
        spatial_rows.append(
            {
                "grower": grower,
                "n_fields": n,
                "nn_min_km": float(nearest.min()),
                "nn_median_km": float(np.median(nearest)),
                "nn_max_km": float(nearest.max()),
                "max_pairwise_km": float(distances.max()),
                "overlap_pairs": overlaps,
                "touching_pairs": touches,
                "centroid_lat_mean": float(subset["centroid_lat"].mean()),
                "centroid_lon_mean": float(subset["centroid_lon"].mean()),
            }
        )
    spatial = pd.DataFrame(spatial_rows)
    write_table(spatial, out / "tables" / "boundary_spatial.csv")
    if (spatial["overlap_pairs"] > 0).any():
        warnings.append("field polygons overlap within a grower; check boundary ingest")

    figure_boundary_size(boundary, stats_table, tests, out, args_dpi)
    figure_boundary_shape(boundary, corr, out, args_dpi)


def coupling_analyses(
    metrics: pd.DataFrame, params: dict, out: Path, warnings: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """GDD versus seasonal precipitation at the grower-year grain.

    Ten fields inside one farm resolve to only two to four NASA POWER nodes
    (0.5 x 0.625 degrees), and same-node fields share identical values, so field
    rows are pseudo-replicates. Everything here aggregates to grower-year first
    and then compares seasons, not fields.

    This analysis is post hoc exploratory: it was added after the initial EDA,
    so its exact p-values describe the stated design on these five seasons, not
    a pre-specified confirmatory hypothesis.
    """
    grain = (
        metrics.groupby(["grower", "year"], as_index=False)
        .agg(
            n_fields=("field_id", "nunique"),
            gdd_mean=("gdd", "mean"),
            gdd_sd=("gdd", "std"),
            precip_mean=("precip_mm", "mean"),
            precip_sd=("precip_mm", "std"),
        )
        .sort_values(["grower", "year"])
    )
    if grain.duplicated(["grower", "year"]).any():
        warnings.append("weather coupling: duplicate grower-year rows after aggregation")
    grain["gdd_anomaly"] = (
        grain["gdd_mean"] - grain.groupby("grower")["gdd_mean"].transform("mean")
    )
    grain["precip_anomaly"] = (
        grain["precip_mean"] - grain.groupby("grower")["precip_mean"].transform("mean")
    )
    write_table(grain, out / "tables" / "weather_grower_year.csv")

    season = (
        grain.groupby("year", as_index=False)
        .agg(gdd=("gdd_mean", "mean"), precip=("precip_mean", "mean"))
        .sort_values("year")
    )

    rows: list[dict] = []

    def add_row(
        scope: str,
        frame: pd.DataFrame,
        xcol: str,
        ycol: str,
        unit: str,
        note: str,
        p_method: str | None = None,
        pvalue: float | None = None,
        pearson_p: float | None = None,
    ) -> None:
        rho, p_value, n, method = spearman_exact(frame[xcol], frame[ycol])
        slope = (
            float(np.polyfit(frame[xcol], frame[ycol], 1)[0]) * 100.0 if n >= 3 else np.nan
        )
        rows.append(
            {
                "scope": scope,
                "unit": unit,
                "n": n,
                "spearman_rho": rho,
                "spearman_p": p_value if pvalue is None else pvalue,
                "p_method": p_method or method,
                "pearson_r": float(np.corrcoef(frame[xcol], frame[ycol])[0, 1]),
                "pearson_p": np.nan if pearson_p is None else pearson_p,
                "slope_mm_per_100gdd": slope,
                "note": note,
            }
        )

    if len(season) >= 3:
        add_row(
            "season_index",
            season,
            "gdd",
            "precip",
            "season",
            "Regional index: mean of the three farms per season.",
        )
    for grower in GROWER_ORDER:
        subset = grain[grain["grower"] == grower]
        if len(subset) >= 3:
            add_row(
                grower,
                subset,
                "gdd_mean",
                "precip_mean",
                "grower-year",
                "Within-farm season association.",
            )
    means = grain.groupby("grower", as_index=False).agg(
        gdd=("gdd_mean", "mean"), precip=("precip_mean", "mean")
    )
    if len(means) >= 3:
        add_row(
            "grower_means",
            means,
            "gdd",
            "precip",
            "location",
            "Descriptive only: three locations, and GDD/precipitation are rank-confounded with geography.",
            p_method="descriptive (n=3 locations)",
            pvalue=np.nan,
        )
    if len(grain) > 3:
        observed, permutation_p, n_perm, method = cluster_permutation_p(
            grain["gdd_anomaly"],
            grain["precip_anomaly"],
            grain["grower"],
            int(params.get("permutations", 10_000)),
            int(params.get("seed", 20261120)),
        )
        rows.append(
            {
                "scope": "pooled_within_grower",
                "unit": "grower-year (anomaly)",
                "n": n_perm,
                "spearman_rho": float(
                    stats.spearmanr(grain["gdd_anomaly"], grain["precip_anomaly"]).statistic
                ),
                "spearman_p": np.nan,
                "p_method": method,
                "pearson_r": observed,
                "pearson_p": permutation_p,
                "slope_mm_per_100gdd": float(
                    np.polyfit(grain["gdd_anomaly"], grain["precip_anomaly"], 1)[0]
                )
                * 100.0,
                "note": "Descriptive: growing seasons are shared across farms, so this p-value is optimistic.",
            }
        )

    corr = pd.DataFrame(rows)
    write_table(corr, out / "tables" / "weather_gdd_precip_corr.csv")

    sensitivity_rows = []
    scopes: list[tuple[str, pd.DataFrame, str, str]] = [
        ("season_index", season, "gdd", "precip")
    ]
    for grower in GROWER_ORDER:
        subset = grain[grain["grower"] == grower]
        if len(subset) >= 3:
            scopes.append((grower, subset, "gdd_mean", "precip_mean"))
    for scope, frame, xcol, ycol in scopes:
        for dropped in sorted(frame["year"].unique()):
            subset = frame[frame["year"] != dropped]
            rho, _p, n, _method = spearman_exact(subset[xcol], subset[ycol])
            sensitivity_rows.append(
                {
                    "scope": scope,
                    "dropped_year": int(dropped),
                    "n": n,
                    "spearman_rho": rho,
                }
            )
    write_table(
        pd.DataFrame(sensitivity_rows),
        out / "tables" / "weather_gdd_precip_sensitivity.csv",
    )
    return grain, corr


def corn_after_corn_analyses(
    primary: pd.DataFrame, params: dict, out: Path, warnings: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Corn-after-corn rates by transition year plus a within-field null test."""
    grower_map = primary.drop_duplicates("field_id").set_index("field_id")["grower"]
    pivot = primary.pivot(index="field_id", columns="year", values="primary_crop")
    years = sorted(int(year) for year in pivot.columns)
    if len(years) < 2:
        warnings.append("corn-after-corn: fewer than two CDL years available")
        return pd.DataFrame(), pd.DataFrame()
    pivot = pivot[years].copy()
    pivot.insert(0, "grower", grower_map.reindex(pivot.index))

    rows = []
    for grower in GROWER_ORDER:
        subset = pivot[pivot["grower"] == grower]
        for from_year, to_year in zip(years[:-1], years[1:]):
            eligible = subset[subset[from_year].astype(str).str.lower() == "corn"]
            total = int(len(eligible))
            if total == 0:
                continue
            successes = int(
                (eligible[to_year].astype(str).str.lower() == "corn").sum()
            )
            low, high = wilson_interval(successes, total)
            rows.append(
                {
                    "grower": grower,
                    "from_year": int(from_year),
                    "to_year": int(to_year),
                    "transition": f"{from_year}->{to_year}",
                    "n_eligible": total,
                    "corn_after_corn": successes,
                    "proportion": successes / total,
                    "wilson_low": low,
                    "wilson_high": high,
                }
            )
    by_transition = pd.DataFrame(rows)
    write_table(
        by_transition,
        out / "tables" / "cdl_corn_after_corn_by_transition.csv",
    )

    permutations = int(params.get("permutations", 10_000))
    seed = int(params.get("seed", 20261120))
    rng = np.random.default_rng(seed)
    persistence_rows = []
    for grower in GROWER_ORDER:
        subset = pivot[pivot["grower"] == grower]
        sequences = [
            [str(row[year]).lower() == "corn" for year in years]
            for _, row in subset.iterrows()
        ]
        if not sequences or len(years) < 3:
            continue
        observed = int(
            sum(
                sum(1 for left, right in zip(sequence, sequence[1:]) if left and right)
                for sequence in sequences
            )
        )
        eligible = int(
            sum(sum(1 for value in sequence[:-1] if value) for sequence in sequences)
        )
        if eligible == 0:
            continue
        null_counts = []
        for _ in range(permutations):
            total = 0
            for sequence in sequences:
                shuffled = list(rng.permutation(sequence))
                total += sum(
                    1 for left, right in zip(shuffled, shuffled[1:]) if left and right
                )
            null_counts.append(total)
        null_counts = np.asarray(null_counts)
        p_lower = (int(np.sum(null_counts <= observed)) + 1) / (permutations + 1)
        p_upper = (int(np.sum(null_counts >= observed)) + 1) / (permutations + 1)
        persistence_rows.append(
            {
                "grower": grower,
                "n_fields": len(sequences),
                "n_years": len(years),
                "eligible_transitions": eligible,
                "observed_corn_after_corn": observed,
                "observed_rate": observed / eligible,
                "null_mean": float(null_counts.mean()),
                "null_q025": float(np.quantile(null_counts, 0.025)),
                "null_q975": float(np.quantile(null_counts, 0.975)),
                "p_lower": p_lower,
                "p_upper": p_upper,
                "p_two_sided": min(1.0, 2.0 * min(p_lower, p_upper)),
                "permutations": permutations,
                "seed": seed,
                "note": "Null shuffles year order within each field, preserving each field's corn-year count.",
            }
        )
    persistence = pd.DataFrame(persistence_rows)
    write_table(
        persistence,
        out / "tables" / "cdl_corn_after_corn_persistence.csv",
    )
    return by_transition, persistence


def weather_analyses(weather: pd.DataFrame, metrics: pd.DataFrame, out: Path, warnings: list[str], params: dict) -> None:
    write_table(metrics, out / "tables" / "weather_seasonal_metrics.csv")
    metric_columns = ["gdd", "precip_mm", "frost_free_days", "longest_dry_run", "max_30d_precip"]
    field_means = (
        metrics.groupby(["grower", "field_id"])[metric_columns].mean().reset_index()
    )
    summary_rows = []
    for grower in GROWER_ORDER:
        subset = field_means[field_means["grower"] == grower]
        for metric in metric_columns:
            values = subset[metric].dropna().to_numpy()
            if not len(values):
                continue
            summary_rows.append(
                {
                    "grower": grower,
                    "metric": metric,
                    "n_fields": len(values),
                    "mean": float(np.mean(values)),
                    "sd": float(np.std(values, ddof=1)) if len(values) > 1 else np.nan,
                    "min": float(np.min(values)),
                    "max": float(np.max(values)),
                }
            )
    summary = pd.DataFrame(summary_rows)
    write_table(summary, out / "tables" / "weather_grower_stats.csv")

    year_summary = (
        metrics.groupby(["grower", "year"], as_index=False)
        .agg(
            n_fields=("field_id", "nunique"),
            gdd_mean=("gdd", "mean"),
            precip_mean=("precip_mm", "mean"),
            precip_sd=("precip_mm", "std"),
            frost_free_days_mean=("frost_free_days", "mean"),
            longest_dry_run_mean=("longest_dry_run", "mean"),
        )
        .sort_values(["grower", "year"])
    )
    write_table(year_summary, out / "tables" / "weather_year_summary.csv")

    variability_rows = []
    for grower in GROWER_ORDER:
        subset = year_summary[year_summary["grower"] == grower]
        for metric, label in (("gdd_mean", "gdd"), ("precip_mean", "precip_mm")):
            values = subset[metric].dropna().to_numpy()
            if len(values) < 2:
                continue
            variability_rows.append(
                {
                    "grower": grower,
                    "metric": label,
                    "n_years": len(values),
                    "year_mean": float(np.mean(values)),
                    "year_sd": float(np.std(values, ddof=1)),
                    "year_cv_pct": float(100.0 * np.std(values, ddof=1) / np.mean(values)),
                    "min_year_mean": float(np.min(values)),
                    "max_year_mean": float(np.max(values)),
                }
            )
    write_table(
        pd.DataFrame(variability_rows),
        out / "tables" / "weather_year_variability.csv",
    )

    test_rows = []
    for metric in metric_columns:
        groups = []
        labels = []
        for grower in GROWER_ORDER:
            values = (
                field_means.loc[field_means["grower"] == grower, metric].dropna().to_numpy()
            )
            if len(values) >= 2:
                groups.append(values)
                labels.append(grower)
        if len(groups) < 2:
            continue
        result = stats.kruskal(*groups)
        test_rows.append(
            {
                "metric": metric,
                "comparison": "overall",
                "group_a": "all",
                "group_b": "all",
                "n_a": sum(len(group) for group in groups),
                "n_b": 0,
                "statistic": float(result.statistic),
                "p_raw": float(result.pvalue),
                "p_holm": float(result.pvalue),
                "cliffs_delta": np.nan,
            }
        )
        pairs = [(labels[i], labels[j]) for i in range(len(labels)) for j in range(i + 1, len(labels))]
        raw_p, pair_rows = [], []
        for left, right in pairs:
            a = field_means.loc[field_means["grower"] == left, metric].dropna().to_numpy()
            b = field_means.loc[field_means["grower"] == right, metric].dropna().to_numpy()
            pair_result = stats.mannwhitneyu(a, b, alternative="two-sided")
            raw_p.append(float(pair_result.pvalue))
            pair_rows.append(
                {
                    "metric": metric,
                    "comparison": f"{left}_vs_{right}",
                    "group_a": left,
                    "group_b": right,
                    "n_a": len(a),
                    "n_b": len(b),
                    "statistic": float(pair_result.statistic),
                    "p_raw": float(pair_result.pvalue),
                    "p_holm": np.nan,
                    "cliffs_delta": cliffs_delta(a, b),
                }
            )
        for row, value in zip(pair_rows, holm(raw_p)):
            row["p_holm"] = float(value)
        test_rows.extend(pair_rows)
    tests = pd.DataFrame(test_rows)
    write_table(tests, out / "tables" / "weather_grower_tests.csv")

    variance_rows = []
    for metric in ["gdd", "precip_mm", "frost_free_days"]:
        row = variance_components(metrics, metric)
        if row:
            variance_rows.append(row)
    write_table(
        pd.DataFrame(variance_rows), out / "tables" / "weather_variance_components.csv"
    )

    curves_gdd = cumulative_curves(
        weather, params["start_md"], params["end_md"], params["gdd_base"], params["gdd_cap"], "gdd"
    )
    curves_precip = cumulative_curves(
        weather, params["start_md"], params["end_md"], params["gdd_base"], params["gdd_cap"], "precip"
    )
    figure_weather_gdd(
        curves_gdd,
        metrics,
        out,
        args_dpi,
        params["start_md"],
        params["end_md"],
        params["gdd_base"],
        params["gdd_cap"],
    )
    figure_weather_precip(
        curves_precip, metrics, out, args_dpi, params["start_md"], params["end_md"]
    )
    grower_year, coupling = coupling_analyses(metrics, params, out, warnings)
    figure_weather_coupling(grower_year, coupling, out, args_dpi)

    missing_frost = int(metrics["frost_free_days"].isna().sum())
    if missing_frost:
        warnings.append(f"{missing_frost} field-years have no complete frost pair")


def cdl_analyses(cdl: pd.DataFrame, primary: pd.DataFrame, rotation: pd.DataFrame, boundary: pd.DataFrame, out: Path, warnings: list[str], params: dict) -> None:
    primary_out = primary[
        [
            "grower",
            "field_id",
            "year",
            "primary_crop",
            "purity",
            "corn_pct",
            "soy_pct",
            "other_pct",
            "n_classes",
            "area_ha",
        ]
    ].sort_values(["grower", "field_id", "year"])
    write_table(primary_out, out / "tables" / "cdl_primary_crop.csv")

    transitions = transition_table(primary)
    if transitions.empty:
        warnings.append("no crop transitions could be derived")
        return
    write_table(transitions, out / "tables" / "cdl_transitions.csv")

    matrix_rows = []
    for grower in GROWER_ORDER:
        subset = transitions[transitions["grower"] == grower]
        table = pd.crosstab(subset["from_crop"], subset["to_crop"])
        for from_crop in table.index:
            total = int(table.loc[from_crop].sum())
            for to_crop in table.columns:
                count = int(table.loc[from_crop, to_crop])
                matrix_rows.append(
                    {
                        "grower": grower,
                        "from_crop": from_crop,
                        "to_crop": to_crop,
                        "count": count,
                        "probability": count / total if total else np.nan,
                    }
                )
    matrix = pd.DataFrame(matrix_rows).sort_values(["grower", "from_crop", "to_crop"])
    write_table(matrix, out / "tables" / "cdl_transition_matrix.csv")

    field_flags = (
        transitions.assign(is_corn_corn=(transitions["from_crop"].eq("Corn") & transitions["to_crop"].eq("Corn")).astype(int))
        .groupby(["grower", "field_id"])["is_corn_corn"]
        .agg(["sum", "count"])
        .reset_index()
        .rename(columns={"sum": "corn_corn_count", "count": "transitions"})
    )
    field_flags["has_corn_corn"] = (field_flags["corn_corn_count"] > 0).astype(int)
    write_table(field_flags, out / "tables" / "cdl_field_rotation_flags.csv")

    test_rows = []
    proportions = {}
    for grower in GROWER_ORDER:
        subset = field_flags[field_flags["grower"] == grower]
        successes = int(subset["has_corn_corn"].sum())
        total = int(len(subset))
        low, high = wilson_interval(successes, total)
        proportions[grower] = (successes, total)
        test_rows.append(
            {
                "comparison": f"{grower}_proportion",
                "n_fields": total,
                "fields_with_corn_corn": successes,
                "proportion": successes / total if total else np.nan,
                "wilson_low": low,
                "wilson_high": high,
                "odds_ratio": np.nan,
                "p_raw": np.nan,
                "p_holm": np.nan,
            }
        )
    pairs = [(GROWER_ORDER[i], GROWER_ORDER[j]) for i in range(len(GROWER_ORDER)) for j in range(i + 1, len(GROWER_ORDER))]
    raw_p, pair_rows = [], []
    for left, right in pairs:
        (a_success, a_total), (b_success, b_total) = proportions[left], proportions[right]
        table = [[a_success, a_total - a_success], [b_success, b_total - b_success]]
        odds_ratio, pvalue = stats.fisher_exact(table)
        raw_p.append(float(pvalue))
        pair_rows.append(
            {
                "comparison": f"{left}_vs_{right}",
                "n_fields": a_total + b_total,
                "fields_with_corn_corn": a_success + b_success,
                "proportion": np.nan,
                "wilson_low": np.nan,
                "wilson_high": np.nan,
                "odds_ratio": float(odds_ratio),
                "p_raw": float(pvalue),
                "p_holm": np.nan,
            }
        )
    for row, value in zip(pair_rows, holm(raw_p)):
        row["p_holm"] = float(value)
    tests = pd.DataFrame(test_rows + pair_rows)
    write_table(tests, out / "tables" / "cdl_transition_test.csv")

    contingency = np.array(
        [
            [int(field_flags[(field_flags["grower"] == grower)]["has_corn_corn"].sum()),
             int((field_flags["grower"] == grower).sum() - field_flags[(field_flags["grower"] == grower)]["has_corn_corn"].sum())]
            for grower in GROWER_ORDER
        ]
    )
    try:
        chi2, chi_p, _dof, _expected = stats.chi2_contingency(contingency)
        n_total = contingency.sum()
        cramers_v = float(np.sqrt(chi2 / (n_total * (min(contingency.shape) - 1)))) if n_total else np.nan
        write_table(
            pd.DataFrame(
                [
                    {
                        "test": "grower_x_corn_corn_field_flag",
                        "chi2": float(chi2),
                        "p_value": float(chi_p),
                        "cramers_v": cramers_v,
                        "n": int(n_total),
                    }
                ]
            ),
            out / "tables" / "cdl_transition_chisq.csv",
        )
    except ValueError as exc:
        warnings.append(f"chi-square skipped: {exc}")

    purity_rows = []
    field_purity = (
        primary.groupby(["grower", "field_id"], as_index=False)
        .agg(area_ha=("area_ha", "first"), purity=("purity", "mean"), other_pct=("other_pct", "mean"))
        .reset_index(drop=True)
    )
    for grower in list(GROWER_ORDER) + ["pooled"]:
        subset = field_purity if grower == "pooled" else field_purity[field_purity["grower"] == grower]
        if len(subset) < 3:
            continue
        rho, pvalue = stats.spearmanr(subset["area_ha"], subset["purity"])
        purity_rows.append(
            {
                "grower": grower,
                "n": len(subset),
                "rho_area_vs_purity": float(rho),
                "p_value": float(pvalue),
                "mean_purity": float(subset["purity"].mean()),
                "min_purity": float(subset["purity"].min()),
                "mean_other_pct": float(subset["other_pct"].mean()),
            }
        )
    write_table(
        pd.DataFrame(purity_rows), out / "tables" / "cdl_purity_diagnostics.csv"
    )

    area = boundary[["field_id", "area_ha", "grower"]].drop_duplicates("field_id")
    composition = cdl.drop(columns=["grower"]).merge(area, on="field_id", how="left")
    composition["crop_class"] = np.where(
        composition["crop_name"].str.lower() == "corn",
        "Corn",
        np.where(composition["crop_name"].str.lower() == "soybeans", "Soybeans", "Other"),
    )
    composition["crop_area_ha"] = composition["area_ha"] * composition["pct"] / 100.0
    mix = (
        composition.groupby(["grower", "year", "crop_class"], as_index=False)["crop_area_ha"]
        .sum()
    )
    totals = mix.groupby(["grower", "year"])["crop_area_ha"].transform("sum")
    mix["share"] = 100.0 * mix["crop_area_ha"] / totals
    write_table(mix, out / "tables" / "cdl_area_mix.csv")

    if not rotation.empty:
        rotation_out = rotation[
            [
                "grower",
                "field_id",
                "rotation_sequence",
                "rotation_count",
                "crop_diversity",
                "corn_years",
                "soybean_years",
                "predicted_next_crop",
                "rotation_confidence",
            ]
        ].sort_values(["grower", "field_id"])
        write_table(rotation_out, out / "tables" / "cdl_rotation_patterns.csv")

    figure_cdl_sequence(primary, rotation, out, args_dpi)
    figure_cdl_mix(mix, out, args_dpi)

    by_transition, persistence = corn_after_corn_analyses(primary, params, out, warnings)
    if not by_transition.empty:
        figure_corn_after_corn(by_transition, persistence, out, args_dpi)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def validate_inputs(
    specs: list[FarmSpec],
    boundaries: dict[str, gpd.GeoDataFrame],
    weather: dict[str, pd.DataFrame],
    cdl: dict[str, pd.DataFrame],
    params: dict,
) -> list[str]:
    warnings: list[str] = []
    for spec in specs:
        boundary = boundaries[spec.label]
        frame = weather[spec.label]
        cdl_frame = cdl[spec.label]
        field_ids = set(boundary["field_id"].astype(str))
        weather_ids = set(frame["field_id"].astype(str))
        if field_ids != weather_ids:
            warnings.append(
                f"{spec.label}: weather fields != boundary fields "
                f"({sorted(field_ids - weather_ids)} / {sorted(weather_ids - field_ids)})"
            )
        cdl_ids = set(cdl_frame["field_id"].astype(str))
        if field_ids != cdl_ids:
            warnings.append(
                f"{spec.label}: CDL fields != boundary fields "
                f"({sorted(field_ids - cdl_ids)} / {sorted(cdl_ids - field_ids)})"
            )
        expected_rows = len(field_ids) * ((params["weather_end_year"] - params["weather_start_year"] + 1) * 365 + 1)
        if len(frame) < expected_rows - 2:
            warnings.append(f"{spec.label}: weather has {len(frame)} rows, expected ~{expected_rows}")
        weather_years = sorted(int(year) for year in frame["date"].dt.year.unique())
        expected_years = list(
            range(params["weather_start_year"], params["weather_end_year"] + 1)
        )
        if weather_years != expected_years:
            warnings.append(
                f"{spec.label}: weather years {weather_years} != requested {expected_years}"
            )
        if frame[["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR"]].isna().any().any():
            warnings.append(f"{spec.label}: weather has nulls in required columns")
        if (frame.groupby(["field_id", "date"]).size() > 1).any():
            warnings.append(f"{spec.label}: duplicate field_id/date weather rows")
        years = sorted(cdl_frame["year"].unique())
        if len(years) < 5:
            warnings.append(f"{spec.label}: CDL has only {len(years)} years ({years})")
        if "crop_source" in cdl_frame.columns:
            sources = sorted(cdl_frame["crop_source"].dropna().astype(str).unique())
            if sources and any(source != "cdl" for source in sources):
                warnings.append(f"{spec.label}: non-CDL crop sources present: {sources}")
        if spec.label not in set(boundary["state_label"].unique()) and spec.label not in set(
            boundary["state_fips"].unique()
        ):
            warnings.append(
                f"{spec.label}: boundary state label {sorted(set(boundary['state_label']))} "
                f"does not match farm label"
            )
        if boundary["n_parts"].max() > 1:
            warnings.append(f"{spec.label}: multipart field polygons present")
        stored = pd.to_numeric(boundary.get("area_ha_est"), errors="coerce")
        if stored is not None and stored.notna().any():
            rel = ((boundary["area_ha"] - stored).abs() / stored).dropna()
            if float(rel.max()) > 1e-3:
                warnings.append(
                    f"{spec.label}: stored area differs from recomputed area by up to {float(rel.max()):.3%}"
                )
    if len(specs) < 2:
        warnings.append("fewer than two growers; cross-grower comparisons are not meaningful")
    return warnings


def run(args: argparse.Namespace) -> int:
    global GROWER_ORDER, GROWER_COLORS, args_dpi
    set_units(args.units)
    args_dpi = args.dpi
    specs = args.farm
    GROWER_ORDER = [spec.label for spec in specs]
    if len(set(GROWER_ORDER)) != len(GROWER_ORDER):
        raise SystemExit("duplicate farm labels; pass distinct :LABEL values")
    GROWER_COLORS = {
        label: PALETTE[index % len(PALETTE)] for index, label in enumerate(GROWER_ORDER)
    }

    params = {
        "start_md": args.season_start[0] * 100 + args.season_start[1],
        "end_md": args.season_end[0] * 100 + args.season_end[1],
        "gdd_base": args.gdd_base,
        "gdd_cap": args.gdd_cap,
        "weather_start_year": args.weather_start_year,
        "weather_end_year": args.weather_end_year,
        "sections": args.sections,
        "dpi": args.dpi,
        "permutations": args.permutations,
        "seed": args.seed,
    }

    boundaries, weather, cdl, rotations, input_paths = {}, {}, {}, {}, []
    for spec in specs:
        boundaries[spec.label] = load_boundary(spec)
        weather_path = find_weather_path(spec, args.weather_start_year, args.weather_end_year)
        weather[spec.label] = load_weather(spec, args.weather_start_year, args.weather_end_year)
        cdl[spec.label], _ = load_cdl(spec)
        rotations[spec.label] = load_rotation(spec)
        for path in [
            farm_boundary_path(spec.grower, spec.farm),
            weather_path,
        ]:
            input_paths.append(path)
        for path in sorted(
            farm_table_path(spec.grower, spec.farm, "_").parent.glob("*_20??_cdl.csv")
        ):
            input_paths.append(path)
        rotation_path = farm_cdl_rotation_path(spec.grower, spec.farm)
        if rotation_path.exists():
            input_paths.append(rotation_path)

    warnings = validate_inputs(specs, boundaries, weather, cdl, params)
    for spec in specs:
        missing = set(boundaries[spec.label]["field_id"]) - set(weather[spec.label]["field_id"])
        if missing:
            raise SystemExit(f"{spec.label}: missing weather for {sorted(missing)}")

    if args.check_inputs:
        print("input check")
        for spec in specs:
            boundary = boundaries[spec.label]
            frame = weather[spec.label]
            cdl_frame = cdl[spec.label]
            centroids, cells = power_grid_summary(frame)
            print(
                f"  {spec.label:>4}  fields={len(boundary):>2}  "
                f"area={boundary['area_ha'].min() * UNITS.area_factor:.1f}-"
                f"{boundary['area_ha'].max() * UNITS.area_factor:.1f} {UNITS.area_short}  "
                f"weather_rows={len(frame):>6}  grid={cells} cells/{centroids} centroids  "
                f"cdl_years={sorted(int(year) for year in cdl_frame['year'].unique())}  "
                f"counties={sorted(boundary['county'].unique())}"
            )
        if warnings:
            print("warnings:")
            for warning in warnings:
                print(f"  - {warning}")
        else:
            print("warnings: none")
        print("check complete; no files written")
        return 0

    out = Path(args.out).expanduser().resolve()
    if out.exists() and any(out.iterdir()) and not args.overwrite:
        raise SystemExit(f"output directory is not empty: {out} (use --overwrite)")
    (out / "figures").mkdir(parents=True, exist_ok=True)
    (out / "tables").mkdir(parents=True, exist_ok=True)

    apply_style()
    all_boundary = gpd.GeoDataFrame(
        pd.concat(boundaries.values(), ignore_index=True), geometry="_proj_geometry", crs="EPSG:5070"
    )
    all_weather = pd.concat(weather.values(), ignore_index=True)
    all_cdl = pd.concat(cdl.values(), ignore_index=True)
    all_rotation = pd.concat(
        [frame for frame in rotations.values() if not frame.empty], ignore_index=True
    ) if any(not frame.empty for frame in rotations.values()) else pd.DataFrame()

    area = all_boundary[["field_id", "area_ha", "grower"]].drop_duplicates("field_id")
    primary = primary_crop_table(all_cdl, area)

    # Map crop colour from the latest CDL year available.
    latest_year = int(primary["year"].max())
    latest = primary[primary["year"] == latest_year][["field_id", "primary_crop"]]
    boundary_with_crop = all_boundary.merge(
        latest.rename(columns={"primary_crop": "crop_latest"}), on="field_id", how="left"
    )
    boundary_with_crop["crop_latest"] = boundary_with_crop["crop_latest"].fillna(
        boundary_with_crop.get("crop", "")
    )

    sections = set(args.sections)
    if "boundaries" in sections:
        boundary_analyses(all_boundary, out, warnings)
    if "weather" in sections:
        metrics = seasonal_metrics(
            all_weather, params["start_md"], params["end_md"], params["gdd_base"], params["gdd_cap"]
        )
        weather_analyses(all_weather, metrics, out, warnings, params)
    if "cdl" in sections:
        cdl_analyses(all_cdl, primary, all_rotation, all_boundary, out, warnings, params)
    if "map" in sections:
        figure_map(boundary_with_crop, out, 300, warnings, latest_year)
        field_index = (
            boundary_with_crop[["field_id", "grower", "county", "area_ha", "centroid_lat", "centroid_lon"]]
            .assign(crop_year=latest_year, crop=boundary_with_crop["crop_latest"])
        )
        write_table(field_index, out / "tables" / "map_field_index.csv")

    figures = sorted((out / "figures").glob("*.png"))
    tables = sorted((out / "tables").glob("*.csv"))
    packages = {}
    for name in ["numpy", "pandas", "scipy", "matplotlib", "geopandas", "shapely", "pyproj"]:
        try:
            module = __import__(name)
            packages[name] = getattr(module, "__version__", "unknown")
        except Exception:  # pragma: no cover
            packages[name] = "missing"

    grid_summary = {spec.label: power_grid_summary(weather[spec.label]) for spec in specs}
    manifest = {
        "run": {
            "label": out.name,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sections": sorted(sections),
            "farm_specs": [
                {"grower": spec.grower, "farm": spec.farm, "label": spec.label}
                for spec in specs
            ],
        },
        "parameters": params,
        "units": {
            "system": UNITS.key,
            "area": UNITS.area,
            "distance": UNITS.distance,
            "temperature": UNITS.temp,
            "gdd": UNITS.gdd,
            "precipitation": UNITS.precip,
            "wind": UNITS.wind,
            "gdd_base_display": UNITS.temperature(params["gdd_base"]),
            "gdd_cap_display": UNITS.temperature(params["gdd_cap"]),
            "conversion_factors": {
                "ha_to_acres": HA_TO_ACRES,
                "km_to_miles": KM_TO_MILES,
                "mm_to_inches": MM_TO_INCHES,
                "ms_to_mph": MS_TO_MPH,
                "c_to_f_scale": C_TO_F_SCALE,
                "c_to_f_offset": C_TO_F_OFFSET,
                "mm_per_100gdd_to_in_per_100gdd_f": MM_PER_100GDD_TO_IN_PER_100GDD_F,
            },
            "note": (
                "Tests and correlations are computed on metric source values; "
                "conversion is applied only when tables and figures are written. "
                "Spearman rho, Pearson r, p-values, and Cliff's delta are invariant "
                "under these linear rescaling choices."
            ),
            "source": "metric" if UNITS.is_us else None,
        },
        "methods": {
            "weather_coupling": {
                "unit": "grower-year mean of the 10 field values; the 10 fields map to 2-4 NASA POWER cells, so field rows are pseudo-replicates",
                "design": "post hoc exploratory; added after the initial EDA, so p-values are not confirmatory",
                "within_grower_test": "exact two-sided Spearman permutation for n <= 8; asymptotic otherwise",
                "pooled_test": "Pearson r on grower-demeaned values with year-shuffle permutation within grower",
                "permutations": int(params["permutations"]),
                "seed": int(params["seed"]),
                "caveat": "seasons are shared across farms, so the pooled p-value is optimistic",
            },
            "rotation_trends": {
                "unit": "field",
                "transition_denominator": "fields with corn in the from-year",
                "interval": "Wilson 95%",
                "persistence_null": "within-field year shuffle preserving each field's corn-year count",
                "permutations": int(params["permutations"]),
                "seed": int(params["seed"]),
            },
        },
        "row_counts": {
            "fields": int(len(all_boundary)),
            "weather_rows": int(len(all_weather)),
            "cdl_rows": int(len(all_cdl)),
            "weather_fields": int(all_weather["field_id"].nunique()),
        },
        "weather_grid": {
            "note": (
                "NASA POWER MERRA-2 nodes are 0.5 degrees latitude x 0.625 degrees "
                "longitude; fields that snap to the same node receive identical daily values"
            ),
            "by_grower": {
                label: {"field_centroids": centroids, "power_cells": cells}
                for label, (centroids, cells) in grid_summary.items()
            },
        },
        "inputs": [
            {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in sorted(set(input_paths))
        ],
        "outputs": {
            "figures": [path.name for path in figures],
            "tables": [path.name for path in tables],
        },
        "packages": packages,
        "warnings": warnings,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (out / "README.txt").write_text(
        "Cross-grower field-level EDA outputs\n"
        f"Generated: {manifest['run']['generated_at']}\n"
        f"Growers: {', '.join(GROWER_ORDER)}\n"
        f"Units: {UNITS.key} ({UNITS.area}, {UNITS.distance}, {UNITS.precip}, "
        f"{UNITS.temp}, {UNITS.gdd}, {UNITS.wind})\n"
        f"Figures: {len(figures)}  Tables: {len(tables)}\n\n"
        "See manifest.json for inputs, hashes, parameters, and warnings.\n"
        "This directory is a reusable analysis run, not the final report.\n",
        encoding="utf-8",
    )

    print(f"wrote {len(figures)} figures and {len(tables)} tables to {out}")
    if warnings:
        print("warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-grower field-level EDA (static figures, tables, manifest)."
    )
    parser.add_argument(
        "--farm",
        action="append",
        type=parse_farm,
        required=True,
        metavar="GROWER/FARM[:LABEL]",
        help="Farm to include; repeat for each grower",
    )
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument(
        "--sections",
        default="boundaries,weather,cdl,map",
        help="Comma-separated sections: boundaries,weather,cdl,map",
    )
    parser.add_argument("--season-start", type=parse_md, default=(5, 1), metavar="MM-DD")
    parser.add_argument("--season-end", type=parse_md, default=(9, 30), metavar="MM-DD")
    parser.add_argument("--gdd-base", type=float, default=10.0)
    parser.add_argument("--gdd-cap", type=float, default=30.0)
    parser.add_argument(
        "--units",
        choices=("metric", "us"),
        default="metric",
        help=(
            "Presentation units for tables and figures (default: metric). "
            "Calculations always use the metric source; 'us' converts acres, "
            "miles, inches, degrees Fahrenheit, degree-days Fahrenheit, and mph."
        ),
    )
    parser.add_argument("--weather-start-year", type=int, default=2021)
    parser.add_argument("--weather-end-year", type=int, default=2025)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument(
        "--permutations",
        type=int,
        default=10_000,
        help="Monte Carlo draws for permutation tests (default 10000)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20261120,
        help="Random seed for permutation tests",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--check-inputs", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    sections = [part.strip() for part in args.sections.split(",") if part.strip()]
    valid = {"boundaries", "weather", "cdl", "map"}
    unknown = set(sections) - valid
    if unknown:
        parser.error(f"unknown sections: {sorted(unknown)}")
    args.sections = sections
    if args.gdd_cap <= args.gdd_base:
        parser.error("--gdd-cap must exceed --gdd-base")
    start_md = args.season_start[0] * 100 + args.season_start[1]
    end_md = args.season_end[0] * 100 + args.season_end[1]
    if end_md <= start_md:
        parser.error("--season-end must be after --season-start within a calendar year")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
