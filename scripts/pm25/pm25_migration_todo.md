# PM2.5 Manifest Migration — Walkthrough TODO

Created: 2026-06-10
Purpose: Use the `o3` manifest-driven changes as a template to convert `pm25` to manifest/stage-based processing. 
Composed and written in the voice of GPT-5 chatbot. Some editing done by AG.

---

## Checklist

- [ ] Diff `o3` between `indicators` and `dataVersioning` branches and develop a change template/checklist
  - Status: not-started
  - Goal: capture the exact changes made to `scripts/o3` (dataVersioning vs indicators) so we can port the pattern.
  - Command (local):

    ```bash
    git diff --no-color indicators..dataVersioning -- scripts/o3
    ```

  - Output: short patch/notes to use as a coding template.

- [ ] Inspect `scripts/pm25/pm25_config.json` and map further required changes
  - Status: partially implemented
  - Done: config matches the o3 architecture as of o3 initial hand-off (6/13). May need to be updated if changes to o3 config are made during testing.

- [ ] Refactor `scripts/pm25/pm25_preprocess.py` to manifest-driven CLI
  - Status: not-started
  - Implementation notes:
    - Replace direct `pm25_config` usage with `build_manifest.get_stage_manifest(target_type='indicator', name='pm25', stage='preprocess', version=..., environment=...)`.
    - Use `resolve_path.get_indicator_root('pm25', version, storage_mode)` for roots.
    - CLI: positional `storage_mode` (`local|remote`), `-v/--version` (default `1.0`), long-only `--dry-run`.
    - Early exit on dry-run after validating manifest + headers.
  - Files to edit: `scripts/pm25/pm25_preprocess.py`.

- [ ] Refactor `scripts/pm25/pm25_indicator.py` to manifest-driven CLI
  - Status: not-started
  - Implementation notes:
    - Add `-l/--location` required, `--state` required (accept `all` mapped to `None`), `-v/--version` default `1.0`, `--dry-run` long-only.
    - Load the `score` stage manifest once in `main()` and pass `manifest` into `resolve_paths()`/`process_state()` to avoid duplicate lookups.
    - Use `resolve_path.get_indicator_root('pm25', version, storage_mode)` and `resolve_path.get_dependency_version('pm25', version, 'census_block_weights')` + `resolve_path.get_shared_root(...)` to resolve shared inputs.
    - Remove dependency on `scripts/pm25/pm25_config.py`; ensure `scripts/shared` importability via `sys.path` insertion as in `o3`.
  - Files to edit: `scripts/pm25/pm25_indicator.py` (plus small helper imports).

- [ ] Delete legacy `scripts/pm25/pm25_config.py` (once all references removed)
  - Status: not-started
  - Steps:
    - Search for `pm25_config` imports across repo.
    - Delete file and run syntax checks.

- [ ] Run local tests and dry-run smoke tests
  - Status: not-started
  - Commands:

    ```bash
    python -m py_compile scripts/pm25/pm25_preprocess.py scripts/pm25/pm25_indicator.py
    python scripts/pm25/pm25_preprocess.py local --dry-run
    python scripts/pm25/pm25_indicator.py --location local --state WY --dry-run
    ```

  - Check logs in `scripts/pm25/*.log` for resolved paths and dry-run messages.

- [ ] Branching / PR
  - Status: not-started
  - Suggested branch: `feature/pm25-manifest-migration`
  - Workflow: commit each logical change (config → preprocess → indicator → cleanup), run tests, push, open PR for review.

---

## How we'll walk through this file together

- I will update the checkboxes and add brief commit notes after each change.
- Please mark a step "go" by replying with the step name (e.g., `diff`, `preprocess`, `indicator`) and I will implement that step and run the local tests.
- If you prefer to implement interactively together, tell me which step to start and I will proceed and push small, testable commits.

---

If you want this file moved to a different path (e.g., `docs/`), tell me and I will relocate it. If you'd like me to start with `diff` now, say `go: diff` and I will run the local diff and produce the template notes/patch.

Last updated: 2026-06-13

Notes:
- Consolidated with the repo-root TODO; this file is now the single authoritative PM2.5 migration TODO.
