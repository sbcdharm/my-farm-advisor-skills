# Local Instructions

## Purpose

This folder owns the reusable cross-grower field-level EDA workflow: static
figures, comparison tables, and a reproducibility manifest built from real field
boundaries, weather, and CDL history.

## Safe edit scope

Edits should stay in this folder and its children unless the user explicitly
asks for a broader skill change. The runnable generator lives in
`../../data-pipeline/src/scripts/eda/eda_multifarm.py`; edit it only when the
task is this workflow. Do not change parent `SKILL.md`, sibling EDA workflows,
or root policy from a subskill task unless explicitly requested.

## Read nearby docs first

Read `GUIDE.md` first, then `references/figure-catalog.md` and
`references/metric-definitions.md`. If routing context is needed, read
`../INDEX.md` and `../../SKILL.md`.

## Output rules

- Generated figures, tables, and manifests belong under
  `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/<run-label>/`, never in Git.
- Do not fold report narrative into the runner or this folder. The one-time
  report consumes `manifest.json` and selected figures in a separate step.
- Every value printed in a figure annotation must also exist in a CSV table.

## Local validation

Run `./scripts/validate.sh` from the repository root after structural changes.
For runner changes, run `eda_multifarm.py --check-inputs` against the smallest
available farm set, then a full run and a visual check of the PNGs.

## Local-delta-only reminder

This nested AGENTS.md only records instructions that differ from the parent or
root files. Do not duplicate root-wide asset, vendor, or validation policy here
except this pointer to `../../../AGENTS.md`.
