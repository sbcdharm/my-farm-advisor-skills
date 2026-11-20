# Metric definitions

Exact definitions used by `eda_multifarm.py`. Calculations and tests use metric
source values. `--units us` is a presentation option that converts area,
distance, temperature, GDD, precipitation, and wind when tables and figures are
written; it never changes the underlying metric calculations.

## Presentation-unit conversions

| Quantity | Metric source | U.S. presentation | Factor |
|---|---|---|---|
| Area | hectares | acres | × 2.471053814671653 |
| Distance | kilometres | miles | × 0.621371192237334 |
| Temperature | °C | °F | × 9/5 + 32 |
| GDD | °C·days | °F·days | × 9/5 (base 10 °C → 50 °F, cap 30 °C → 86 °F) |
| Precipitation | millimetres | inches | ÷ 25.4 |
| Wind | m/s | mph | × 2.2369362920544 |
| Coupling slope | mm per 100 °C·d | inches per 100 °F·d | × (1/25.4) × (5/9) |

Spearman ρ, Pearson r, p-values, Cliff's δ, shares, and counts are invariant under
these linear rescalings. Sums of squares in the variance table are converted by
the square of the linear factor when shown in U.S. units, so the percentage
shares are unchanged.

## Boundary metrics

| Metric | Definition | Notes |
|---|---|---|
| Field area | Polygon area in `EPSG:5070` ÷ 10,000 | Compared with the boundary `area_ha_est`; a >0.5 % gap is a hard failure |
| Perimeter | Polygon length in `EPSG:5070` ÷ 1,000 (km) | Includes road-cut and sliver edges |
| Polsby–Popper | `4πA / P²`, range 0–1 | 1 = circle, π/4 ≈ 0.785 = square; scale-free |
| BBox fill | `area / ((maxx−minx)(maxy−miny))` | 1 = rectangle aligned to axes; sensitive to rotation |
| Parts | Number of polygon parts | All current fields are single-part |
| Nearest-neighbour distance | Minimum centroid distance to another field in the same farm (km) | Spatial-clustering diagnostic |
| Overlap | Intersection area > 1 m² for a field pair | Must be zero; anything else is a boundary defect |
| Gini | `2Σ(i·x_i)/(nΣx) − (n+1)/n` on sorted areas | 0 = equal areas; higher = acreage concentrated |
| Bootstrap median CI | 10,000 resamples, seed 42, 2.5–97.5 percentiles | Describes the sample, not the region |

## Weather metrics

Daily inputs: `T2M_MAX`, `T2M_MIN` (°C), `PRECTOTCORR` (mm), `ALLSKY_SFC_SW_DWN`
(kWh/m²/day), `RH2M` (%), `WS10M` (m/s).

| Metric | Definition |
|---|---|
| Growing degree day, daily | `max(0, (min(T2M_MAX, cap) + T2M_MIN) / 2 − base)` with `base=10 °C`, `cap=30 °C`; displayed as °F·days with `base=50 °F`, `cap=86 °F` |
| Seasonal GDD | Sum of daily GDD inside the season window |
| Seasonal precipitation | Sum of `PRECTOTCORR` inside the window (never a mean of daily values) |
| Dry day | `PRECTOTCORR < 1.0` mm |
| Longest dry run | Longest consecutive run of dry days inside the window |
| Max 30-day precipitation | Largest 30-day rolling sum inside the window |
| Last spring frost | Last date before 1 July with `T2M_MIN ≤ 0 °C` |
| First fall frost | First date on/after 1 July with `T2M_MIN ≤ 0 °C` |
| Frost-free days | `first fall frost − last spring frost` in days; `NaN` when either is missing |
| Seasonal means | Mean of daily `T2M`, solar, RH, and wind inside the window |

The season is a calendar window (default 1 May–30 September). Planting dates are
not available, so these are potential-heat and rainfall metrics, not crop-stage
metrics.

NASA POWER MERRA-2 nodes are 0.5° latitude × 0.625° longitude, node-centred.
Fields in these farms resolve to two to four distinct cells (IL 4, IA 3, NE 3);
fields sharing a cell have identical values, so field-to-field weather
differences are not a microclimate signal. Treat grower contrasts as
location-level comparisons.

## Season-coupling metrics

The coupling analysis aggregates weather to **grower-year** first (mean of the
10 fields), because within-farm field values are pseudo-replicates of two to
four grid cells.

This analysis is **post hoc exploratory**: it was added after the initial EDA,
so its exact p-values describe the stated design on these five seasons, not a
pre-specified confirmatory hypothesis.

| Metric | Definition | Notes |
|---|---|---|
| Grower-year GDD / precipitation | Mean seasonal total across the fields in one grower-year | Fields contribute a mean, never separate observations |
| GDD anomaly | Grower-year GDD minus that grower's five-year mean | Removes the between-farm level |
| Precipitation anomaly | Grower-year precipitation minus that grower's five-year mean | Same |
| Season index | Mean GDD and mean precipitation across growers within a year | n = number of seasons, not number of farms |
| Exact Spearman p | Two-sided permutation over all `n!` rank reorderings, used for n ≤ 8 | Avoids anti-conservative asymptotic p at n = 5 |
| Cluster permutation p | Pearson r on anomalies, recomputed after shuffling years within each grower | Preserves farm means; still optimistic because seasons are shared |
| Coupling slope | OLS slope of precipitation on GDD × 100 | Descriptive mm per 100 °C·d in the metric run; inches per 100 °F·d with `--units us`; not a causal effect |
| Leave-one-year-out ρ | Spearman ρ recomputed with one season removed | Fragility check, not a significance test |

## Crop-history metrics

| Metric | Definition |
|---|---|
| Primary crop | `crop_name` with the largest `pct` for a field-year |
| Primary-class share (purity) | `pct` of that primary class (0–100) |
| Other share | `100 − pct(Corn) − pct(Soybeans)`, floored at 0 |
| Transition | Consecutive-year pair of primary crops (2019→2020 … 2022→2023) |
| Corn→corn count | Adjacent corn–corn year pairs inside a field sequence |
| Corn→corn share (field) | Fields with at least one corn→corn transition ÷ all fields in the grower |
| Eligible transitions | Transition rows where the from-year crop is corn; the denominator for per-year rates |
| Transition-year rate | Corn→corn fields in one transition ÷ eligible fields in that transition |
| Corn→corn adjacency | Count of adjacent corn–corn year pairs inside a field sequence |
| Persistence null | Shuffle year order within each field, preserving its corn-year count; recompute adjacency |
| Field-level rotation flag | 1 when a field has at least one corn→corn transition |
| Area-weighted crop area | `field_area_ha × pct / 100` per class, summed by grower-year (shown as acres under `--units us`) |
| Cramér's V | `sqrt(χ² / (n·(min(r,c)−1)))` for grower × transition type |

Fields, not transitions, are the inferential unit: four transitions per field
are not independent observations. Per-transition rates therefore describe
timing, not independent trials; a chi-square across transition-years would
overstate evidence. The persistence test conditions on each field's corn-year
count, separating "lots of corn" from "corn follows corn more than frequency
alone would predict".

## Statistical helpers

| Statistic | Implementation |
|---|---|
| Kruskal–Wallis | `scipy.stats.kruskal` |
| Mann–Whitney U | `scipy.stats.mannwhitneyu`, two-sided |
| Holm correction | Step-down adjustment over the declared pairwise family |
| Cliff's delta | `(#(x>y) − #(x<y)) / (n_x·n_y)`, range −1…1 |
| Fisher exact | `scipy.stats.fisher_exact` on 2×2 field-level counts |
| Wilson interval | 95 % score interval for a binomial proportion |
| Spearman ρ | `scipy.stats.spearmanr`; exact permutation p for n ≤ 8, asymptotic otherwise |
| Cluster permutation | Within-grower year shuffle, 10,000 draws, recorded seed |
| Rotation persistence | Within-field year shuffle, 10,000 draws, exact-style Monte Carlo p |

## Variance decomposition

Balanced design with `G` growers, `F` fields per grower, `Y` years:

- `SS_grower = F·Y · Σ_g (mean_g − mean)²`
- `SS_field = Y · Σ_f (mean_f − mean_g(f))²`
- `SS_year = G·F · Σ_y (mean_y − mean)²`
- `SS_resid = SS_total − SS_grower − SS_field − SS_year`

Reported as percentage of total sum of squares. A large field-within share means
fields experience materially different weather; a small share means one weather
series per grower is effectively sufficient.
