#!/usr/bin/env python3
"""
demoS3readingOptions.py

Purpose:
  Small demonstrator script that validates AWS credentials and
    - copies a GeoJSON file from S3 to a local path.
    - reads the entire object into memory and prints the first/last lines
    - streams the object line-by-line (memory-efficient) and prints first/last lines

Usage:
  Run the script directly. It expects AWS credentials available via environment
  variables or a .env file in the working directory (the script calls load_dotenv()).

Dependencies:
  - boto3 (for S3/STS access)
  - botocore
  - python-dotenv (optional, for loading .env)
  - S3 authentication values available via environment variables or a .env file
    AWS_ACCESS_KEY_ID=<your id>
    AWS_SECRET_ACCESS_KEY=<your secret>

Author / Credit:
  Code written by GitHub Copilot (coding assistance) under the direction of Anne Gunn.

Notes:
  This file is intentionally minimal and educational. The S3 URI and local
  destination are hard-coded near the top of the file for convenience; edit
  them as needed.
"""

from pathlib import Path
import sys
from collections import deque
from dotenv import load_dotenv

# load environment from .env if present
load_dotenv()

# --- configuration (hard-coded for simplicity) -----------------------------
# S3 URI to download and local destination path. Edit these values as needed.
S3_URI = 's3://pedp-data-preserved/ejscreen-data-processing/traffic/ri_bg_summary.geojson'
DEST_PATH = './test_files/ri_bg_summary.geojson'


try:
    import boto3
    from botocore import exceptions as botocore_exceptions
except Exception as e:
    print("boto3 and botocore are required. Install with: pip install boto3", file=sys.stderr)
    raise


def validate_aws_credentials():
    """Call STS GetCallerIdentity to verify credentials and print the identity."""
    try:
        sts = boto3.client('sts')
        resp = sts.get_caller_identity()
        arn = resp.get('Arn')
        account = resp.get('Account')
        user_id = resp.get('UserId')
        print(f"AWS credentials valid. Arn={arn}, Account={account}, UserId={user_id}")
        return True
    except botocore_exceptions.NoCredentialsError:
        print("No AWS credentials found. Please set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (or use a role/profile).", file=sys.stderr)
        return False
    except botocore_exceptions.ClientError as e:
        print(f"AWS client error validating credentials: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Unexpected error validating AWS credentials: {e}", file=sys.stderr)
        return False


def download_s3_uri(s3_uri: str, dest_path: str):
    """Download s3://bucket/key to dest_path using boto3.

    Raises RuntimeError on failure.
    """
    if not s3_uri.lower().startswith('s3://'):
        raise ValueError("s3_uri must start with s3://")
    tail = s3_uri[5:]
    parts = tail.split('/', 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    bucket, key = parts[0], parts[1]

    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # If destination exists, remove the old file before downloading
    if dest.exists():
        if dest.is_file():
            try:
                dest.unlink()
                print(f"Deleted existing file {dest}")
            except Exception as e:
                raise RuntimeError(f"Failed to delete existing file {dest}: {e}") from e
        else:
            # It's a directory or something unexpected
            raise RuntimeError(f"Destination {dest} exists and is not a file")

    s3 = boto3.client('s3')
    try:
        print(f"Starting download s3://{bucket}/{key} -> {dest}")
        s3.download_file(Bucket=bucket, Key=key, Filename=str(dest))
        print(f"Downloaded to {dest}")
    except botocore_exceptions.ClientError as e:
        raise RuntimeError(f"Failed to download s3://{bucket}/{key}: {e}") from e


def read_s3_into_memory_and_print_lines(s3_uri: str, head: int = 1, tail: int = 1) -> None:
    """Read the S3 object fully into memory (bytes -> text) and print the first
    `head` lines and the last `tail` lines. This demonstrates reading directly
    from S3 without using the local downloaded copy.

    Note: this reads the entire object into memory; use only for reasonably-sized files.
    """
    if not s3_uri.lower().startswith('s3://'):
        raise ValueError("s3_uri must start with s3://")
    tailpath = s3_uri[5:]
    parts = tailpath.split('/', 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    bucket, key = parts[0], parts[1]

    s3 = boto3.client('s3')
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        body = resp['Body'].read()
        text = body.decode('utf-8', errors='replace')
        lines = text.splitlines()
        n = len(lines)
        print(f"S3 object s3://{bucket}/{key} read into memory: {n} line(s)")
        if n == 0:
            print("(object is empty)")
            return
        # print head
        for i in range(min(head, n)):
            print(f"HEAD[{i+1}]: {lines[i]}")
        # print tail without duplicating when file small
        start_tail = max(head, n - tail)
        for i in range(start_tail, n):
            print(f"TAIL[{i - (n - tail) + 1}]: {lines[i]}")
    except botocore_exceptions.ClientError as e:
        print(f"Failed to read S3 object s3://{bucket}/{key} for preview: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Unexpected error reading S3 object s3://{bucket}/{key}: {e}", file=sys.stderr)


def read_s3_streaming(s3_uri: str, head: int = 1, tail: int = 1) -> None:
    """Stream an S3 object line-by-line and print the first `head` and last `tail` lines.

    This reads from the StreamingBody iterator to avoid loading the full object into memory.
    """
    if not s3_uri.lower().startswith('s3://'):
        raise ValueError("s3_uri must start with s3://")
    tailpath = s3_uri[5:]
    parts = tailpath.split('/', 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    bucket, key = parts[0], parts[1]

    s3 = boto3.client('s3')
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        stream = resp['Body']
        first_lines = []
        last_lines = deque(maxlen=tail)
        count = 0
        # iter_lines yields decoded strings when decode_unicode=True
        for raw in stream.iter_lines():
            if raw is None:
                continue
            # raw is bytes; decode to text safely
            if isinstance(raw, bytes):
                line = raw.decode('utf-8', errors='replace')
            else:
                line = str(raw)
            count += 1
            if len(first_lines) < head:
                first_lines.append(line)
            last_lines.append(line)
        print(f"S3 object s3://{bucket}/{key} streamed: {count} line(s)")
        if count == 0:
            print("(object is empty)")
            return
        for i, ln in enumerate(first_lines, start=1):
            print(f"STREAM-HEAD[{i}]: {ln}")
        # print tail lines
        for i, ln in enumerate(list(last_lines), start=1):
            print(f"STREAM-TAIL[{i}]: {ln}")
    except botocore_exceptions.ClientError as e:
        print(f"Failed to stream S3 object s3://{bucket}/{key}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Unexpected error streaming S3 object s3://{bucket}/{key}: {e}", file=sys.stderr)


def main():
    # Use the hard-coded S3_URI and DEST_PATH; validate credentials first
    ok = validate_aws_credentials()
    if not ok:
        print('AWS credential validation failed. Aborting.', file=sys.stderr)
        return 2

    # Demonstrate downloading the S3 object to local path
    try:
        download_s3_uri(S3_URI, DEST_PATH)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 3

    # Demonstrate reading the S3 object directly into memory and printing
    # the first and last line(s) without using the downloaded file.
    print("--- S3 preview (first/last lines) ---")
    read_s3_into_memory_and_print_lines(S3_URI, head=1, tail=1)

    # Demonstrate streaming the S3 object and printing first/last lines
    print("--- S3 streaming preview (first/last lines) ---")
    read_s3_streaming(S3_URI, head=1, tail=1)
    return 0


if __name__ == '__main__':
    import sys
    rc = main()
    sys.exit(rc if isinstance(rc, int) else 0)
