---
name: field-level-eda
description: Cross-grower field-level exploratory analysis for real field boundaries, NASA POWER weather, and USDA CDL crop history. Produces static PNG figures, CSV tables, and a reproducibility manifest from a set of grower/farm pairs.
version: 1.1.0
author: Boreal Bytes
tags: [eda, multi-farm, boundaries, weather, cdl, geospatial, static-output]
---

# Workflow: field-level-eda

## Description

Answer field-level questions across several growers at once: how field size and
shape differ, how growing-season heat and water compare, whether heat and
rainfall move together across seasons, how crop sequences and rotation behaviour
differ, and whether rotation patterns are steady or episodic. The run emits
static matplotlib PNGs, tidy CSV tables, and a `manifest.json` that records
inputs, parameters, and package versions. It does not write narrative or a
report.

This is the reusable subskill. A one-time report is assembled separately from
the manifest and selected figures; do not put report prose into the runner.

## When to Use This Workflow

- You have two or more farms with boundaries, weather, and CDL history.
- You need field-level EDA that compares fields, fields within a grower, and
  growers against each other.
- You need stable, inspectable numbers behind every chart (each caption value
  is also a CSV cell).
- You need a static overview map of all growers with real field boundaries.

Do not use it for yield analysis, irrigation inference, soil, terrain, or
imagery — those inputs are not part of this workflow.

## Inputs

All inputs come from the runtime tree, never the checkout:

```
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/farms/<farm>/
├── boundary/field_boundaries.geojson          # required
├── derived/tables/*_weather_<start>_<end>.csv # required
├── derived/tables/*_<year>_cdl.csv            # required, 5 years
└── derived/tables/*_crop_rotation.csv         # optional summary
```

Required boundary properties: `field_id`, `area_ha_est`, `crop`, `county`,
`state` (or `state_fips`). Weather columns: `field_id`, `date`, `T2M`,
`T2M_MAX`, `T2M_MIN`, `PRECTOTCORR`, `ALLSKY_SFC_SW_DWN`, `RH2M`, `WS10M`.
CDL columns: `field_id`, `year`, `crop_name`, `pct`, `pixel_count`,
`crop_source`.

## Runner

The runner lives in the data-pipeline runtime source so `install.sh` deploys it:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
PY="${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python"

# Validate paths, schema, row counts; writes nothing
"$PY" scripts/eda/eda_multifarm.py \
  --farm il-grower/illinois-demo-farm:IL \
  --farm ia-grower/iowa-demo-farm:IA \
  --farm ne-grower/nebraska-demo-farm:NE \
  --out "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/assignment-2" \
  --check-inputs
```

Remove `--check-inputs` to write outputs. Useful flags:

| Flag | Default | Meaning |
|---|---|---|
| `--farm grower/farm[:LABEL]` | required, repeatable | Farm to include; label defaults to farm slug |
| `--out DIR` | required | Output root (created; refuses non-empty without `--overwrite`) |
| `--sections` | `boundaries,weather,cdl,map` | Re-run one family |
| `--season-start` / `--season-end` | `05-01` / `09-30` | Growing-season window (MM-DD) |
| `--gdd-base` / `--gdd-cap` | `10` / `30` | GDD base and upper temperature cap (°C, source units) |
| `--units` | `metric` | Presentation units: `metric` or `us` (acres, °F, °F·days, inches, miles, mph). Calculations stay metric |
| `--weather-start-year` / `--weather-end-year` | `2021` / `2025` | Weather file window |
| `--permutations` | `10000` | Monte Carlo draws for the coupling and rotation-trend tests; recorded in the manifest |
| `--seed` | `20261120` | Random seed for permutation tests |
| `--dpi` | `200` | PNG resolution (map uses 300) |
| `--overwrite` | off | Replace an existing non-empty output directory |

## Output contract

```
<out>/
├── figures/   # 9 PNGs, fixed names
├── tables/    # CSV metrics, tests, diagnostics
├── manifest.json
└── README.txt
```

`manifest.json` includes input paths and sha256 hashes, parameters, row counts,
package versions, warnings, a `units` block (system, display GDD base/cap, and
conversion factors), a methods block (analysis unit, test, permutations, seed),
and the generated file list. A separate report step can cite it without
re-running the analysis.

## Figures

| File | Question |
|---|---|
| `fig_boundary_size_by_grower.png` | How variable are field sizes within and across growers? |
| `fig_boundary_shape_vs_size.png` | Are larger fields geometrically simpler? |
| `fig_weather_gdd_accumulation.png` | How much heat accumulates, and how variable is it? |
| `fig_weather_seasonal_precip.png` | How much water arrives, when, and how reliable is it? |
| `fig_weather_gdd_precip_coupling.png` | Do hotter seasons also arrive drier, within and across growers? |
| `fig_cdl_crop_sequence_grid.png` | What crop sequence has each field followed? |
| `fig_cdl_area_weighted_crop_mix.png` | What is the area-weighted corn–soy balance? |
| `fig_cdl_corn_after_corn_by_year.png` | Is corn-after-corn steady across transitions or concentrated in one? |
| `fig_map_three_growers.png` | Where are the growers and how are fields laid out? |

Per-figure variables, methods, and caveats:
[references/figure-catalog.md](references/figure-catalog.md). Formulas:
[references/metric-definitions.md](references/metric-definitions.md).

## Comparison analyses

- **Field size** — Kruskal–Wallis across growers, pairwise Mann–Whitney with
  Holm correction, Cliff's delta, median bootstrap CI, CV, and Gini. A narrow
  size range produced by a sampling band is labelled as such, not generalised.
- **Weather** — per-field five-year means are the analysis unit, reported in the
  selected presentation units; Kruskal–Wallis
  and pairwise Mann–Whitney with Holm correction cover GDD, seasonal
  precipitation, frost-free days, dry runs, and 30-day maxima, plus a balanced
  variance decomposition (`grower + field(grower) + year + residual`). NASA
  POWER nodes are 0.5° × 0.625°, so the ten fields per farm resolve to only two
  to four distinct cells and fields sharing a cell are exact duplicates;
  p-values describe the three sampled farm locations, not regional inference.
  Report effect sizes and year-to-year spread alongside. Statistical results are
  computed on metric source values and are invariant under the presentation
  conversion.
- **Rotation** — field-level transitions (fields with at least one corn→corn
  transition) with Fisher exact tests and Wilson CIs, transition matrices with
  row-normalised probabilities, and Cramér's V. Fields, not transitions, are the
  inferential unit.
- **Heat–rainfall coupling** — seasonal GDD vs seasonal precipitation at the
  grower-year grain. Three views: a regional season index (n = 5 seasons), each
  farm separately (n = 5), and grower-demeaned anomalies (descriptive, with a
  within-grower year-shuffle permutation). Leave-one-year-out sensitivity is
  reported. Three grower means are shown as a location gradient only; they are
  rank-confounded with geography and are not tested. This analysis is **post hoc
  exploratory**: it was added after the initial EDA, so report its exact
  p-values as computed but do not describe the hypothesis as pre-specified or
  confirmatory. Display slopes are converted to inches per 100 °F·days when
  `--units us` is selected; ρ, r, and p are unchanged.
- **Rotation trends** — corn→corn rate for each consecutive CDL transition
  (2019→2020 … 2022→2023), with the denominator restricted to fields that were
  corn in the from-year and Wilson CIs around each rate. A within-field
  year-shuffle test asks whether adjacency differs from what each field's
  corn-year count alone would produce. Transition rows are not independent trials.

## Method rules

- Precipitation is summed, never averaged over days. GDD uses daily extrema.
- Unit conversion is presentation-only. With `--units us`, area, distance,
  temperature, GDD, precipitation, and wind are converted at write time; never
  relabel an unconverted value. Tests, correlations, and p-values are computed on
  the metric source and are invariant under these linear rescalings.
- The season is a calendar window; planting dates are not in the data, so GDD is
  potential heat, not crop-stage heat.
- Area-weighted statements multiply field area by CDL class share.
- Within-grower spatial clustering is reported so fields are not presented as
  independent climate replicates.
- NASA POWER nodes are 0.5° × 0.625°; the ten fields in a farm usually fall in
  only two to four cells, so fields are pseudo-replicates of a few values, not
  ten independent observations. Treat weather contrasts as grower-level, not
  field-level, signals.
- Aggregate weather to grower-year before correlating two weather metrics. Never
  correlate daily or field-level weather rows: within-farm fields are
  pseudo-replicates of two to four grid cells.
- Use exact permutation p-values for rank correlations when n ≤ 8; the
  asymptotic Spearman approximation is anti-conservative at small n (ρ = −0.90 at
  n = 5 is p = 0.037 asymptotically but p = 0.083 exactly).
- CDL transition rows within a field are serially dependent. Report per-transition
  denominators (fields with the from-crop) and n/N; do not chi-square across
  transition-years.
- CDL purity (primary-class share) is reported alongside crop results; class
  shares below 100 % are edge/mixed pixels, not intercropping.

## Validation

1. `--check-inputs` (schema, unique IDs, state consistency, day counts, CDL
   years, area recomputation within 0.5 %, and field-centroid-to-grid-cell
   counts).
2. Full run, then confirm all nine PNGs exist and are non-blank.
3. Cross-check caption numbers against the emitted CSVs.
4. Re-run with the same seed and confirm permutation tables are reproducible.
5. `./scripts/validate.sh` from the repository root after doc or script changes.

## Limitations

- No yield, management, planting/harvest dates, or irrigation variables.
- Weather is bias-corrected reanalysis at each field centroid, not an in-field
  sensor.
- CDL is an annual 30 m classification; double crops and cover crops are not
  resolved, and the available window is five years.
- Five weather seasons and five CDL years are short records. A perfect rank
  correlation across five seasons is the smallest attainable two-sided p = 0.017
  and remains a statement about these seasons, not a climate normal.
- Three sampled farm locations cannot separate a heat–rainfall trade-off from
  the broader geographic gradient; between-grower coupling is descriptive only.
- FIBOA boundaries are not survey-grade; compactness is sensitive to slivers.

## Related

- [EDA index](../INDEX.md)
- [Compare guide](../eda-compare/GUIDE.md)
- [Correlate guide](../eda-correlate/GUIDE.md)
- [Time-series guide](../eda-time-series/GUIDE.md)
