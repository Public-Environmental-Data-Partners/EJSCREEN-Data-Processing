#!/usr/bin/env python3
"""
Prototype script showing three ways to write a local file to S3.

This script validates AWS credentials and then uploads one hard-coded local CSV
to S3 three different ways: direct `upload_file`, in-memory `put_object`, and
streaming `upload_fileobj` using a lightweight file-like wrapper.

Runtime arguments:
    None. Edit the hard-coded `LOCAL_PATH` and `S3_PREFIX` values near the top of
    the file before running it.

Notes:
    - Intended as an educational S3-writing example for validation work.
    - Requires boto3/botocore and usable AWS credentials from the environment or
        a `.env` file.
    - Not intended as a reusable production utility.
"""

from pathlib import Path
import sys
from dotenv import load_dotenv

# load environment (so you can put AWS creds in a .env if desired)
load_dotenv()

# --- configuration (edit as needed) ---------------------------------------
LOCAL_PATH = './output/testS3write.csv'  # user-provided test data file
S3_PREFIX = 's3://pedp-data-preserved/ejscreen-data-processing/traffic/test/'
# We'll endup uploading/writing three objects:
# testS3write1.csv, testS3write2.csv, testS3write3.csv

try:
    import boto3
    from botocore import exceptions as botocore_exceptions
except Exception:
    print('boto3 and botocore are required. Install with: pip install boto3', file=sys.stderr)
    raise


def parse_s3_uri(s3_uri: str):
    if not s3_uri.lower().startswith('s3://'):
        raise ValueError('s3_uri must start with s3://')
    tail = s3_uri[5:]
    parts = tail.split('/', 1)
    if len(parts) != 2:
        raise ValueError(f'Invalid S3 URI: {s3_uri}')
    return parts[0], parts[1]


def validate_aws_credentials():
    """Simple STS GetCallerIdentity check."""
    try:
        sts = boto3.client('sts')
        resp = sts.get_caller_identity()
        print('AWS credentials valid:', resp.get('Arn'))
        return True
    except botocore_exceptions.NoCredentialsError:
        print('No AWS credentials found.', file=sys.stderr)
        return False
    except botocore_exceptions.ClientError as e:
        print('AWS client error validating credentials:', e, file=sys.stderr)
        return False
    except Exception as e:
        print('Unexpected error validating AWS credentials:', e, file=sys.stderr)
        return False


def upload_local_file(local_path: str, s3_uri: str):
    """Upload local file to S3 by letting boto3 handle the file transfer (upload_file)."""
    bucket, key = parse_s3_uri(s3_uri)
    s3 = boto3.client('s3')
    try:
        print(f'Uploading local file {local_path} to s3://{bucket}/{key} using upload_file...')
        s3.upload_file(Filename=str(local_path), Bucket=bucket, Key=key)
        print('upload_copy_local: done')
    except Exception as e:
        print('upload_copy_local failed:', e, file=sys.stderr)
        raise


def upload_from_memory(local_path: str, s3_uri: str):
    """Read entire file into memory and write to S3 with put_object."""
    bucket, key = parse_s3_uri(s3_uri)
    s3 = boto3.client('s3')
    try:
        data = Path(local_path).read_bytes()
        print(f'Putting object to s3://{bucket}/{key} from memory ({len(data)} bytes)...')
        s3.put_object(Bucket=bucket, Key=key, Body=data)
        print('upload_from_memory: done')
    except Exception as e:
        print('upload_from_memory failed:', e, file=sys.stderr)
        raise


class LineIterableIO:
    """Wrap an iterator of lines (strings) into a file-like object with a read() method.

    This is a minimal adapter used for boto3.upload_fileobj, which expects a
    file-like object providing read(). Our adapter pulls lines from an iterator
    and returns bytes on read().
    """
    def __init__(self, line_iter, encoding='utf-8'):
        self._iter = iter(line_iter)
        self._buffer = b''
        self._encoding = encoding
        self._exhausted = False

    def read(self, size=-1):
        # If size is negative, return all remaining data
        if size is None or size < 0:
            chunks = [self._buffer]
            self._buffer = b''
            for s in self._iter:
                chunks.append(str(s).encode(self._encoding))
            self._exhausted = True
            return b''.join(chunks)

        # Fill buffer until we have at least size bytes or iterator exhausted
        while not self._exhausted and len(self._buffer) < size:
            try:
                s = next(self._iter)
                if not isinstance(s, (bytes, bytearray)):
                    s = str(s).encode(self._encoding)
                self._buffer += s
            except StopIteration:
                self._exhausted = True
                break

        if size == 0:
            return b''
        out = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return out

    def readable(self):
        return True


def upload_streaming(local_path: str, s3_uri: str):
    """Stream local file lines to S3 using upload_fileobj with a file-like adapter."""
    bucket, key = parse_s3_uri(s3_uri)
    s3 = boto3.client('s3')
    try:
        print(f'Streaming upload of {local_path} to s3://{bucket}/{key}...')
        def line_gen():
            with open(local_path, 'r', encoding='utf-8', errors='replace') as fh:
                for line in fh:
                    yield line
        wrapped = LineIterableIO(line_gen())
        s3.upload_fileobj(Fileobj=wrapped, Bucket=bucket, Key=key)
        print('upload_streaming: done')
    except Exception as e:
        print('upload_streaming failed:', e, file=sys.stderr)
        raise


def main():
    ok = validate_aws_credentials()
    if not ok:
        print('AWS validation failed; aborting', file=sys.stderr)
        return 2

    base = S3_PREFIX.rstrip('/') + '/'
    # Destination keys
    s3_uri1 = base + 'testS3write1.csv'
    s3_uri2 = base + 'testS3write2.csv'
    s3_uri3 = base + 'testS3write3.csv'

    local = LOCAL_PATH
    p = Path(local)
    if not p.exists():
        print(f'Local test file {local} not found. Create a small CSV named {local} before running.', file=sys.stderr)
        return 3

    # 1) copy local file using upload_file
    upload_local_file(local, s3_uri1)

    # 2) upload from memory
    upload_from_memory(local, s3_uri2)

    # 3) stream upload
    upload_streaming(local, s3_uri3)

    print('All uploads completed successfully')
    return 0


if __name__ == '__main__':
    import sys
    rc = main()
    sys.exit(rc if isinstance(rc, int) else 0)
