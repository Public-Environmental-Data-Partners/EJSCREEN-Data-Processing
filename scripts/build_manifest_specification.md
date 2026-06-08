# Module Specification: build_manifest.py
**Purpose:** Provide a high-level orchestration planning tool shared across Python and R runtimes via reticulate. It reads an indicator's stage-specific configuration block, automatically handles its nested shared-asset dependencies, coordinates with `resolve_path.py`, and returns a single unified "Run Manifest" dictionary containing every file path required for that execution phase.

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
The module reads the indicator configuration file located at `./scripts/{indicator}/{indicator}_config.json`. It must navigate to the requested `version`, extract the targeted `stage` block, and parse its explicit `inputs` and `outputs` mappings.

### 2. Dependency Tracking Invariant
When an entry under a stage's `inputs` section points to a global shared asset (e.g., `type: "shared_asset"`), `build_manifest.py` must automatically step back up the configuration tree, query `resolve_path.py` to identify the precise version string pinned under `required_shared_assets` for that indicator version, and then pass that information along to get the asset's shared paths. The calling processing script should never have to explicitly declare or look up dependency versions.

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

### `get_stage_manifest(indicator: str, stage: str, version: str, environment: str = "local") -> dict`

`environment` must accept only `"local"` or `"remote"`, and invalid values must fail fast with a clear error.

#### Expected Behavior:

1. **Load Configuration:** Read and parse the target indicator JSON file (`./scripts/{indicator}/{indicator}_config.json`).
2. **Isolate Stage Requirements:** Navigate down to the specific `["versions"][version]["stages"][stage]` block. If the stage or version does not exist, raise an explicit descriptive error.
3. **Compile Inputs:** Loop through all items defined inside the stage's `inputs` block:
* If an input maps to a local indicator asset, request its coordinates from `resolve_path.get_download_path()`.
* If an input maps to a global shared asset, query `resolve_path.get_dependency_version()` to find the required version, then pass that version to `resolve_path.get_shared_asset_path()`.


4. **Compile Outputs:** Loop through all items defined inside the stage's `outputs` block. Match them against indicator paths or directory structures, compiling them into the identical dual-key dictionary output format.
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

---

## 5. Implementation Verification / Test Cases

1. **Local Preprocess Context Resolution:**
`get_stage_manifest(indicator="o3", stage="preprocess", version="1.0", environment="local")`
=> Must return an input dictionary mapping both internal indicator assets and external `census_block_weights` location dictionaries with local file-system roots.
2. **Remote Score Context Resolution:**
`get_stage_manifest(indicator="o3", stage="score", version="1.0", environment="remote")`
=> Must return an input/output mapping dictionary prefixing all `"root"` parameters with the explicit S3 protocol block retrieved from the configuration file, keeping relative string structures preserved for pipeline orchestration.
