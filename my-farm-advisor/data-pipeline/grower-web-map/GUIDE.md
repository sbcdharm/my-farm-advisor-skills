---
name: grower-web-map
description: Generate a grower-level interactive HTML web map from canonical field boundary GeoJSON, with ESRI World Imagery, zoom controls, clickable field popups, and a field-list navigator.
version: 1.0.0
author: Boreal Bytes
tags: [web-map, grower, leaflet, esri-world-imagery, field-boundaries, interactive]
---

# Workflow: grower-web-map

## What it produces

One self-contained HTML file per grower:

```text
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/derived/reports/<grower>_grower_web_map.html
```

The file embeds **only** the canonical boundary GeoJSON. It does not embed
satellite rasters, field posters, or other large assets, so it stays small
(typically tens of KB).

## Inputs

For every farm under `growers/<grower>/farms/*/`:

| Input | Use |
|---|---|
| `boundary/field_boundaries.geojson` | Field polygons and attributes (required) |
| `farm.json` | Farm display name (state is ignored for display) |
| `growers/<grower>/grower.json` | Grower display name |

The boundary file must be WGS84/EPSG:4326 (GeoJSON default). The script rejects
coordinates outside lon/lat range instead of silently drawing a wrong map.

## CLI

```bash
python scripts/reporting/generate_grower_web_map.py \
  --grower-slug il-grower \
  [--farm-slug illinois-demo-farm] \
  [--grower-name "Illinois Grower"] \
  [--output /custom/path/map.html] \
  [--repair-farm-state] \
  [--all-growers]
```

| Flag | Behavior |
|---|---|
| `--grower-slug` | Grower under the runtime growers root (required unless `--all-growers`) |
| `--farm-slug` | Restrict the map to a single farm |
| `--all-growers` | Generate one map per grower; incompatible with `--output` |
| `--grower-name` | Override the display name shown in the sidebar |
| `--output` | Custom HTML path (single-grower runs only) |
| `--repair-farm-state` | Optional, **off by default**; rewrites `farm.json` state from boundary properties when they disagree |

Exit status is non-zero when the grower or its boundaries are missing.

## Map behavior

- **Basemap:** ESRI World Imagery
  `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`
- **Overlays:** optional ESRI place labels; OpenStreetMap is available as an
  alternate base layer in the layer control.
- **Controls:** Leaflet zoom control, scale bar, fit-to-grower on load.
- **Polygons:** clickable; popups show grower, farm, field, crop, area, county,
  and state. Hover highlights the field.
- **Field list:** grouped by farm with a text filter; clicking an entry flies
  to the field and opens its popup.
- **Colors:** each farm gets a distinct outline/fill color.

## Farm.json state bug

The pipeline helper `ensure_canonical_data_tree` can overwrite
`farm.json.state` with a hardcoded value (for example, an Illinois farm showing
`"IA"`). This subskill therefore:

1. derives state labels from boundary `state` (falling back to `state_fips`),
2. never uses `farm.json.state` for map display, and
3. only rewrites `farm.json.state` when `--repair-farm-state` is passed and the
   boundary states agree on a single value.

## Lightweight-output rules

- Inline boundary geometry only; never base64-embed imagery or reports.
- Keep Leaflet JS/CSS on CDN rather than vendoring large bundles.
- Target well under 150 KB per grower map; flag any file that exceeds it during
  verification.

## Related workflows

- Visual style and broader dashboard patterns: [../../admin/interactive-web-map/GUIDE.md](../../admin/interactive-web-map/GUIDE.md)
- Runtime contract for the parent skill: [../README.md](../README.md)
