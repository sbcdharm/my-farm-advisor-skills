# Growing an existing demo farm (dataset expansion)

Use this runbook when a grower already has fields and you need more fields, or a
new state grower, **without** re-running the full 16-step farm pipeline. It
preserves the existing fields, weather, CDL tables, imagery, terrain, soil, and
reports.

## Principles

- **Boundaries first, additive.** Re-run `ingest/download_csb_fields.py` with a
  larger `--max-fields` and the *same* bbox/prefix/flags. The script sorts by
  area, so the previous top-N keep their `field_id` numbering.
- **Never renumber or delete existing fields.** If the FIBOA dataset was
  re-published and a former top-N field drops out, restore the original
  features from the pre-change backup and merge only the new IDs.
- **Only run the steps you need.** For weather/CDL expansion run
  `ingest/download_weather.py` and `ingest/download_cdl.py` directly. Do not run
  `run_farm_pipeline.py`; it re-runs terrain, both soil steps, satellite, and
  every report.
- **State identity comes from the boundary.** Boundary `state` / `state_fips`
  is authoritative; `farm.json.state` is a convenience label only.

## State FIPS and CDL routing

`download_csb_fields.py` writes `state_fips` (derived from `--state-fips` or
`--state`). `download_cdl.py` resolves FIPS from `state_fips`, then from the
`state` abbreviation, and **raises** if neither resolves. It never falls back to
a default state. Verify the printed line before extraction:

```
State CDL coverage: 17
```

`reporting_bootstrap.ensure_canonical_data_tree` no longer overwrites an
existing `farm.json`; new farm metadata derives `state`/`country` from the farm
boundary.

## Recipe

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
PY="${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python"
```

1. Back up the farm's `boundary/`, `manifests/`, `derived/tables/`, and
   `farm.json`, and record `field_id` + `csb_id` + geometry hashes.
2. Expand boundaries (same bbox and flags as the original ingest):

   ```bash
   AG_GROWER_SLUG=il-grower AG_FARM_SLUG=illinois-demo-farm \
   "$PY" scripts/ingest/download_csb_fields.py \
     --bbox 40.485568 -88.131935 41.010341 -87.525661 \
     --min-area-ha 40 --max-fields 10 --field-prefix il-r \
     --country US --state IL --year 2025
   ```

   `--bbox` is **S W N E**. Use `--max-area-ha` to select a size band instead of
   the largest fields (for example `--min-area-ha 40 --max-area-ha 70` for
   pivot-scale parcels). Bands are size evidence only — they do not prove an
   irrigation system is installed.
3. Refresh weather for all fields (`--force` rewrites the combined CSV):

   ```bash
   AG_FARM_NAME="Illinois Demo Farm" "$PY" scripts/ingest/download_weather.py \
     --grower-slug il-grower --farm-slug illinois-demo-farm \
     --weather-backend zarr --weather-start-year 2021 --weather-end-year 2025 \
     --weather-time-standard lst --force
   ```
4. Refresh CDL history for all fields:

   ```bash
   AG_GROWER_SLUG=il-grower AG_FARM_SLUG=illinois-demo-farm \
     "$PY" scripts/ingest/download_cdl.py
   ```

   For a new state, pre-seed the five state rasters first:

   ```bash
   "$PY" scripts/ingest/download_cdl.py --raster-only --cdl-scope state \
     --cdl-state-fips 31 --cdl-years 2019,2020,2021,2022,2023
   ```

5. Verify: inventory row count, boundary `state`/`state_fips` per feature,
   unchanged `csb_id`/geometry for original fields, weather rows
   (`fields × days`), and a non-empty CDL rotation table for every field.

## New grower/farm skeleton

`download_csb_fields.py` requires the farm directory to exist, so create
`growers/<grower>/grower.json` and
`growers/<grower>/farms/<farm>/farm.json` (`country`, `state`) first.
