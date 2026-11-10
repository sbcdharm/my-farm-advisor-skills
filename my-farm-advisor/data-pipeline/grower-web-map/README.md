# Grower Web Map

Reusable grower-level interactive HTML map built from the real field boundaries
already written by the data pipeline.

Each run produces **one lightweight HTML file per grower** that shows every
farm and field on an **ESRI World Imagery** satellite basemap, with zoom
controls, clickable polygons, and a filterable field list for navigation.

- No satellite rasters are embedded; only boundary geometry is inlined.
- Outputs are written under the runtime tree, never into the skill checkout.
- State labels come from boundary properties, not `farm.json` (see
  [GUIDE.md](GUIDE.md#farmjson-state-bug)).

## Quick start

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"

"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_web_map.py --grower-slug il-grower
```

Default output:

```text
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/derived/reports/<grower>_grower_web_map.html
```

Open the HTML file in any modern browser. The basemap and Leaflet load from
CDNs, so the viewer needs network access for imagery; all field data is
embedded and still renders without it.

## Files

- [GUIDE.md](GUIDE.md) — CLI, data contract, map behavior, and lightweight-output rules
- [AGENTS.md](AGENTS.md) — local edit scope for this subskill
- Implementation: [`../src/scripts/reporting/generate_grower_web_map.py`](../src/scripts/reporting/generate_grower_web_map.py)
