# Figure catalog

Each entry names the figure, the agricultural question, the exact variables,
the method, what a conclusion may say, and the caveats that must stay attached.
Every numeric annotation is also written to a table under `tables/`.

Figures are written in the selected presentation units. With `--units us`, areas
are acres, temperatures °F, GDD °F·days, precipitation inches, distances miles,
and wind mph. The calculations and tests remain metric; only the display changes.

---

## B1 · `fig_boundary_size_by_grower.png`

- **Question:** How variable are field sizes within each grower, and do the
  growers differ?
- **Variables:** `area_ha_est` recomputed from geometry (`EPSG:5070`), grower
  label; plotted in acres under `--units us` (hectares under the default).
- **Method:** jittered dot strip + box + median with 10,000-resample bootstrap
  95 % CI; n and CV annotated; `tables/boundary_size_stats.csv` and
  `tables/boundary_size_test.csv` (Kruskal–Wallis, pairwise Mann–Whitney with
  Holm, Cliff's delta).
- **May conclude:** IL/IA parcels have real within-grower spread (CV ≈ 15–20 %);
  no IL–IA difference is detected at 10 fields per farm (Holm p ≈ 0.97), which
  is not evidence that their size distributions are equal; the NE sample is
  uniform because it was drawn from a roughly 100–170 acre band.
- **Caveats:** the NE narrowness is a **sampling choice**, not evidence about
  York County; do not generalise the size range to the region.

## B2 · `fig_boundary_shape_vs_size.png`

- **Question:** Are larger fields geometrically simpler, and do growers' parcels
  differ in shape?
- **Variables:** area, perimeter, Polsby–Popper `4πA/P²`, bounding-box fill.
- **Method:** scatter with log area, per-grower Spearman ρ and p,
  `tables/boundary_shape_corr.csv`.
- **May conclude:** the size–shape relationship is grower-specific (strongly
  negative in IA, absent in IL/NE), so edge and headland expectations cannot be
  derived from area alone.
- **Caveats:** FIBOA polygons are not survey-grade; slivers and road cuts lower
  compactness.

## W1 · `fig_weather_gdd_accumulation.png`

- **Question:** How much heat accumulates in each growing season, and how much
  does it vary across fields and years?
- **Variables:** daily `T2M_MAX`, `T2M_MIN`; source GDD is `max(0,
  (min(TMAX,30)+TMIN)/2 − 10)` in °C·days, displayed as °F·days (base 50 °F, cap
  86 °F) under `--units us`; cumulative from 1 May.
- **Method:** small multiples by year; grower mean with field min–max ribbon;
  season totals annotated; `tables/weather_seasonal_metrics.csv`.
- **May conclude:** NE accumulates the most heat in every year; IL usually
  exceeds IA, but the IL–IA gap is small. Between-year spread exceeds
  within-grower field spread.
- **Caveats:** calendar window, not crop-stage GDD; reanalysis at 2 m; NASA
  POWER nodes are 0.5° × 0.625°, so the ten fields per farm resolve to two to
  four cells and the field ribbon reflects between-cell differences, not
  microclimate; no irrigation or soil moisture.

## W2 · `fig_weather_seasonal_precip.png`

- **Question:** How much water arrives during the season, when, and how reliable
  is it?
- **Variables:** `PRECTOTCORR`; cumulative seasonal curves and seasonal totals;
  dry-run and 30-day-max diagnostics in the table.
- **Method:** left panel = five-year mean cumulative curve per grower with
  year min–max band; right panel = seasonal totals by grower and year.
- **May conclude:** NE has the lowest mean seasonal rainfall in 4 of 5 years,
  but Iowa has the largest year-to-year spread (CV 28 % vs 22 % NE, 14 % IL);
  IL is the most stable. The IL–IA ranking flips by year, so one year is not
  evidence.
- **Caveats:** precipitation only — no irrigation, ET, or soil water; low
  rainfall does not prove drought stress. NASA POWER nodes are 0.5° × 0.625°, so
  the fields in a farm resolve to two to four cells and within-farm spread is
  not a microclimate signal.

## W3 · `fig_weather_gdd_precip_coupling.png`

- **Question:** In these seasons, did hotter growing seasons also arrive drier —
  and is that pattern the same on each farm?
- **Variables:** seasonal GDD and seasonal precipitation, averaged to grower-year;
  grower-demeaned anomalies for the pooled panel. Source units are °C·d and mm;
  `--units us` displays °F·d and inches.
- **Method:** four panels — IL, IA, NE (five labelled grower-years per farm, path
  in year order, exact two-sided Spearman ρ and p), plus pooled grower-demeaned
  anomalies with a Pearson fit and a within-grower year-shuffle permutation.
  `tables/weather_grower_year.csv`, `weather_gdd_precip_corr.csv`,
  `weather_gdd_precip_sensitivity.csv`.
- **Status:** post hoc exploratory. The question, the season index, and the test
  were chosen after the initial EDA, so the exact p-values describe a
  data-chosen hypothesis and are not confirmatory.
- **May conclude:** the sign is negative on every farm except IL; the regional
  season index is perfectly inverse (ρ = −1.0, exact p = 0.017, n = 5); the sign
  survives dropping any one season at the index level, while IL is unstable.
  Iowa and Nebraska share the direction of the association.
- **Caveats:** post hoc, exploratory, and selected after the initial EDA — not a
  pre-specified confirmatory result. Five seasons is a very short record; ρ =
  −1.0 is the smallest attainable two-sided p at n = 5, so the result is fragile
  by construction.
  Shared seasons make the pooled permutation p optimistic — it is descriptive,
  not independent replication. Three grower means are a location gradient
  (perfectly rank-confounded with geography) and are not tested. This is an
  association between two seasonal aggregates, not evidence that heat causes
  dry seasons. Farm panels use independent axis ranges: compare direction, not
  slope. The converted slope is inches per 100 °F·d under `--units us`; no yield,
  irrigation, or soil-water data.

## C1 · `fig_cdl_crop_sequence_grid.png`

- **Question:** What crop sequence has each field followed, and how consistent is
  rotation within a grower?
- **Variables:** primary `crop_name` per field-year (2019–2023); rotation table
  `rotation_sequence`, `predicted_next_crop`.
- **Method:** 30×5 tile grid grouped by grower, plus a hatched heuristic next-crop
  column.
- **May conclude:** IL and IA mostly alternate corn–soy; NE is corn-dominant with
  several continuous-corn fields.
- **Caveats:** 30 m annual classification; double/cover crops invisible; the
  primary class hides roughly 1–5 % minority pixels; the next-crop column is
  heuristic.

## C2 · `fig_cdl_area_weighted_crop_mix.png`

- **Question:** What is the area-weighted corn–soy balance of each grower, and
  how stable is it?
- **Variables:** `area_ha_est × pct / 100` per class and grower-year.
- **Method:** 100 % stacked bars, one facet per grower; corn share annotated;
  `tables/cdl_area_mix.csv`.
- **May conclude:** NE carries a much larger corn share of field area than IL or
  IA; year-to-year movement follows rotation phase.
- **Caveats:** boundary area, not planted area; edge pixels inflate "other" in
  small fields.

## C3 · `fig_cdl_corn_after_corn_by_year.png`

- **Question:** Is a grower's corn-after-corn frequency steady across the four
  year transitions, or concentrated in one or two of them? And is adjacency more
  than each field's corn frequency alone would produce?
- **Variables:** consecutive-year primary CDL crop per field, 2019–2023;
  eligibility = fields with corn in the from-year.
- **Method:** left panel = corn→corn proportion by transition year for each
  grower, Wilson 95 % CIs, n/N annotated, pooled rate in the legend; right panel
  = observed adjacency vs a within-field year-shuffle null that preserves each
  field's corn-year count. `tables/cdl_corn_after_corn_by_transition.csv`,
  `tables/cdl_corn_after_corn_persistence.csv`.
- **May conclude:** Nebraska is steady, not episodic — every transition falls
  between roughly 75 % and 89 % and contributes 22–30 % of its events. IL and IA
  each have a single field driving their corn→corn events. The null test
  separates frequency from persistence: NE adjacency is at chance given its 3–5
  corn years per field, while IL/IA alternate more than chance.
- **Caveats:** denominators vary (2–9 fields) because eligibility is conditional
  on the from-year crop; transition rows within a field are not independent.
  Wilson CIs overlap across NE transitions, so "steady" means no visible
  concentration, not a precise constant rate. Five CDL years cannot identify a
  rotation system; CDL is a 30 m primary-class product with 1–5 % minority
  pixels. Absence of excess adjacency does not weaken the field-level finding
  that 9/10 NE fields had at least one corn→corn transition.

## C4 · transition analysis (tables only)

- **Question:** Do growers differ in how often corn follows corn?
- **Variables:** consecutive-year primary crops; field-level corn→corn flag.
- **Method:** `tables/cdl_transition_matrix.csv` (counts and row probabilities),
  `tables/cdl_transition_test.csv` (Fisher exact pairwise, Wilson CIs, Cramér's
  V). Field is the inferential unit.
- **May conclude:** the share of fields with corn-after-corn separates NE from
  IL/IA by a wide margin.
- **Caveats:** n=10 fields per grower and a five-year window; p-values are
  indicative, effect sizes and CIs carry the argument.

## M1 · `fig_map_three_growers.png`

- **Question:** Where are the growers, and how are fields laid out within each
  county?
- **Variables:** all field polygons, latest-year CDL primary crop (2023 here),
  area, county/state; geoadmin state and county outlines.
- **Method:** CONUS locator plus three equal 60 × 60 km (37 × 37 mi) panels at a
  common scale, `EPSG:5070`, scale bar (10 km metric, 10 mi U.S.), north arrow.
- **May conclude:** each grower is a single-county cluster; IL/IA parcels are
  large and irregular, NE parcels are uniform blocks; growers are far apart.
- **Caveats:** FIBOA boundaries are not survey records; crop colour is the
  latest-year CDL class (2023 here), not ground truth. The 60 km / 37 mi window is the
  smallest common window that keeps every field polygon inside all three
  panels; it is not a regional extent.

---

## Shared diagnostics (tables)

| Table | Purpose |
|---|---|
| `boundary_spatial.csv` | Nearest-neighbour distances, max pairwise distance, overlap/touch counts |
| `weather_grower_year.csv` | Grower-year weather means, field SD, and anomalies used by the coupling figure |
| `weather_gdd_precip_corr.csv` | Season-index, per-grower, location-gradient, and pooled coupling statistics |
| `weather_gdd_precip_sensitivity.csv` | Leave-one-season-out Spearman ρ for the coupling analysis |
| `cdl_corn_after_corn_by_transition.csv` | Corn→corn rate per transition year with Wilson CIs |
| `cdl_corn_after_corn_persistence.csv` | Observed adjacency vs within-field year-shuffle null |
| `cdl_purity_diagnostics.csv` | Area vs primary-class share (Spearman), per grower and pooled |
| `weather_variance_components.csv` | Grower / field-within-grower / year variance shares |
| `cdl_rotation_patterns.csv` | Per-field sequence, corn/soy counts, heuristic outlook |
