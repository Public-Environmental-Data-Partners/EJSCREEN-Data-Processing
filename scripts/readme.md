Before running any scripts for the first time, set up and activate the project virtual environment:

1. Create and sync the environment:
   `uv sync`
   This automatically creates the .venv folder and installs all dependencies listed in `pyproject.toml` file. 

2. Activate the virtual environment:

    - macOS/Linux: `source .venv/bin/activate`
    - Windows (PowerShell): `.venv\Scripts\Activate.ps1`
    - Windows (CMD): `.venv\Scripts\activate.bat`

3. Once activated, you can run any script directly using standard python:
  `python scripts/tool1/tool1.py`
   (Alternatively, you can skip activation entirely and run any script using `uv run <path-to-script>`).