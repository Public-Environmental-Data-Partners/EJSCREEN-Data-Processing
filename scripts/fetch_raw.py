"""fetch_raw.py


Purpose:
        Central fetch utility to download raw source files needed to generate indicator
        scores. It depends on the indicator folder containing a config .json file with
        the necessary `downloads` structure to specify the source and destination for the download.`

Usage examples:

        python scripts/fetch_raw.py --indicator pm25 --mode local (has only one file to download, so no --download key needed)
        python scripts/fetch_raw.py --indicator o3 --mode remote  (ditto)

Notes:
        - This initial slice supports indicators that expose the same helpers
            used by PM2.5 (i.e. `get_<indicator>_config()` and
            `resolve_local_<indicator>_root_path()` implemented in the
            indicator's `<indicator>_config.py`).
        - `--download` is optional for this slice; when provided the runner
            validates the key against the indicator's `<indicator>_config.json`.
        - State-scoped downloads (templates) are not supported in this phase.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import importlib.machinery
import importlib.util
import sys
import logging
from pathlib import Path
import time
import uuid

import requests

SCRIPTS_DIR = Path(__file__).resolve().parent
PROCESS_NAME = 'fetch_raw'
DEFAULT_LOG_FILENAME = 'fetch_raw.log'
DEFAULT_AUDIT_FILENAME = 'fetch_audit.csv'
HEARTBEAT_INTERVAL_SECONDS = 30.0
AUDIT_FIELDNAMES = (
    'process_name',
    'indicator',
    'run_id',
    'dl_started_at',
    'dl_ended_at',
    'filename',
    'state',
    'destination_path',
    'storage_mode',
    'status',
    'source_url',
    'bytes_downloaded',
    'message',
)


@dataclass(frozen=True, slots=True)
class Config:
    indicator: str
    storage_mode: str
    local_root_path: str
    remote_root_path: str
    raw_download_relative_path: str
    source_url: str
    request_timeout_seconds: int
    chunk_size_bytes: int


@dataclass(frozen=True, slots=True)
class DownloadResult:
    filename: str
    state: str
    destination_path: str
    storage_mode: str
    status: str
    source_url: str
    bytes_downloaded: int
    message: str


def _load_indicator_config_module(indicator: str):
    cfg_path = SCRIPTS_DIR / indicator / f'{indicator}_config.py'
    if not cfg_path.exists():
        raise FileNotFoundError(f'Indicator config not found: {cfg_path}')
    loader = importlib.machinery.SourceFileLoader(f'{indicator}_config', str(cfg_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    # Ensure the module is present in sys.modules so decorators and runtime
    # introspection (e.g. dataclasses) can locate the module object during
    # execution. This mirrors importlib.import_module behavior.
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def get_config(argv=None) -> Config:
    parser = argparse.ArgumentParser(description='Central fetch runner (Phase 2 initial slice).')
    parser.add_argument('--indicator', '-i', required=True, help='Indicator key such as pm25 or shared')
    parser.add_argument('--mode', '-m', dest='storage_mode', choices=('local', 'remote'), required=True, help='Select storage mode: local or remote')
    parser.add_argument('--download', '-d', help='Download key from the indicator config (optional for now)')
    args = parser.parse_args(argv)

    indicator = args.indicator
    cfg_module = _load_indicator_config_module(indicator)

    # Expect the indicator config to expose a get_<indicator>_config() helper and
    # a resolve_local_<indicator>_root_path(pm_dir) helper similar to PM2.5.
    get_cfg_fn_name = f'get_{indicator}_config'
    resolve_local_fn_name = f'resolve_local_{indicator}_root_path'
    if not hasattr(cfg_module, get_cfg_fn_name):
        raise AttributeError(f'Indicator config module missing {get_cfg_fn_name}')
    if not hasattr(cfg_module, resolve_local_fn_name):
        raise AttributeError(f'Indicator config module missing {resolve_local_fn_name}')

    get_cfg_fn = getattr(cfg_module, get_cfg_fn_name)
    resolve_local_fn = getattr(cfg_module, resolve_local_fn_name)
    raw_config = get_cfg_fn()

    # Default values (used when --download not provided)
    raw_download_relative_path = getattr(raw_config, 'raw_download_relative_path', None)
    source_url = getattr(raw_config, 'source_url', None)
    request_timeout_seconds = getattr(raw_config, 'request_timeout_seconds', 120)
    chunk_size_bytes = getattr(raw_config, 'chunk_size_bytes', 1048576)

    # If a download key was provided, validate it against the indicator JSON
    # configuration (this mirrors the pm25_config.json structure).
    json_cfg_path = SCRIPTS_DIR / indicator / f'{indicator}_config.json'
    if args.download:
        if not json_cfg_path.exists():
            raise FileNotFoundError(f'Indicator JSON config not found for download validation: {json_cfg_path}')
        import json
        with json_cfg_path.open('r', encoding='utf-8') as fh:
            indicator_raw = json.load(fh)
        downloads_obj = indicator_raw.get('downloads')
        if not isinstance(downloads_obj, dict) or not downloads_obj:
            raise ValueError(f'{json_cfg_path.name} missing a non-empty "downloads" object')
        # When the JSON includes top-level settings like request_timeout_seconds
        # the actual entries live alongside them; collect entry keys.
        # Accept either the flat layout used by pm25 (keys directly under
        # downloads) or a nested `entries` object in future configs.
        entries = {}
        if 'entries' in downloads_obj and isinstance(downloads_obj['entries'], dict):
            entries = downloads_obj['entries']
        else:
            # Treat other keys except known settings as entries
            for k, v in downloads_obj.items():
                if k in ('request_timeout_seconds', 'chunk_size_bytes'):
                    continue
                entries[k] = v

        if args.download not in entries:
            raise ValueError(f'Unknown download key for indicator {indicator}: {args.download}')

        selected = entries[args.download]
        # If the entry looks like a state-scoped template, reject for now
        if 'relative_path_template' in selected or 'source_url_template' in selected or selected.get('scope') == 'state':
            raise NotImplementedError('State-scoped downloads are not supported in Phase 2 initial slice')

        # Expect single-scope entry form
        if 'relative_path' not in selected or 'source_url' not in selected:
            raise ValueError('Download entry missing required "relative_path" or "source_url"')

        raw_download_relative_path = selected['relative_path']
        source_url = selected['source_url']

        # Override timeout/chunk when supplied at the downloads level
        if isinstance(downloads_obj.get('request_timeout_seconds'), int):
            request_timeout_seconds = downloads_obj['request_timeout_seconds']
        if isinstance(downloads_obj.get('chunk_size_bytes'), int):
            chunk_size_bytes = downloads_obj['chunk_size_bytes']

    # If --download not provided, require the indicator config expose the default
    if raw_download_relative_path is None or source_url is None:
        raise ValueError('Loaded indicator config does not expose expected download metadata for this slice')

    local_root = resolve_local_fn(SCRIPTS_DIR / indicator)
    remote_root = getattr(raw_config, 'remote_root_path')

    return Config(
        indicator=indicator,
        storage_mode=args.storage_mode,
        local_root_path=local_root,
        remote_root_path=remote_root,
        raw_download_relative_path=raw_download_relative_path,
        source_url=source_url,
        request_timeout_seconds=request_timeout_seconds,
        chunk_size_bytes=chunk_size_bytes,
    )


def initialize_runtime_dependencies(cfg: Config) -> None:
    if cfg.storage_mode != 'remote':
        return
    importlib.import_module('dotenv')
    importlib.import_module('s3fs')
    importlib.import_module('fsspec')
    importlib.import_module('boto3')


def load_fsspec_module():
    return importlib.import_module('fsspec')


def configure_logging() -> str:
    log_path = SCRIPTS_DIR / DEFAULT_LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[logging.FileHandler(log_path, mode='a', encoding='utf-8')],
        force=True,
    )
    logging.info('========== Log session started %s ==========', datetime.now().astimezone().isoformat(timespec='seconds'))
    return str(log_path)


def is_s3_uri(path: str) -> bool:
    return isinstance(path, str) and path.lower().startswith('s3://')


def get_active_root_path(cfg: Config) -> str:
    if cfg.storage_mode == 'local':
        return cfg.local_root_path
    if cfg.storage_mode == 'remote':
        return cfg.remote_root_path
    raise ValueError(f'Unsupported storage mode: {cfg.storage_mode}')


def join_root_and_relative_path(root_path: str, relative_path: str) -> str:
    if is_s3_uri(root_path):
        return root_path.rstrip('/') + '/' + relative_path.lstrip('/')
    return str(Path(root_path) / Path(relative_path))


def ensure_local_parent_dir(path: str) -> None:
    if not is_s3_uri(path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def open_text_output_stream(path: str, mode: str):
    ensure_local_parent_dir(path)
    if is_s3_uri(path):
        fsspec = load_fsspec_module()
        return fsspec.open(path, mode, encoding='utf-8', newline='')
    return open(path, mode, encoding='utf-8', newline='')


def open_binary_output_stream(path: str):
    ensure_local_parent_dir(path)
    if is_s3_uri(path):
        fsspec = load_fsspec_module()
        return fsspec.open(path, 'wb')
    return open(path, 'wb')


def path_exists(path: str) -> bool:
    if is_s3_uri(path):
        fsspec = load_fsspec_module()
        return bool(fsspec.open(path).fs.exists(path))
    return Path(path).exists()


def get_audit_path(cfg: Config) -> str:
    # Centralized audit lives beside this runner in the scripts folder.
    return str(SCRIPTS_DIR / DEFAULT_AUDIT_FILENAME)


def create_run_id() -> str:
    return uuid.uuid4().hex


def build_audit_row(*, run_id: str, dl_started_at: str, dl_ended_at: str, indicator: str, result: DownloadResult) -> dict[str, str | int]:
    return {
        'process_name': PROCESS_NAME,
        'indicator': indicator,
        'run_id': run_id,
        'dl_started_at': dl_started_at,
        'dl_ended_at': dl_ended_at,
        'filename': result.filename,
        'state': result.state,
        'destination_path': result.destination_path,
        'storage_mode': result.storage_mode,
        'status': result.status,
        'source_url': result.source_url,
        'bytes_downloaded': result.bytes_downloaded,
        'message': result.message,
    }


def append_audit_row(path: str, row: dict[str, str | int]) -> None:
    file_exists = path_exists(path)
    mode = 'a' if file_exists else 'w'
    with open_text_output_stream(path, mode) as output_stream:
        writer = csv.DictWriter(output_stream, fieldnames=list(AUDIT_FIELDNAMES))
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def download_file(session: requests.Session, cfg: Config, destination_path: str, filename: str) -> DownloadResult:
    if path_exists(destination_path):
        logging.info('Skipping download for %s because destination already exists: %s', filename, destination_path)
        return DownloadResult(
            filename=filename,
            state='ALL',
            destination_path=destination_path,
            storage_mode=cfg.storage_mode,
            status='skipped',
            source_url=cfg.source_url,
            bytes_downloaded=0,
            message='destination already existed',
        )

    logging.info('Downloading from %s', cfg.source_url)
    total_bytes = 0
    dest_path = Path(destination_path)
    ensure_local_parent_dir(destination_path)
    started_at = time.monotonic()
    last_heartbeat_at = started_at

    if cfg.storage_mode == 'local':
        temp_dest = dest_path.with_name(dest_path.name + '.tmp')
        try:
            with session.get(cfg.source_url, stream=True, timeout=cfg.request_timeout_seconds) as response:
                response.raise_for_status()
                logging.info('Connected to source URL. HTTP %s. Content-Length=%s', response.status_code, response.headers.get('Content-Length', 'unknown'))
                with temp_dest.open('wb') as f:
                    for chunk in response.iter_content(chunk_size=cfg.chunk_size_bytes):
                        if not chunk:
                            continue
                        f.write(chunk)
                        total_bytes += len(chunk)
                        now = time.monotonic()
                        if now - last_heartbeat_at >= HEARTBEAT_INTERVAL_SECONDS:
                            elapsed_seconds = max(now - started_at, 0.001)
                            bytes_per_second = total_bytes / elapsed_seconds
                            logging.info('Downloading %s: %d bytes so far (%.1f B/s)', filename, total_bytes, bytes_per_second)
                            print(f'Bytes downloaded so far: {total_bytes}', flush=True)
                            last_heartbeat_at = now
            temp_dest.replace(dest_path)
        except Exception:
            try:
                temp_dest.unlink()
            except Exception:
                pass
            raise
    else:
        try:
            with session.get(cfg.source_url, stream=True, timeout=cfg.request_timeout_seconds) as response:
                response.raise_for_status()
                logging.info('Connected to source URL. HTTP %s. Content-Length=%s', response.status_code, response.headers.get('Content-Length', 'unknown'))
                with open_binary_output_stream(destination_path) as out_stream:
                    for chunk in response.iter_content(chunk_size=cfg.chunk_size_bytes):
                        if not chunk:
                            continue
                        out_stream.write(chunk)
                        total_bytes += len(chunk)
                        now = time.monotonic()
                        if now - last_heartbeat_at >= HEARTBEAT_INTERVAL_SECONDS:
                            elapsed_seconds = max(now - started_at, 0.001)
                            bytes_per_second = total_bytes / elapsed_seconds
                            logging.info('Downloading %s: %d bytes so far (%.1f B/s)', filename, total_bytes, bytes_per_second)
                            print(f'Bytes downloaded so far: {total_bytes}', flush=True)
                            last_heartbeat_at = now
        except Exception:
            try:
                if is_s3_uri(destination_path):
                    fsspec = load_fsspec_module()
                    fs = fsspec.open(destination_path).fs
                    if fs.exists(destination_path):
                        fs.rm(destination_path)
            except Exception:
                pass
            raise

    logging.info('Wrote %s bytes to %s', total_bytes, destination_path)
    return DownloadResult(
        filename=filename,
        state='ALL',
        destination_path=destination_path,
        storage_mode=cfg.storage_mode,
        status='uploaded',
        source_url=cfg.source_url,
        bytes_downloaded=total_bytes,
        message='downloaded and uploaded',
    )


def main(argv=None) -> int:
    log_path = configure_logging()
    cfg = get_config(argv)
    run_id = create_run_id()
    audit_path = get_audit_path(cfg)
    destination_path = join_root_and_relative_path(get_active_root_path(cfg), cfg.raw_download_relative_path)
    filename = Path(cfg.raw_download_relative_path).name

    logging.info('Logging to %s', log_path)
    logging.info('Run ID: %s', run_id)
    logging.info('Audit CSV path: %s', audit_path)
    logging.info('Storage mode: %s', cfg.storage_mode)
    logging.info('Indicator: %s', cfg.indicator)
    logging.info('Active root path: %s', get_active_root_path(cfg))
    logging.info('Destination path: %s', destination_path)
    logging.info('Source URL: %s', cfg.source_url)
    initialize_runtime_dependencies(cfg)

    with requests.Session() as session:
        dl_started_at = datetime.now().astimezone().isoformat(timespec='seconds')
        try:
            result = download_file(session, cfg, destination_path, filename)
        except Exception as exc:
            dl_ended_at = datetime.now().astimezone().isoformat(timespec='seconds')
            append_audit_row(
                audit_path,
                build_audit_row(
                    run_id=run_id,
                    dl_started_at=dl_started_at,
                    dl_ended_at=dl_ended_at,
                    indicator=cfg.indicator,
                    result=DownloadResult(
                        filename=filename,
                        state='ALL',
                        destination_path=destination_path,
                        storage_mode=cfg.storage_mode,
                        status='failed',
                        source_url=cfg.source_url,
                        bytes_downloaded=0,
                        message=str(exc),
                    ),
                ),
            )
            raise

        dl_ended_at = datetime.now().astimezone().isoformat(timespec='seconds')
        append_audit_row(
            audit_path,
            build_audit_row(
                run_id=run_id,
                dl_started_at=dl_started_at,
                dl_ended_at=dl_ended_at,
                indicator=cfg.indicator,
                result=result,
            ),
        )

    logging.info('Completed fetch with status %s', result.status)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except FileNotFoundError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
    except (ValueError, AttributeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
    except NotImplementedError as exc:
        print(f'NOT SUPPORTED: {exc}', file=sys.stderr)
        sys.exit(3)
    except Exception as exc:  # pragma: no cover - unexpected
        # Best-effort logging if logging was configured; otherwise print minimal info.
        try:
            logging.exception('Unhandled exception in fetch_raw')
        except Exception:
            pass
        print('An unexpected error occurred. See fetch_raw.log for details.', file=sys.stderr)
        sys.exit(1)
