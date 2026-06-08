# Module Specification: build_manifest.py
**Purpose:** Provide a high-level orchestration planning tool shared across Python and R runtimes via reticulate. It reads either an indicator's or a shared asset's stage-specific configuration block, coordinates with `resolve_path.py`, and returns a single unified "Run Manifest" dictionary containing every file path required for that execution phase.

---

## 1. System Requirements & Context
- **Language Compatibility:** Pure Python 3.x using only standard libraries (`json`, `os`, `pathlib`). No third-party dependencies.
- **Interoperability:** Must expose a flat functional interface at the module root level to support smooth R `reticulate` execution ergonomics. Under the hood, it must import and utilize `resolve_path.py` to obtain physical storage vectors.
- **Directory Context:** Assumes execution occurs from the `project_root/` directory.
- **Module Location:** The manifest module will live at `./scripts/shared/build_manifest.py`.

---

## 2. Core Functional Requirements (The "What")

The module acts as a workflow compiler. Instead of operational scripts parsing raw JSON trees to discover their inputs, this module processes the configuration schema to produce an actionable bill of materials.

### 1. Configuration Target Structure
The module must support two target domains:

- **Indicator target:** Read `./scripts/{name}/{name}_config.json`, navigate to `versions -> {version} -> stages -> {stage}`.
- **Shared target:** Read `./scripts/shared/shared_config.json`, navigate to `assets -> {name} -> {version} -> stages -> {stage}`.

In both cases, the module must parse the targeted stage block's explicit `inputs` and `outputs` mappings. A stage may omit `inputs`; in that case, the returned manifest must contain an empty `inputs` dictionary.

### 2. Dependency Tracking Invariant
When an entry under a stage's `inputs` section points to a global shared asset (e.g., `type: "shared_asset"`), `build_manifest.py` must automatically step back up the configuration tree, query `resolve_path.py` to identify the precise version string pinned under `required_shared_assets` for that indicator version, and then pass that information along to get the asset's shared paths. The calling processing script should never have to explicitly declare or look up dependency versions.

Indicator-owned fetched assets are now modeled as outputs of the indicator's `fetch` stage. Shared downloaded assets are modeled as outputs of the shared asset's `fetch` stage. Shared preprocessed assets are modeled as outputs of the shared asset's `preprocess` stage.

This dependency-version lookup rule applies to indicator targets. Shared targets are first-class manifest targets themselves and must be compilable directly from `shared_config.json` without being routed through an indicator config.

### 3. Output Data Shape Contract
The function must compile and return a **single Python Dictionary** containing exactly two top-level keys: `"inputs"` and `"outputs"`. 

The value of every single nested asset identity key within this manifest must follow the resolver contract and include at least the keys shown below:
```python
{
    "root": "string base path",
    "relative": "string versioned relative path (or template)"
}

```

---

## 3. Required Module Interface (Exposed Function)

### `get_stage_manifest(target_type: str, name: str, stage: str, version: str, environment: str = "local") -> dict`

`target_type` must accept only `"indicator"` or `"shared"`, and invalid values must fail fast with a clear error.

`environment` must accept only `"local"` or `"remote"`, and invalid values must fail fast with a clear error.

#### Expected Behavior:

1. **Load Configuration:**
- If `target_type="indicator"`, read and parse `./scripts/{name}/{name}_config.json`.
- If `target_type="shared"`, read and parse `./scripts/shared/shared_config.json`.
2. **Isolate Stage Requirements:**
- If `target_type="indicator"`, navigate to `["versions"][version]["stages"][stage]`.
- If `target_type="shared"`, navigate to `["assets"][name][version]["stages"][stage]`.

If the target, stage, or version does not exist, raise an explicit descriptive error.
3. **Compile Inputs:** Loop through all items defined inside the stage's `inputs` block:
- For indicator targets:
    - If an input maps to an indicator fetch asset, request its coordinates from `resolve_path.get_download_path()`, which resolves through the indicator's `fetch` stage outputs.
    - If an input maps to a global shared asset, query `resolve_path.get_dependency_version()` to find the required version, then pass that version to `resolve_path.get_shared_asset_path()`.
    - If an input maps to an indicator-owned `file`, `directory`, or `template`, compile it directly against the indicator root path using its `relative_path`.
- For shared targets:
    - If the stage defines no `inputs`, return an empty `inputs` dictionary.
    - If shared-stage inputs are later defined, they must be compiled strictly from their declared entry types. The implementation must not invent fallback rules for undeclared or transitional shared input shapes.


4. **Compile Outputs:** Loop through all items defined inside the stage's `outputs` block. Match them against indicator paths or directory structures, compiling them into the identical dual-key dictionary output format.
* For indicator targets, compile outputs against the indicator root path using `relative_path`.
* For shared targets, compile outputs against the shared root path using the output entry's declared relative path field. Template tokens such as `{postal}` or `{fips}` must remain intact.
5. **Consolidate and Return:** Return the unified manifest layout.

---

## 4. Manifest Dictionary Structural Contract (Target Output Shape)

The AI code generator must guarantee that calling this module results in a dictionary nested exactly like the sample structure below:

```python
{
    "inputs": {
        "primary_asset_name": {
            "root": "./pipeline/o3/",
            "relative": "v1.0/downloads/2020/2020_ozone_daily_8hour_maximum.txt.gz"
        },
        "dependent_shared_asset_name": {
            "root": "./pipeline/shared/",
            "relative": "census_block_weights/1.0/preprocessed_input/census_block_weights_2020_{postal}.csv"
        }
    },
    "outputs": {
        "output_directory": {
            "root": "./pipeline/o3/",
            "relative": "v1.0/preprocessed_input/"
        },
        "main_tract_averages": {
            "root": "./pipeline/o3/",
            "relative": "v1.0/preprocessed_input/o3_tract_annual_average.csv"
        }
    }
}

```

Shared-target example:

```python
{
    "inputs": {},
    "outputs": {
        "tiger_bg_2020": {
            "root": "./pipeline/shared/",
            "relative": "downloads/tiger_lines/2020/bg/tl_2020_{fips}_bg.zip"
        }
    }
}

```

---

## 5. Implementation Verification / Test Cases

1. **Local Preprocess Context Resolution:**
`get_stage_manifest(target_type="indicator", name="o3", stage="preprocess", version="1.0", environment="local")`
=> Must return an input dictionary mapping both internal indicator assets and external `census_block_weights` location dictionaries with local file-system roots.
2. **Remote Score Context Resolution:**
`get_stage_manifest(target_type="indicator", name="o3", stage="score", version="1.0", environment="remote")`
=> Must return an input/output mapping dictionary prefixing all `"root"` parameters with the explicit S3 protocol block retrieved from the configuration file, keeping relative string structures preserved for pipeline orchestration.
3. **Local Shared Fetch Context Resolution:**
`get_stage_manifest(target_type="shared", name="tiger_bg", stage="fetch", version="2020", environment="local")`
=> Must return an outputs dictionary rooted at the shared local path with the unexpanded state-scoped relative template preserved.
4. **Local Shared Preprocess Context Resolution:**
`get_stage_manifest(target_type="shared", name="census_block_weights", stage="preprocess", version="1.0", environment="local")`
=> Must return the currently declared preprocess outputs for that shared asset with the shared local root and the relative template preserved.

## 6. Current Strictness Decision

`build_manifest.py` should remain strict and should not implement fallback logic for incomplete or transitional config shapes.

Shared assets are first-class manifest targets and must be supported directly by the manifest interface.

`census_block_weights` is still an evolving shared-asset configuration surface. Until its final stage layout is settled, the manifest implementation should only honor the stages and fields explicitly present in config, without adding compatibility layers or silent fallbacks.
