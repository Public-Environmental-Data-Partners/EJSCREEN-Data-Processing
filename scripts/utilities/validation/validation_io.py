"""validation_io.py

Replacement for the previous validation_paths.py implemented to
- Delegate root resolution to shared/resolve_path.py
- Provide minimal S3/local read/write helpers used by validation scripts

This file is intentionally placed under utilities/validation so changes
are limited to the validation slice as requested.
"""
from pathlib import Path
import json
import io
import sys
import tempfile
import importlib
from typing import Any

try:
    import boto3
    from botocore.exceptions import ClientError
except Exception:
    boto3 = None
    ClientError = Exception

import pandas as pd

# All of our project-specific imports must be relative to the
# `scripts` folder which we assume is at the first level of the
# repository.
# NB: ***If `scripts` moves, this code will have to change.***
REPO_ROOT = next((p for p in Path(__file__).resolve().parents if (p / ".git").exists()), None)
if REPO_ROOT is None:
    raise RuntimeError("Architectural Error: Repository root anchor (.git) could not be found!")
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import shared.resolve_path as resolve_path


def get_validation_root(location: str) -> str:
    loc = str(location).lower()
    if loc not in ('local', 'remote'):
        raise ValueError("location must be 'local' or 'remote'")
    return resolve_path.get_pipeline_root(loc)


def is_s3_uri(p: str) -> bool:
    return isinstance(p, str) and p.lower().startswith('s3://')


def join_root_and_relative_path(root: str, relative: str) -> str:
    if is_s3_uri(root):
        return root.rstrip('/') + '/' + str(relative).lstrip('/')
    return str(Path(root) / relative)


def exists_s3_or_local(path: str) -> bool:
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
    try:
        return Path(path).exists()
    except Exception:
        return False


def read_csv_s3_or_local(path: str, **pd_kwargs) -> pd.DataFrame:
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
            return pd.read_csv(io.BytesIO(body), **pd_kwargs)
        except ClientError as exc:
            raise FileNotFoundError(f"S3 object not found: {path}: {exc}")
        except Exception as exc:
            raise RuntimeError(f"Failed to read S3 object {path}: {exc}")
    return pd.read_csv(path, **pd_kwargs)


def ensure_local_parent_dir(path: str) -> None:
    if is_s3_uri(path):
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def load_fsspec_module():
    return importlib.import_module('fsspec')


def write_df_s3_or_local(df: pd.DataFrame, out_path: str) -> None:
    ensure_local_parent_dir(out_path)
    if is_s3_uri(out_path):
        if boto3 is None:
            raise RuntimeError("boto3 required to write S3 objects but is not installed")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.csv')
        tmp.close()
        try:
            df.to_csv(tmp.name, index=False)
            tail = out_path[5:]
            parts = tail.split('/', 1)
            if len(parts) != 2:
                raise ValueError(f"Invalid S3 URI: {out_path}")
            bucket, key = parts[0], parts[1]
            s3 = boto3.client('s3')
            s3.upload_file(Filename=tmp.name, Bucket=bucket, Key=key)
        finally:
            try:
                Path(tmp.name).unlink()
            except Exception:
                pass
        return
    df.to_csv(out_path, index=False)


def write_text_s3_or_local(out_path: str, text: str) -> None:
    ensure_local_parent_dir(out_path)
    if is_s3_uri(out_path):
        if boto3 is None:
            raise RuntimeError("boto3 required to write S3 objects but is not installed")
        tail = out_path[5:]
        parts = tail.split('/', 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid S3 URI: {out_path}")
        bucket, key = parts[0], parts[1]
        s3 = boto3.client('s3')
        s3.put_object(Bucket=bucket, Key=key, Body=text.encode('utf-8'))
        return
    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write(text)


def write_figure_s3_or_local(fig, out_path: str, dpi: int = 150, bbox_inches='tight') -> None:
    if is_s3_uri(out_path):
        if boto3 is None:
            raise RuntimeError("boto3 required to write S3 objects but is not installed")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
        tmp.close()
        try:
            fig.savefig(tmp.name, dpi=dpi, bbox_inches=bbox_inches)
            tail = out_path[5:]
            parts = tail.split('/', 1)
            if len(parts) != 2:
                raise ValueError(f"Invalid S3 URI: {out_path}")
            bucket, key = parts[0], parts[1]
            s3 = boto3.client('s3')
            s3.upload_file(Filename=tmp.name, Bucket=bucket, Key=key)
        finally:
            try:
                Path(tmp.name).unlink()
            except Exception:
                pass
        return
    ensure_local_parent_dir(out_path)
    fig.savefig(out_path, dpi=dpi, bbox_inches=bbox_inches)
