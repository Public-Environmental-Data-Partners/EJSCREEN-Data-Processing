"""fetch_raw.py

Purpose:
    Central fetch utility to download raw source files needed to generate indicator
    scores. It depends on the stage-based indicator and shared config .json files
    to specify fetch outputs, source locations, and destination paths.

Process summary:
    - Resolve the indicator or shared config, stage manifest, and canonical local/remote
        root paths for the requested target.
    - Validate `--state` (or expand the source/destination templates for every configured
        state when `--state all` is requested).
    - Skip the download if the destination file already exists.
    - Stream the source URL to the destination, logging heartbeat progress periodically.
    - Append one row per attempted download to fetch_audit.csv.
    - Print and log a run summary (succeeded/failed/skipped counts).

Runtime arguments (current defaults shown):
    - -i/--indicator: required. Indicator key such as `pm25`, `o3`, or `shared`.
    - -l/--location: required one of `local` or `remote`.
    - -d/--download: optional fetch-stage output key. Required when the selected target has
        multiple fetch outputs, and always required for shared fetches.
    - -v/--version: optional; required only when the selected target has more than one
        configured version.
    - -s/--state: optional two-letter postal code (e.g. `VT`) for state-scoped downloads.
        Validated against `scripts/shared/state_config.json` and translated to the FIPS code
        used in filenames and URLs. The postal code (uppercased) is stored in the audit. The
        special value `all` (case-insensitive) iterates across all configured states.
    - --dry-run: long-only flag. Prints the expanded source URL and destination path without
        downloading.

Outputs:
    - The downloaded raw file at the resolved local path or S3 URI.
    - One appended row per attempted download in fetch_audit.csv (scripts/shared), recording
        run id, timing, status (`uploaded`/`skipped`/`failed`), and bytes downloaded.
    - fetch_raw.log in scripts/shared.

Examples (run from the `scripts` folder):
    - Simple indicator with a default download:
        python3 shared/fetch_raw.py --indicator pm25 -v 1.2020 -l local

    - Remote fetch for ozone:
        python3 shared/fetch_raw.py --indicator o3 -l remote

    - Shared tiger BG download for Vermont (postal code). --state uses two-letter postal codes:
        python3 shared/fetch_raw.py --indicator shared --download tiger_bg_2020 --state VT -l local
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import importlib.machinery
import importlib.util
import json
import sys
import logging
from pathlib import Path
import time
import uuid

import requests

import scripts.shared.build_manifest as build_manifest
import scripts.shared.resolve_path as resolve_path

SCRIPTS_DIR = resolve_path.SCRIPTS_ROOT
# Handy reference to the shared folder for loading sibling config/state files.
SHARED_DIR = SCRIPTS_DIR / "shared"

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
    version: str
    storage_mode: str
    local_root_path: str
    remote_root_path: str
    raw_download_relative_path: str
    source_url: str
    request_timeout_seconds: int
    chunk_size_bytes: int
    state: str | None = None
    dry_run: bool = False
    state_all_mode: bool = False


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

def _load_shared_state_module():
    """Load the canonical shared state_config.py as a module and return it.

    This mirrors how indicator config modules are loaded so we get the
    same import semantics and dataclass behavior.
    """
    # Shared state config should live beside this runner in scripts/shared/
    state_py = SHARED_DIR / 'state_config.py'
    if not state_py.exists():
        raise FileNotFoundError(f'Shared state config module not found: {state_py}')
    loader = importlib.machinery.SourceFileLoader('shared_state_config', str(state_py))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def _load_json_object(config_path: Path, label: str) -> dict[str, object]:
    if not config_path.exists():
        raise FileNotFoundError(f'{label} config not found: {config_path}')
    with config_path.open('r', encoding='utf-8') as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f'{label} config must contain a JSON object: {config_path}')
    return payload


def _require_mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f'Expected {field_name} to be a JSON object')
    return value


def _require_non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f'Expected {field_name} to be a non-empty string')
    return value


def _require_positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f'Expected {field_name} to be a positive integer')
    return value


# local root resolution is now provided by `resolve_path.get_indicator_root`
# and `resolve_path.get_shared_root`.


def _resolve_version(versions: dict[str, object], label: str, requested_version: str | None) -> str:
    if requested_version:
        if requested_version not in versions:
            raise ValueError(f'Unknown version {requested_version!r} for {label}')
        return requested_version

    available_versions = sorted(versions.keys())
    if len(available_versions) != 1:
        joined = ', '.join(available_versions)
        raise ValueError(f'{label} requires an explicit --version. Available versions: {joined}')
    return available_versions[0]


def _get_download_settings(config: dict[str, object], field_prefix: str) -> tuple[int, int]:
    settings = _require_mapping(config.get('download_settings'), f'{field_prefix}.download_settings')
    timeout = _require_positive_int(settings.get('request_timeout_seconds'), f'{field_prefix}.download_settings.request_timeout_seconds')
    chunk_size = _require_positive_int(settings.get('chunk_size_bytes'), f'{field_prefix}.download_settings.chunk_size_bytes')
    return timeout, chunk_size


def _resolve_fetch_output_key(outputs: dict[str, dict[str, str]], requested_key: str | None, label: str) -> str:
    if requested_key:
        if requested_key not in outputs:
            available = ', '.join(sorted(outputs.keys()))
            raise ValueError(f'Unknown fetch output key for {label}: {requested_key}. Available keys: {available}')
        return requested_key

    if len(outputs) != 1:
        available = ', '.join(sorted(outputs.keys()))
        raise ValueError(f'{label} requires --download because multiple fetch outputs are available: {available}')
    return next(iter(outputs))


def _resolve_shared_fetch_target(shared_config: dict[str, object], download_key: str, requested_version: str | None) -> tuple[str, str]:
    assets = _require_mapping(shared_config.get('assets'), 'shared.assets')
    matches: list[tuple[str, str, dict[str, object]]] = []

    for asset_name, versions_obj in assets.items():
        if not isinstance(versions_obj, dict):
            continue
        for version, version_block in versions_obj.items():
            if requested_version and version != requested_version:
                continue
            if not isinstance(version_block, dict):
                continue
            stages = version_block.get('stages')
            if not isinstance(stages, dict):
                continue
            fetch_stage = stages.get('fetch')
            if not isinstance(fetch_stage, dict):
                continue
            outputs = fetch_stage.get('outputs')
            if not isinstance(outputs, dict):
                continue
            output_entry = outputs.get(download_key)
            if isinstance(output_entry, dict):
                matches.append((asset_name, version, output_entry))

    if not matches:
        if requested_version:
            raise ValueError(f'Unknown shared fetch output key {download_key!r} for version {requested_version!r}')
        raise ValueError(f'Unknown shared fetch output key {download_key!r}')

    if len(matches) > 1:
        joined = ', '.join(f'{asset}@{version}' for asset, version, _ in matches)
        raise ValueError(
            f'Shared fetch output key {download_key!r} is ambiguous across targets: {joined}. Supply --version.'
        )

    # Return only the asset name and version; callers should look up the
    # specific output entry from the manifest rather than relying on raw
    # config objects.
    asset_name, version, _ = matches[0]
    return asset_name, version


def _resolve_fetch_source_and_relative(
    entry: dict[str, object],
    manifest_relative: str,
    validated_postal: str | None,
    validated_fips: str | None,
    state_all_requested: bool,
) -> tuple[str, str, bool]:
    source_url = entry.get('source_url')
    source_url_template = entry.get('source_url_template')
    is_state_scoped = bool(entry.get('scope') == 'state' or isinstance(source_url_template, str))

    if is_state_scoped:
        if not isinstance(source_url_template, str) or not source_url_template:
            raise ValueError('State-scoped fetch output must define source_url_template')
        if state_all_requested:
            return manifest_relative, source_url_template, True
        if not validated_postal or not validated_fips:
            raise ValueError('This fetch output requires a valid --state/-s two-letter postal code')
        try:
            relative = manifest_relative.format(fips=validated_fips, postal=validated_postal)
            source = source_url_template.format(fips=validated_fips, postal=validated_postal)
        except Exception as exc:
            raise ValueError(f'Failed to expand state templates for fetch output: {exc}')
        return relative, source, False

    if not isinstance(source_url, str) or not source_url:
        raise ValueError('Fetch output must define source_url')
    return manifest_relative, source_url, False


def get_config(argv=None) -> Config:
    parser = argparse.ArgumentParser(description='Centralized raw data fetch utility.')
    # Required arguments (listed first)
    parser.add_argument('-i', '--indicator', required=True, help='Required: Indicator key such as pm25 or shared')
    parser.add_argument('-l', '--location', dest='storage_mode', choices=('local', 'remote'), required=True, help='Required: Select storage location (local or remote)')

    # Optional arguments
    parser.add_argument('-d', '--download', help='Optional: Fetch-stage output key from the stage-based config')
    parser.add_argument('-v', '--version', help='Optional: Explicit config version; required when multiple versions exist')
    parser.add_argument('-s', '--state', dest='state', help='Optional: Two-letter postal state code for state-scoped downloads (e.g. VT)')
    parser.add_argument('--dry-run', action='store_true', help='Optional: Print expanded source URL and destination path and exit')
 
    args = parser.parse_args(argv)
    indicator = args.indicator

    # If the user provided a --state value, validate it early and obtain the
    # canonical postal and fips values from the shared state config. Support
    # special value 'all' (case-insensitive) to indicate we should iterate
    # across every state in the shared config for state-scoped downloads.
    validated_postal = None
    validated_fips = None
    state_all_requested = False
    if getattr(args, 'state', None):
        if isinstance(args.state, str) and args.state.strip().lower() == 'all':
            state_all_requested = True
        else:
            try:
                state_mod = _load_shared_state_module()
                if not hasattr(state_mod, 'get_state_config'):
                    raise RuntimeError('Loaded shared state module does not expose get_state_config')
                state_cfg = state_mod.get_state_config(args.state)
                validated_fips = getattr(state_cfg, 'fips')
                validated_postal = getattr(state_cfg, 'postal')
            except Exception as exc:
                raise ValueError(f'Invalid --state value {args.state!r}: {exc}')

    state_all_mode = False

    if indicator == 'shared':
        if not args.download:
            raise ValueError('Shared fetch runs require --download to select a shared fetch output key')

        shared_config = _load_json_object(SHARED_DIR / 'shared_config.json', 'shared')
        request_timeout_seconds, chunk_size_bytes = _get_download_settings(shared_config, 'shared')
        asset_name, version = _resolve_shared_fetch_target(shared_config, args.download, args.version)
        # Build manifest for the requested environment; resolver accessors
        # provide canonical roots so we don't need local/remote manifests here.
        manifest = build_manifest.get_stage_manifest(
            target_type='shared',
            name=asset_name,
            stage='fetch',
            version=version,
            environment=args.storage_mode,
        )

        output_key = _resolve_fetch_output_key(manifest['outputs'], args.download, f'shared asset {asset_name}')
        manifest_output = manifest['outputs'][output_key]
        raw_download_relative_path, source_url, state_all_mode = _resolve_fetch_source_and_relative(
            manifest_output,
            manifest_output['relative'],
            validated_postal,
            validated_fips,
            state_all_requested,
        )

        # Obtain canonical roots from the resolver accessors instead of
        # reading them from the JSON config directly.
        local_root = resolve_path.get_shared_root(asset_name, version, 'local')
        remote_root = resolve_path.get_shared_root(asset_name, version, 'remote')
    else:
        indicator_config = _load_json_object(SCRIPTS_DIR / indicator / f'{indicator}_config.json', f'indicator {indicator}')
        versions = _require_mapping(indicator_config.get('versions'), f'{indicator}.versions')
        version = _resolve_version(versions, f'indicator {indicator}', args.version)
        request_timeout_seconds, chunk_size_bytes = _get_download_settings(indicator_config, indicator)
        version_block = _require_mapping(versions.get(version), f'{indicator}.versions.{version}')
        stages = _require_mapping(version_block.get('stages'), f'{indicator}.versions.{version}.stages')
        fetch_stage = _require_mapping(stages.get('fetch'), f'{indicator}.versions.{version}.stages.fetch')
        # fetch_outputs no longer needed here; manifest provides source metadata
        # Obtain manifests for each environment so we can use the manifest
        # roots instead of reading roots from the indicator config.
        manifest = build_manifest.get_stage_manifest(
            target_type='indicator',
            name=indicator,
            stage='fetch',
            version=version,
            environment=args.storage_mode,
        )

        output_key = _resolve_fetch_output_key(manifest['outputs'], args.download, f'indicator {indicator}')
        manifest_output = manifest['outputs'][output_key]
        raw_download_relative_path, source_url, state_all_mode = _resolve_fetch_source_and_relative(
            manifest_output,
            manifest_output['relative'],
            validated_postal,
            validated_fips,
            state_all_requested,
        )

        local_root = resolve_path.get_indicator_root(indicator, version, 'local')
        remote_root = resolve_path.get_indicator_root(indicator, version, 'remote')

    return Config(
        indicator=indicator,
        version=version,
        storage_mode=args.storage_mode,
        local_root_path=local_root,
        remote_root_path=remote_root,
        raw_download_relative_path=raw_download_relative_path,
        source_url=source_url,
        request_timeout_seconds=request_timeout_seconds,
        chunk_size_bytes=chunk_size_bytes,
        state=validated_postal or (args.state.strip().upper() if getattr(args, 'state', None) else ''),
        dry_run=bool(getattr(args, 'dry_run', False)),
        state_all_mode=state_all_mode,
    )


def initialize_runtime_dependencies(cfg: Config) -> None:
    if cfg.storage_mode != 'remote':
        return
    dotenv =importlib.import_module('dotenv')
    importlib.import_module('s3fs')
    importlib.import_module('fsspec')
    importlib.import_module('boto3')
    dotenv.load_dotenv()


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
        'state': result.state or '',
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
            state=cfg.state or '',
            destination_path=destination_path,
            storage_mode=cfg.storage_mode,
            status='skipped',
            source_url=cfg.source_url,
            bytes_downloaded=0,
            message='skipped, destination already existed',
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
        state=cfg.state or '',
        destination_path=destination_path,
        storage_mode=cfg.storage_mode,
        status='uploaded',
        source_url=cfg.source_url,
        bytes_downloaded=total_bytes,
        message='download successful',
    )


def main(argv=None) -> int:
    log_path = configure_logging()
    cfg = get_config(argv)
    run_id = create_run_id()
    audit_path = get_audit_path(cfg)
    logging.info('Logging to %s', log_path)
    logging.info('Run ID: %s', run_id)
    logging.info('Audit CSV path: %s', audit_path)
    logging.info('Storage mode: %s', cfg.storage_mode)
    logging.info('Indicator: %s', cfg.indicator)
    logging.info('Active root path: %s', get_active_root_path(cfg))
    initialize_runtime_dependencies(cfg)

    # If state_all_mode is enabled, iterate over every state in the shared
    # state config and perform the download for each state's expanded template.
    if cfg.state_all_mode:
        try:
            state_mod = _load_shared_state_module()
            # Use the public accessor to obtain the list of configured states
            # rather than reaching into the module's private JSON loader.
            state_list = state_mod.get_state_config_list('ALL')
        except Exception as exc:
            raise RuntimeError(f'Failed to load shared state configs for --state all: {exc}')

        with requests.Session() as session:
            overall_result_status = 'completed'
            total_states = len(state_list)
            succeeded = 0
            failed = 0
            skipped = 0
            # Iterate deterministic order by postal code using the lightweight
            # `StateConfig` objects returned by `get_state_config_list()`.
            for idx, cand in enumerate(sorted(state_list, key=lambda s: s.postal), start=1):
                postal_up = getattr(cand, 'postal') or ''
                fips = getattr(cand, 'fips') or ''
                # Require both postal and fips to be non-blank; skip otherwise.
                if not postal_up or not fips:
                    logging.error('Skipping state entry with missing postal or fips: postal=%r fips=%r', postal_up, fips)
                    failed += 1
                    print(f'[{idx}/{total_states}] {postal_up or "<missing>"}: skipped (missing postal or fips)')
                    continue

                # Expand templates for this state
                try:
                    expanded_rel = cfg.raw_download_relative_path.format(fips=fips, postal=postal_up)
                    expanded_src = cfg.source_url.format(fips=fips, postal=postal_up)
                except Exception as exc:
                    logging.error('Failed to expand templates for state %s: %s', postal_up, exc)
                    failed += 1
                    print(f'[{idx}/{total_states}] {postal_up}: failed (template expansion)')
                    continue

                destination_path = join_root_and_relative_path(get_active_root_path(cfg), expanded_rel)
                filename = Path(expanded_rel).name

                if cfg.dry_run:
                    skipped += 1
                    print(f'[{idx}/{total_states}] {postal_up}: dry-run (src={expanded_src} dest={destination_path})')
                    logging.info('Dry run for %s: src=%s dest=%s', postal_up, expanded_src, destination_path)
                    continue

                dl_started_at = datetime.now().astimezone().isoformat(timespec='seconds')
                try:
                    temp_cfg = Config(
                        indicator=cfg.indicator,
                        version=cfg.version,
                        storage_mode=cfg.storage_mode,
                        local_root_path=cfg.local_root_path,
                        remote_root_path=cfg.remote_root_path,
                        raw_download_relative_path=expanded_rel,
                        source_url=expanded_src,
                        request_timeout_seconds=cfg.request_timeout_seconds,
                        chunk_size_bytes=cfg.chunk_size_bytes,
                        state=postal_up,
                        dry_run=False,
                        state_all_mode=False,
                    )

                    result = download_file(session, temp_cfg, destination_path, filename)
                except Exception as exc:
                    overall_result_status = 'failed'
                    failed += 1
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
                                state=postal_up,
                                destination_path=destination_path,
                                storage_mode=cfg.storage_mode,
                                status='failed',
                                source_url=expanded_src,
                                bytes_downloaded=0,
                                message=str(exc),
                            ),
                        ),
                    )
                    logging.exception('Download failed for state %s', postal_up)
                    print(f'[{idx}/{total_states}] {postal_up}: failed')
                    continue

                # Record result counts and print progress
                if result.status == 'uploaded':
                    succeeded += 1
                    status_print = 'succeeded'
                elif result.status == 'skipped':
                    skipped += 1
                    status_print = 'skipped'
                else:
                    failed += 1
                    status_print = result.status

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

                print(f'[{idx}/{total_states}] {postal_up}: {status_print}')

            # Summary
            print('Multi-state run summary:')
            print(f'  succeeded: {succeeded}')
            print(f'  failed:    {failed}')
            print(f'  skipped:   {skipped}')
            logging.info('Completed multi-state fetch run %s with status %s (succeeded=%d failed=%d skipped=%d)', run_id, overall_result_status, succeeded, failed, skipped)
            return 0

    # Single-download path
    destination_path = join_root_and_relative_path(get_active_root_path(cfg), cfg.raw_download_relative_path)
    filename = Path(cfg.raw_download_relative_path).name

    logging.info('Destination path: %s', destination_path)
    logging.info('Source URL: %s', cfg.source_url)

    if cfg.dry_run:
        print('DRY RUN:')
        print(f'  indicator: {cfg.indicator}')
        print(f'  storage_mode: {cfg.storage_mode}')
        print(f'  state: {cfg.state or ""}')
        print(f'  source_url: {cfg.source_url}')
        print(f'  destination_path: {destination_path}')
        # Provide a short, human-friendly summary for quick inspection
        print('SUMMARY:')
        print('  total: 1')
        print('  succeeded: 0')
        print('  failed: 0')
        print('  skipped: 1')
        print(f'  filename: {filename}')
        print(f'  destination: {destination_path}')
        print('  status: dry-run')
        logging.info('Dry run requested; no download performed')
        return 0

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
                        state=cfg.state or '',
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

    # Print a concise summary for single-download runs
    try:
        total = 1
        succeeded = 1 if result.status == 'uploaded' else 0
        skipped = 1 if result.status == 'skipped' else 0
        failed = 0 if result.status in ('uploaded', 'skipped') else 1
    except Exception:
        total = succeeded = skipped = failed = 0

    print('SUMMARY:')
    print(f'  total: {total}')
    print(f'  succeeded: {succeeded}')
    print(f'  failed: {failed}')
    print(f'  skipped: {skipped}')
    print(f'  filename: {result.filename}')
    print(f'  destination: {result.destination_path}')
    print(f'  bytes_downloaded: {result.bytes_downloaded}')
    print(f'  status: {result.status}')
    if result.message:
        print(f'  message: {result.message}')

    logging.info('Completed fetch with status %s', result.status)
    return 0


if __name__ == '__main__':
    logging.info("Starting fetch_raw script")
    logging.info("SCRIPTS_DIR: %s", SCRIPTS_DIR)
    try:
        raise SystemExit(main())
    except FileNotFoundError as exc:
        # Log the command line and the error message for easier debugging of
        # configuration or missing-file issues that occur during validation.
        try:
            logging.error('FileNotFound / config error: %s; argv: %s', exc, ' '.join(sys.argv))
        except Exception:
            pass
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
    except (ValueError, AttributeError) as exc:
        # Validation/usage errors should be visible in the log alongside the
        # command line used to invoke the script so users can quickly diagnose
        # incorrect flags or missing parameters.
        try:
            logging.error('Commandline validation error: %s; argv: %s', exc, ' '.join(sys.argv))
        except Exception:
            pass
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
    except NotImplementedError as exc:
        # Feature-not-supported messages are useful to log as well.
        try:
            logging.error('NotImplemented: %s; argv: %s', exc, ' '.join(sys.argv))
        except Exception:
            pass
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
