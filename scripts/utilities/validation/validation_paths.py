"""validation_paths.py

Small helpers to resolve the validation root (local vs remote) and build
root-relative paths for the validation/migration slices.

This is intentionally minimal for Slice 1: it provides
- `get_validation_root(location)`
- `is_s3_uri(path)`
- `join_root_and_relative_path(root, relative)`
"""
from pathlib import Path
import json
import os
import io
import pandas as pd

try:
    import boto3
    from botocore.exceptions import ClientError
except Exception:
    boto3 = None
    ClientError = Exception

# Determine repository root (look for .git like resolve_path.py does)
REPO_ROOT = next((p for p in Path(__file__).resolve().parents if (p / ".git").exists()), None)
if REPO_ROOT is None:
    raise RuntimeError("Repository root anchor (.git) could not be found for validation_paths")


def _load_config():
    here = Path(__file__).parent
    cfg_path = here / 'validation_config.json'
    if not cfg_path.exists():
        raise FileNotFoundError(f"validation_config.json not found at {cfg_path}")
    with open(cfg_path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def get_validation_root(location: str) -> str:
    """Return the configured validation root for `location` ('local'|'remote')."""
    cfg = _load_config()
    loc = str(location).lower()
    if loc == 'local':
        # Resolve local root relative to repository root to return an absolute path
        local_root = cfg.get('local_root_path')
        if not isinstance(local_root, str) or not local_root:
            raise ValueError("validation_config.json missing 'local_root_path' entry")
        return str((REPO_ROOT / local_root).resolve())
    if loc == 'remote':
        return cfg.get('remote_root_path')
    raise ValueError("location must be 'local' or 'remote'")


def is_s3_uri(p: str) -> bool:
    return isinstance(p, str) and p.lower().startswith('s3://')


def join_root_and_relative_path(root: str, relative: str) -> str:
    """Join a root (local or s3) with a relative path.

    For S3 roots, joins with '/' and preserves the s3:// scheme.
    For local roots, returns a string path using pathlib.Path.
    """
    if is_s3_uri(root):
        return root.rstrip('/') + '/' + str(relative).lstrip('/')
    return str(Path(root) / relative)


def exists_s3_or_local(path: str) -> bool:
    """Return True if the given path exists locally or in S3."""
    if is_s3_uri(path):
        if boto3 is None:
            raise RuntimeError("boto3 required to check S3 URIs but is not installed")
        tail = path[5:]
        parts = tail.split('/', 1)
        if len(parts) != 2:
            return False
        bucket, key = parts[0], parts[1]
        s3 = boto3.client('s3')
        try:
            s3.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError:
            return False
        except Exception:
            return False
    # local path
    try:
        return Path(path).exists()
    except Exception:
        return False


def read_csv_s3_or_local(path: str, **pd_kwargs) -> pd.DataFrame:
    """Read CSV from local filesystem or S3 and return a pandas DataFrame.

    Uses `boto3` for S3 reads; raises informative errors when boto3 is missing
    or the S3 object cannot be found.
    """
    if is_s3_uri(path):
        if boto3 is None:
            raise RuntimeError("boto3 required to read S3 URIs but is not installed")
        tail = path[5:]
        parts = tail.split('/', 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid S3 URI: {path}")
        bucket, key = parts[0], parts[1]
        s3 = boto3.client('s3')
        try:
            resp = s3.get_object(Bucket=bucket, Key=key)
            body = resp['Body'].read()
            # let pandas infer text/binary; use BytesIO
            return pd.read_csv(io.BytesIO(body), **pd_kwargs)
        except ClientError as exc:
            raise FileNotFoundError(f"S3 object not found: {path}: {exc}")
        except Exception as exc:
            raise RuntimeError(f"Failed to read S3 object {path}: {exc}")

    # local file: delegate to pandas
    return pd.read_csv(path, **pd_kwargs)
