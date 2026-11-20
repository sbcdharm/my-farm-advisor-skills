#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""Generate a grower-level interactive HTML web map from real field boundaries.

Reads the canonical boundary GeoJSON written by the data pipeline for every
farm under a grower, renders all fields on an ESRI World Imagery basemap with
Leaflet, and writes one lightweight HTML file per grower.

Design rules:
- Field geometry comes from `<farm>/boundary/field_boundaries.geojson`.
- Only boundary geometry is embedded; no satellite rasters are embedded.
- State labels come from boundary properties (`state`, then `state_fips`).
  ``farm.json`` state is never trusted for display or routing, because the
  pipeline's canonical-tree helper can overwrite it with a hardcoded value.
- Outputs live under the runtime tree, never in the skill checkout.

Example:
    python scripts/reporting/generate_grower_web_map.py --grower-slug il-grower
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

_LOCAL_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(_LOCAL_LIB))

from runtime_paths import resolve_runtime_paths  # noqa: E402

_RUNTIME_PATHS = resolve_runtime_paths()
_RUNTIME_BASE = _RUNTIME_PATHS.runtime_base
_GROWERS_ROOT = _RUNTIME_BASE / "growers"

DEFAULT_ZOOM = 13
FARM_COLORS = [
    "#e6194b",
    "#3cb44b",
    "#4363d8",
    "#f58231",
    "#911eb4",
    "#008080",
    "#9a6324",
    "#800000",
    "#808000",
    "#000075",
]

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>__TITLE__</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
          integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <style>
    :root {
      --sidebar-width: 330px;
      --ink: #1f2933;
      --muted: #5f6b76;
      --line: #d9dee3;
      --accent: #1f6feb;
      --panel: #ffffff;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; margin: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      color: var(--ink);
      display: flex;
      height: 100vh;
      overflow: hidden;
    }
    #sidebar {
      width: var(--sidebar-width);
      min-width: var(--sidebar-width);
      height: 100%;
      background: var(--panel);
      border-right: 1px solid var(--line);
      display: flex;
      flex-direction: column;
      z-index: 1200;
    }
    #sidebar header {
      padding: 16px 16px 10px;
      border-bottom: 1px solid var(--line);
    }
    #sidebar h1 {
      font-size: 1.05rem;
      margin: 0 0 4px;
      line-height: 1.25;
    }
    #sidebar .summary {
      margin: 0;
      color: var(--muted);
      font-size: 0.82rem;
    }
    .filter-wrap { padding: 10px 16px; border-bottom: 1px solid var(--line); }
    #filter {
      width: 100%;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      font-size: 0.85rem;
    }
    #field-list { flex: 1; overflow-y: auto; padding: 8px 10px 20px; }
    .farm-group { margin-bottom: 12px; }
    .farm-group h2 {
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
      margin: 10px 6px 6px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .swatch {
      width: 10px; height: 10px; border-radius: 2px; display: inline-block; flex: 0 0 auto;
    }
    .field-btn {
      width: 100%;
      text-align: left;
      border: 1px solid transparent;
      background: transparent;
      border-radius: 6px;
      padding: 8px 10px;
      cursor: pointer;
      font-size: 0.86rem;
      color: var(--ink);
      display: flex;
      justify-content: space-between;
      gap: 8px;
    }
    .field-btn:hover { background: #f2f5f8; }
    .field-btn.active { background: #e8f0fe; border-color: #b9d0fb; }
    .field-btn .field-name { font-weight: 600; }
    .field-btn .field-meta { color: var(--muted); font-size: 0.75rem; white-space: nowrap; }
    #sidebar footer {
      padding: 10px 16px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 0.72rem;
      line-height: 1.4;
    }
    #map { flex: 1; height: 100%; }
    #sidebar-toggle {
      display: none;
      position: absolute;
      top: 12px;
      left: 12px;
      z-index: 1300;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 1rem;
      cursor: pointer;
      box-shadow: 0 1px 4px rgba(0,0,0,0.18);
    }
    .leaflet-popup-content { margin: 12px 14px; font-size: 0.86rem; }
    .popup-title { font-size: 1rem; font-weight: 700; margin-bottom: 6px; }
    .popup-table { border-collapse: collapse; }
    .popup-table td { padding: 2px 10px 2px 0; vertical-align: top; }
    .popup-table td.k { color: var(--muted); white-space: nowrap; }
    .legend-note { font-size: 0.72rem; color: var(--muted); }
    @media (max-width: 760px) {
      #sidebar {
        position: absolute;
        top: 0; left: 0; bottom: 0;
        transform: translateX(-100%);
        transition: transform 0.2s ease;
        box-shadow: 2px 0 12px rgba(0,0,0,0.2);
        width: min(85vw, 330px);
        min-width: 0;
      }
      #sidebar.open { transform: translateX(0); }
      #sidebar-toggle { display: block; }
      #map { width: 100%; }
    }
  </style>
</head>
<body>
  <aside id="sidebar">
    <header>
      <h1>__GROWER_NAME__</h1>
      <p class="summary">__SUMMARY__</p>
    </header>
    <div class="filter-wrap">
      <input id="filter" type="search" placeholder="Filter by field, farm, crop..." aria-label="Filter fields" />
    </div>
    <div id="field-list"></div>
    <footer>
      Field geometry from canonical boundary GeoJSON. State labels come from boundary
      properties, not <code>farm.json</code>. Basemap: ESRI World Imagery.
    </footer>
  </aside>
  <button id="sidebar-toggle" aria-label="Toggle field list" title="Toggle field list">&#9776;</button>
  <main id="map" aria-label="Interactive field map"></main>

  <script>
    const MAP_DATA = __MAP_DATA__;

    const map = L.map("map", {
      zoomControl: true,
      minZoom: 3,
      worldCopyJump: true,
    });

    const imagery = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      {
        maxZoom: 19,
        attribution: "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
      }
    ).addTo(map);

    const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors",
    });

    const labels = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
      {
        maxZoom: 19,
        pane: "overlayPane",
        attribution: "Labels &copy; Esri",
      }
    );

    L.control.layers(
      { "ESRI World Imagery": imagery, "OpenStreetMap": osm },
      { "Place labels": labels },
      { collapsed: false, position: "topright" }
    ).addTo(map);

    L.control.scale({ position: "bottomright", imperial: true, metric: true }).addTo(map);

    const layerById = new Map();
    const buttonById = new Map();
    let activeId = null;

    function esc(value) {
      return String(value === undefined || value === null ? "" : value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function fmtNumber(value) {
      const n = Number(value);
      if (!Number.isFinite(n)) return "&mdash;";
      return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
    }

    function popupHtml(props) {
      const rows = [
        ["Grower", props.grower_name],
        ["Farm", props.farm_name],
        ["Field", props.field_name],
        ["Crop", props.crop],
        ["Area", props.area_acres ? fmtNumber(props.area_acres) + " acres" : "&mdash;"],
        ["Area", props.area_ha_est ? fmtNumber(props.area_ha_est) + " ha" : "&mdash;"],
        ["County", props.county],
        ["State", props.state_label],
      ];
      let body = "";
      for (const [key, value] of rows) {
        if (value === undefined || value === null || value === "") continue;
        body += `<tr><td class="k">${esc(key)}</td><td>${esc(value)}</td></tr>`;
      }
      return `<div class="popup-title">${esc(props.field_name)}</div>`
        + `<table class="popup-table">${body}</table>`;
    }

    function activate(id, { openPopup = true } = {}) {
      if (activeId !== null) {
        const prev = layerById.get(activeId);
        const prevButton = buttonById.get(activeId);
        if (prev) prev.setStyle({ weight: 2, fillOpacity: 0.18 });
        if (prevButton) prevButton.classList.remove("active");
      }
      activeId = id;
      const layer = layerById.get(id);
      const button = buttonById.get(id);
      if (button) button.classList.add("active");
      if (!layer) return;
      layer.setStyle({ weight: 4, fillOpacity: 0.35 });
      map.flyToBounds(layer.getBounds(), { padding: [40, 40], maxZoom: 17, duration: 0.6 });
      if (openPopup) layer.openPopup();
    }

    const fieldsLayer = L.geoJSON(MAP_DATA, {
      style: (feature) => ({
        color: feature.properties.farm_color,
        weight: 2,
        opacity: 0.95,
        fillColor: feature.properties.farm_color,
        fillOpacity: 0.18,
      }),
      onEachFeature: (feature, layer) => {
        const props = feature.properties;
        layerById.set(props.field_id, layer);
        layer.bindPopup(popupHtml(props));
        layer.bindTooltip(esc(props.field_name), { sticky: true });
        layer.on("click", () => activate(props.field_id, { openPopup: false }));
        layer.on("mouseover", () => {
          if (activeId !== props.field_id) layer.setStyle({ weight: 3, fillOpacity: 0.3 });
        });
        layer.on("mouseout", () => {
          if (activeId !== props.field_id) layer.setStyle({ weight: 2, fillOpacity: 0.18 });
        });
      },
    }).addTo(map);

    if (fieldsLayer.getLayers().length > 0) {
      map.fitBounds(fieldsLayer.getBounds(), { padding: [30, 30] });
    } else {
      map.setView([40, -95], 4);
    }

    const listRoot = document.getElementById("field-list");
    const groups = new Map();
    for (const feature of MAP_DATA.features) {
      const props = feature.properties;
      if (!groups.has(props.farm_slug)) {
        groups.set(props.farm_slug, {
          farm_name: props.farm_name,
          farm_color: props.farm_color,
          fields: [],
        });
      }
      groups.get(props.farm_slug).fields.push(props);
    }

    for (const [farmSlug, group] of groups) {
      const section = document.createElement("section");
      section.className = "farm-group";
      section.dataset.farmSlug = farmSlug;
      const heading = document.createElement("h2");
      heading.innerHTML = `<span class="swatch" style="background:${esc(group.farm_color)}"></span>${esc(group.farm_name)}`;
      section.appendChild(heading);

      for (const props of group.fields) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "field-btn";
        button.dataset.fieldId = props.field_id;
        button.dataset.search = [props.field_name, props.farm_name, props.crop, props.county]
          .join(" ")
          .toLowerCase();
        button.innerHTML = `<span class="field-name">${esc(props.field_name)}</span>`
          + `<span class="field-meta">${esc(props.crop || "")}${props.area_acres ? " · " + fmtNumber(props.area_acres) + " ac" : ""}</span>`;
        button.addEventListener("click", () => activate(props.field_id));
        section.appendChild(button);
        buttonById.set(props.field_id, button);
      }
      listRoot.appendChild(section);
    }

    const filterInput = document.getElementById("filter");
    filterInput.addEventListener("input", () => {
      const needle = filterInput.value.trim().toLowerCase();
      for (const button of buttonById.values()) {
        button.style.display = !needle || button.dataset.search.includes(needle) ? "" : "none";
      }
      for (const section of listRoot.querySelectorAll(".farm-group")) {
        const visible = Array.from(section.querySelectorAll(".field-btn"))
          .some((button) => button.style.display !== "none");
        section.style.display = visible ? "" : "none";
      }
    });

    const toggle = document.getElementById("sidebar-toggle");
    const sidebar = document.getElementById("sidebar");
    toggle.addEventListener("click", () => sidebar.classList.toggle("open"));
  </script>
</body>
</html>
"""


def _prettify_slug(slug: str) -> str:
    words = [part for part in slug.replace("_", "-").split("-") if part]
    if not words:
        return slug
    return " ".join(word.upper() if word.isalpha() and len(word) <= 3 else word.capitalize() for word in words)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _validate_slug(value: str, label: str) -> str:
    candidate = value.strip()
    if not candidate or candidate in {".", ".."} or "/" in candidate or "\\" in candidate:
        raise SystemExit(f"error: invalid {label}: {value!r}")
    return candidate


def _iter_coords(geometry: dict[str, Any]) -> Any:
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    if kind == "GeometryCollection":
        for item in geometry.get("geometries") or []:
            yield from _iter_coords(item)
        return
    def walk(node: Any) -> Any:
        if isinstance(node, (list, tuple)):
            if len(node) >= 2 and all(isinstance(v, (int, float)) for v in node[:2]):
                yield float(node[0]), float(node[1])
            else:
                for child in node:
                    yield from walk(child)
    yield from walk(coords)


def _geometry_bounds(geometry: dict[str, Any]) -> list[list[float]] | None:
    points = list(_iter_coords(geometry))
    if not points:
        return None
    lons = [lon for lon, _ in points]
    lats = [lat for _, lat in points]
    if min(lats) < -90 or max(lats) > 90 or min(lons) < -180 or max(lons) > 180:
        raise SystemExit(
            "error: boundary coordinates are outside lon/lat range; "
            "reproject the boundary GeoJSON to EPSG:4326 before mapping"
        )
    return [[min(lats), min(lons)], [max(lats), max(lons)]]


def _resolve_growers(grower_slug: str | None, all_growers: bool) -> list[Path]:
    if all_growers:
        if not _GROWERS_ROOT.is_dir():
            raise SystemExit(f"error: growers root not found: {_GROWERS_ROOT}")
        growers = [p for p in sorted(_GROWERS_ROOT.iterdir()) if p.is_dir()]
        if not growers:
            raise SystemExit(f"error: no growers found under {_GROWERS_ROOT}")
        return growers
    if not grower_slug:
        raise SystemExit("error: --grower-slug is required unless --all-growers is used")
    slug = _validate_slug(grower_slug, "grower slug")
    grower_dir = _GROWERS_ROOT / slug
    if not grower_dir.is_dir():
        raise SystemExit(f"error: grower not found: {grower_dir}")
    return [grower_dir]


def _resolve_farms(grower_dir: Path, farm_slug: str | None) -> list[Path]:
    farms_root = grower_dir / "farms"
    if not farms_root.is_dir():
        return []
    farms = [p for p in sorted(farms_root.iterdir()) if p.is_dir()]
    if farm_slug:
        slug = _validate_slug(farm_slug, "farm slug")
        farms = [p for p in farms if p.name == slug]
    return farms


def _grower_name(grower_dir: Path, override: str | None) -> str:
    if override:
        return override
    meta = _read_json(grower_dir / "grower.json")
    display = str(meta.get("display_name") or "").strip()
    if display and display != grower_dir.name:
        return display
    return _prettify_slug(grower_dir.name)


def _farm_name(farm_dir: Path) -> str:
    meta = _read_json(farm_dir / "farm.json")
    display = str(meta.get("display_name") or "").strip()
    return display or _prettify_slug(farm_dir.name)


def _boundary_state(props: dict[str, Any]) -> str:
    state = str(props.get("state") or "").strip()
    if state:
        return state
    state_fips = str(props.get("state_fips") or "").strip()
    return state_fips or ""


def _maybe_repair_farm_state(farm_dir: Path, boundary_states: list[str]) -> str | None:
    unique = sorted({state for state in boundary_states if state})
    if len(unique) != 1:
        return f"skipped farm.json repair for {farm_dir.name}: ambiguous boundary states {unique!r}"
    canonical = unique[0]
    farm_json = farm_dir / "farm.json"
    if not farm_json.exists():
        return f"skipped farm.json repair for {farm_dir.name}: farm.json missing"
    meta = _read_json(farm_json)
    previous = str(meta.get("state") or "")
    if previous == canonical:
        return None
    meta["state"] = canonical
    farm_json.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return f"repaired farm.json state for {farm_dir.name}: {previous!r} -> {canonical!r}"


def _build_grower_map(
    grower_dir: Path,
    *,
    farm_slug: str | None,
    grower_name_override: str | None,
    repair_farm_state: bool,
) -> tuple[dict[str, Any], list[str]]:
    grower_slug = grower_dir.name
    grower_name = _grower_name(grower_dir, grower_name_override)
    farms = _resolve_farms(grower_dir, farm_slug)
    if not farms:
        raise SystemExit(f"error: no farms found for grower {grower_slug}")

    warnings: list[str] = []
    features: list[dict[str, Any]] = []
    farm_summaries: list[dict[str, Any]] = []
    states: set[str] = set()

    for index, farm_dir in enumerate(farms):
        boundary_path = farm_dir / "boundary" / "field_boundaries.geojson"
        if not boundary_path.exists():
            warnings.append(f"skipped {farm_dir.name}: missing {boundary_path}")
            continue
        try:
            boundary = json.loads(boundary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"skipped {farm_dir.name}: cannot read boundary GeoJSON ({exc})")
            continue
        raw_features = boundary.get("features") or []
        if not raw_features:
            warnings.append(f"skipped {farm_dir.name}: boundary has no features")
            continue

        farm_name = _farm_name(farm_dir)
        color = FARM_COLORS[index % len(FARM_COLORS)]
        farm_states: list[str] = []
        kept = 0

        for feature in raw_features:
            geometry = feature.get("geometry") or {}
            props = dict(feature.get("properties") or {})
            bounds = _geometry_bounds(geometry)
            if bounds is None:
                warnings.append(f"{farm_dir.name}: skipped a field with empty geometry")
                continue
            field_name = str(props.get("field_id") or props.get("field_slug") or props.get("name") or "").strip()
            if not field_name:
                field_name = f"field-{kept + 1}"
            state_label = _boundary_state(props)
            if state_label:
                farm_states.append(state_label)
                states.add(state_label)
            props.update(
                {
                    "grower_slug": grower_slug,
                    "grower_name": grower_name,
                    "farm_slug": farm_dir.name,
                    "farm_name": farm_name,
                    "farm_color": color,
                    "field_name": field_name,
                    "state_label": state_label or "unknown",
                }
            )
            features.append(
                {
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": props,
                }
            )
            kept += 1

        if kept == 0:
            warnings.append(f"skipped {farm_dir.name}: no usable field geometry")
            continue

        if repair_farm_state:
            message = _maybe_repair_farm_state(farm_dir, farm_states)
            if message:
                warnings.append(message)

        farm_summaries.append(
            {
                "farm_slug": farm_dir.name,
                "farm_name": farm_name,
                "field_count": kept,
                "color": color,
                "states": sorted(set(farm_states)),
            }
        )

    if not features:
        raise SystemExit(f"error: grower {grower_slug} has no usable field boundaries")

    map_data = {"type": "FeatureCollection", "features": features}
    meta = {
        "grower_slug": grower_slug,
        "grower_name": grower_name,
        "farm_count": len(farm_summaries),
        "field_count": len(features),
        "states": sorted(states),
        "farms": farm_summaries,
    }
    return {"map_data": map_data, "meta": meta}, warnings


def _render_html(meta: dict[str, Any], map_data: dict[str, Any]) -> str:
    states = ", ".join(meta["states"]) if meta["states"] else "state unknown"
    summary = (
        f"{meta['farm_count']} farm{'s' if meta['farm_count'] != 1 else ''} · "
        f"{meta['field_count']} field{'s' if meta['field_count'] != 1 else ''} · {states}"
    )
    payload = json.dumps(map_data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    return (
        _HTML_TEMPLATE.replace("__TITLE__", html.escape(f"{meta['grower_name']} — Grower Web Map"))
        .replace("__GROWER_NAME__", html.escape(meta["grower_name"]))
        .replace("__SUMMARY__", html.escape(summary))
        .replace("__MAP_DATA__", payload)
    )


def _default_output(grower_dir: Path) -> Path:
    return grower_dir / "derived" / "reports" / f"{grower_dir.name}_grower_web_map.html"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a grower-level interactive HTML web map from canonical field boundaries."
    )
    parser.add_argument("--grower-slug", help="Grower slug under the runtime growers root")
    parser.add_argument("--farm-slug", help="Optional farm slug to restrict the map to one farm")
    parser.add_argument("--all-growers", action="store_true", help="Generate one map per grower")
    parser.add_argument("--grower-name", help="Override the grower display name")
    parser.add_argument("--output", help="Output HTML path (single-grower runs only)")
    parser.add_argument(
        "--repair-farm-state",
        action="store_true",
        help="Rewrite farm.json state from boundary properties when they disagree (off by default)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    growers = _resolve_growers(args.grower_slug, args.all_growers)
    if args.output and len(growers) > 1:
        raise SystemExit("error: --output is only valid for a single grower")

    exit_code = 0
    for grower_dir in growers:
        map_bundle, warnings = _build_grower_map(
            grower_dir,
            farm_slug=args.farm_slug,
            grower_name_override=args.grower_name,
            repair_farm_state=args.repair_farm_state,
        )
        meta = map_bundle["meta"]
        html_text = _render_html(meta, map_bundle["map_data"])
        output = Path(args.output).expanduser() if args.output else _default_output(grower_dir)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(html_text, encoding="utf-8")

        for message in warnings:
            print(f"warning: {message}", file=sys.stderr)
        size_kb = output.stat().st_size / 1024.0
        print(
            f"✓ {meta['grower_name']}: {meta['field_count']} fields across "
            f"{meta['farm_count']} farm(s) -> {output} ({size_kb:.1f} KB)"
        )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
