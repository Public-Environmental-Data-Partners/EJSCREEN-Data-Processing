"""Simple test for `get_state_config_list()` counts.

This test loads the raw `state_config.json` and computes expected counts for
the following extents: CONUS, US-51, US-52, TERRITORIES, ALL. It then calls
`get_state_config_list()` (case-insensitive) and asserts the returned list
length matches the computed expectation. The test is intentionally small and
does not require external services.
"""
from pathlib import Path
import sys
import json

# Ensure the scripts directory is importable like the runtime.
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

from shared import state_config as sc

# AG-supplied expected counts
EXPECTED_COUNTS: dict[str, int | None] = {
    'CONUS': 49,
    'US-51': 51,
    'US-52': 52,
    'TERRITORIES': 5,
    'ALL': 56
}


def test_get_state_config_list_counts():
    cfg_path = SCRIPTS_DIR / 'shared' / 'state_config.json'
    for extent in ('CONUS', 'US-51', 'US-52', 'TERRITORIES', 'ALL'):
        result = sc.get_state_config_list(extent)
        expected = EXPECTED_COUNTS.get(extent)
        print(f'{extent}: expected={expected}, got={len(result)}')
        if expected is not None:
            assert len(result) == expected
        assert all(isinstance(s, sc.StateConfig) for s in result)


if __name__ == '__main__':
    test_get_state_config_list_counts()
    print('OK')
