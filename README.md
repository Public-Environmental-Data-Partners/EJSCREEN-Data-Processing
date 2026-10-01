# EJSCREEN-Data-Processing

This repo is intended to hold the code required to build and update
all [EJAM](https://ejam.publicenvirodata.org/) |
[EJScreen](https://ejscreen.ejanalysis.com/) indicators 
and indexes as new data becomes available. 

The goal is to re-engineer the previous generation of EPA-produced
tools to get comparable, but not necessarily exactly the same, results
given the same input. And then be able to process updated inputs
as they become available.
However, this tool stack and our processing steps will diverge substantially 
from the EPA's ArcGIS-, AWS-, and Hadoop-dependent pipeline. One of the primary reference
documents we have used to understand the older system is:
https://www.epa.gov/system/files/documents/2024-07/ejscreen-tech-doc-version-2-3.pdf

# How to start contributing to this repo
* For the time being, this code is being built by a small team from 
  the Public Environmental Data Partners organization. If you'd like
  to be actively involved, we'd appreciate you joining us there
  so you have access to our working docs and communication channels.
  https://screening-tools.com/get-involved
* Later, this project should to evolve into a more standard
  open source effort where this GitHub repo will be the center of
  communication.

## Developer setup

Much of our code right now is in Python scripts. As we move forward, we
expect to add a substantial amount of R code to support the GIS 
processing requirements for many of our indicators.

### Python setup

The processing scripts require Python 3.12 or newer and use `uv` to manage the
project environment and dependencies.

1. Install `uv` if it is not already available: https://docs.astral.sh/uv/getting-started/installation/
2. From the repository root, create and sync the environment:

  ```sh
  uv sync
  ```

3. Activate the environment when running scripts directly:

  - macOS/Linux or Windows WSL: `source .venv/bin/activate`
  - Windows PowerShell: `.venv\Scripts\Activate.ps1`
  - Windows Command Prompt: `.venv\Scripts\activate.bat`

  Alternatively, run a script without activating the environment with
  `uv run python <path-to-script>`.

Indicator scripts are documented as being run from the `scripts/` directory.
See [`scripts/readme.md`](scripts/readme.md) for additional usage details.

### R Setup

TBD

---
**Coming attractions:**
* Documentation and "How to use" instructions
* Issue labels including some issues labelled [good-first-issue] (https://github.com/issues?q=is%3Aopen+is%3Aissue+label%3Agood-first-issue+user%3APublic-Environmental-Data-Partners) 
---

## License & Copyright

This repository is likely to include some sample code and data produced by the EPA.
That material is and will remain public domain. 
The bulk of the content will be new-build software for which the 
GNU Affero GPL license referenced below will apply.
We will make best efforts to make it clear which material is public domain.
All other contents should be assumed to be covered by the copyright/license below.

Copyright (C) <year> Public Environmental Data Partners (PEDP)
This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software Foundation, version 3.0.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

See the [`LICENSE`](/LICENSE) file for details.