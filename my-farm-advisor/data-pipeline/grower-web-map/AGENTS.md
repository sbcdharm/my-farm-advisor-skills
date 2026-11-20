# Local Instructions

## Purpose

This folder owns the grower-level interactive web-map subskill for the
data-pipeline workflow. It documents how to turn canonical field boundaries
into one lightweight interactive HTML file per grower.

## Safe edit scope

Edits should stay in this folder and the reporting implementation at
`../src/scripts/reporting/generate_grower_web_map.py`. Do not change parent
`SKILL.md`, sibling workflows, or root policy from this subskill task unless
explicitly requested.

## Read nearby docs first

Read `README.md` and `GUIDE.md` first. For runtime behavior, read
`../AGENTS.md` and `../README.md`. For the parent skill map, read
`../../SKILL.md`.

## Local rules

- Read boundaries from the runtime, never from the checkout.
- Treat boundary `state` / `state_fips` as the state source of truth; do not
  trust `farm.json.state`.
- Embed boundary geometry only. Never embed satellite rasters, posters, or other
  large assets into the HTML.
- Keep `--repair-farm-state` opt-in and disabled by default.

## Local validation

Run `./scripts/validate.sh` from the repository root after structural changes.
Then generate and inspect a map for at least one runtime grower:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_web_map.py --all-growers
```

## Local-delta-only reminder

This nested AGENTS.md only records instructions that differ from the parent or
root files. Do not duplicate root-wide asset, vendor, or validation policy here
except this pointer to `../../../AGENTS.md`.
