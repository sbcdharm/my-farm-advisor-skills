# Assignment #2 — field-level EDA report

A one-time, offline single-page report built from the reusable
`eda/field-level-eda` workflow. Open `index.html` in any browser; no server,
fonts, scripts, or network access are required.

## What is here

```
assignment-2-eda/
├── index.html                 # single-page report
├── manifest.json              # run provenance: inputs, hashes, parameters, methods
├── figures/                   # 9 optimized PNGs (7 existing + 2 new)
│   ├── fig_boundary_size_by_grower.png
│   ├── fig_boundary_shape_vs_size.png
│   ├── fig_weather_gdd_accumulation.png
│   ├── fig_weather_seasonal_precip.png
│   ├── fig_weather_gdd_precip_coupling.png          # new (W3)
│   ├── fig_cdl_crop_sequence_grid.png
│   ├── fig_cdl_area_weighted_crop_mix.png
│   ├── fig_cdl_corn_after_corn_by_year.png          # new (C3)
│   └── fig_map_three_growers.png
└── tables/                    # 26 derived CSVs (21 existing + 5 new)
```

The five new tables are:

- `weather_grower_year.csv` — grower-year weather means and anomalies
- `weather_gdd_precip_corr.csv` — coupling statistics at four scopes
- `weather_gdd_precip_sensitivity.csv` — leave-one-season-out Spearman ρ
- `cdl_corn_after_corn_by_transition.csv` — corn→corn rate per transition
- `cdl_corn_after_corn_persistence.csv` — observed vs within-field shuffle null

## Data window and grain

| Item | Value |
|---|---|
| Growers | IL (Iroquois), IA (Kossuth), NE (York) |
| Fields | 30 (10 per grower) |
| Boundary area | 14,546.32 acres (5,886.69 ha source) |
| Weather | 2021–2025, May 1 – Sep 30, NASA POWER 0.5° × 0.625° (4 cells IL, 3 each IA/NE) |
| GDD | base 50 °F, cap 86 °F (10 °C / 30 °C source) |
| Crop history | 2019–2023, USDA CDL 30 m primary class |
| Weather/CDL overlap | 2021–2023 |
| Permutations | 10,000, seed 20261120 |
| Units | U.S. customary presentation (`--units us`): acres, °F, °F·days, inches, miles, mph |

The two new analyses deliberately aggregate to **grower-year** before
correlating weather. Each field centroid snaps to a NASA POWER node, and the ten
fields per farm resolve to only four cells in IL and three each in IA and NE, so
field rows are pseudo-replicates of a few values. The season-level coupling test
therefore has n = 5, and the pooled grower-demeaned correlation is descriptive
only. Rotation transitions use field-conditional denominators (fields with corn
in the from-year), Wilson intervals, and a within-field year-shuffle persistence
test.

The coupling analysis is **post hoc and exploratory**: it was added after the
initial EDA, once the seasonal weather tables had been examined. Its exact
p-values are reported as computed, but they describe a data-chosen design and
are not confirmatory.

## Key results

- **Boundaries:** no statistically detectable difference in field size was found
  between IL and IA in this sample (Holm p = 0.97); that is not evidence of equal
  size distributions. NE is smaller and uniform (166.1–171.3 acres) because of the
  sampling band. IA alone shows an area–compactness trade-off
  (ρ = −0.88, p = 0.0008).
- **Weather:** NE is hottest (3,348.6 °F·d) and driest (15.9 in); IA is coolest
  (2,926.6 °F·d) and wettest (19.2 in). 75% of GDD variance is between growers;
  52% of precipitation variance is between years.
- **Heat–rain coupling (post hoc, exploratory):** the regional season index is
  perfectly inverse (ρ = −1.00, exact p = 0.0167, n = 5). IA and NE are −0.90 each
  (exact p = 0.083); IL is +0.40 (p = 0.517) and unstable under leave-one-out. The
  pooled anomaly r = −0.73 is descriptive (cluster p = 0.017).
- **Crop history:** 9/10 IL and 9/10 IA fields alternate corn–soy; 9/10 NE
  fields contain corn→corn, and 4/10 are corn in all five years.
- **Corn after corn:** NE is steady at 75.0–88.9% across all four transitions
  (27/34 pooled) and sits at its within-field chance expectation (27 observed
  vs 28.0 expected, p = 0.71). IL and IA are more alternating than chance
  (p = 0.0002 and p = 0.0012).

## Provenance

The report is a snapshot of the run at:

```
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/assignment-2/
```

`manifest.json` in this folder is copied verbatim from that run. It records
input SHA-256 hashes, row counts, package versions, the permutation seed, a
`units` block with the presentation system and conversion factors, and a
`methods` block naming the analysis unit and test for each new analysis.

Runner: `my-farm-advisor/data-pipeline/src/scripts/eda/eda_multifarm.py`

Regenerate:

```bash
export DATA_PIPELINE_DATA_ROOT=/path/to/my-farm-advisor-runtime
cd my-farm-advisor/data-pipeline
./scripts/install.sh --non-interactive --force-refresh --no-install-deps --persist none

cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/eda/eda_multifarm.py \
  --farm il-grower/illinois-demo-farm:IL \
  --farm ia-grower/iowa-demo-farm:IA \
  --farm ne-grower/nebraska-demo-farm:NE \
  --out "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/assignment-2" \
  --units us --permutations 10000 --overwrite
```

## Reading rules

- The report is **observational**. Correlations are described, not explained.
- Five weather seasons and five CDL years are short records. At n = 5 the
  smallest attainable two-sided exact p is 0.0167, and only a near-perfect rank
  reversal reaches it.
- Three sampled locations cannot separate a heat–rainfall trade-off from
  geography. The three grower means are not tested.
- NE field-size uniformity is a sampling choice, not a claim about York County.
- CDL is a 30 m annual primary class; double crops and cover crops are not
  resolved.
- Units are a presentation choice. `--units us` converts area, distance,
  temperature, GDD, precipitation, and wind at write time; the tests run on the
  metric source values. Spearman ρ, Pearson r, p-values, and Cliff's δ are
  invariant under these linear rescalings. No yield data exist, so no bu/ac
  figures are reported.
- No yield, irrigation, soil-water, management, or economic data are included.

## Validation

- At the time of the extended run, all 21 pre-existing tables and 7 pre-existing
  figures were verified byte-identical (SHA-256); only the 5 new tables and
  2 new figures were added.
- A later presentation pass regenerated all 26 tables and all 9 figures with
  `--units us`. Unit-free and statistical columns were verified unchanged;
  unit-bearing columns were checked by back-conversion to the metric run. A
  default `--units metric` rerun reproduces all 26 tables and all 9 figures
  byte-for-byte. The manifest records the unit system, display GDD base/cap, and
  conversion factors.
- An earlier QC pass corrected the NASA POWER grid-cell description, softened the
  IL–IA size comparison to "no detectable difference", fixed the Figure B1
  range, and marked the coupling analysis as post hoc.
- All figure annotations trace to a CSV under `tables/`.
- All local links in `index.html` resolve to files in this folder.
- `./scripts/validate.sh` passes from the repository root.

## Repository status

This folder is a deliverable snapshot. It is not committed or pushed. Generated
runtime outputs remain under `${DATA_PIPELINE_DATA_ROOT}` per repository policy;
the assignment copy exists only to make the report portable and reviewable.
