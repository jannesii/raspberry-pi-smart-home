#!/usr/bin/env python3
"""Fetch the current Tuya local key for a device via Tuya Cloud."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import tinytuya

logger = logging.getLogger(__name__)

"""
.venv/bin/python scripts/fetch_tuya_local_key.py \
  --api-region eu \
  --api-key '<API_KEY>' \
  --api-secret '<API_SECRET>' \
  --api-device-id '<API_DEVICE_ID>' \
  --write-env \
  --verbose

"""


@dataclass
class CloudLookupConfig:
    """Resolved runtime configuration for the Tuya key lookup."""

    env_file: Path
    device_id: str
    api_region: str
    api_key: str
    api_secret: str
    api_device_id: str
    write_env: bool


@dataclass
class DeviceKeyResult:
    """Resolved device details from Tuya Cloud."""

    device_id: str
    name: str
    local_key: str
    product_id: str | None
    uid: str | None


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Fetch the current Tuya local key for the configured AC device."
    )
    parser.add_argument(
        "--env-file",
        default="jannenkoti.env",
        help="Path to a systemd-style env file. Default: %(default)s",
    )
    parser.add_argument(
        "--device-id", help="Target Tuya device ID. Defaults to AC_DEV_ID from env."
    )
    parser.add_argument(
        "--api-region",
        help="Tuya Cloud API region, for example eu/us/in/sg. Defaults to TUYA_API_REGION from env.",
    )
    parser.add_argument("--api-key", help="Tuya Cloud API key.")
    parser.add_argument("--api-secret", help="Tuya Cloud API secret.")
    parser.add_argument(
        "--api-device-id",
        help="Any device ID linked to the Tuya Cloud project for initial user lookup.",
    )
    parser.add_argument(
        "--write-env",
        action="store_true",
        help="Write the fetched key back to AC_LOCAL_KEY in the env file.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the result as JSON instead of human-readable text.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args()


def configure_logging(verbose: bool) -> None:
    """Configure process logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s:%(name)s:%(message)s")


def parse_env_file(env_file: Path) -> tuple[dict[str, str], list[str]]:
    """Parse a systemd-style EnvironmentFile preserving original lines."""
    logger.debug("Parsing env file: %s", env_file)
    if not env_file.exists():
        raise FileNotFoundError(f"Env file not found: {env_file}")

    raw_lines = env_file.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}

    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

    logger.debug("Parsed env keys: %s", sorted(values))
    return values, raw_lines


def resolve_config(args: argparse.Namespace) -> CloudLookupConfig:
    """Resolve runtime config from CLI args, env vars, and env file values."""
    env_file = Path(args.env_file).expanduser().resolve()
    env_values, _ = parse_env_file(env_file)

    device_id = args.device_id or env_values.get("AC_DEV_ID") or os.getenv("AC_DEV_ID")
    api_region = (
        args.api_region or env_values.get("TUYA_API_REGION") or os.getenv("TUYA_API_REGION")
    )
    api_key = args.api_key or env_values.get("TUYA_API_KEY") or os.getenv("TUYA_API_KEY")
    api_secret = (
        args.api_secret or env_values.get("TUYA_API_SECRET") or os.getenv("TUYA_API_SECRET")
    )
    api_device_id = (
        args.api_device_id
        or env_values.get("TUYA_API_DEVICE_ID")
        or os.getenv("TUYA_API_DEVICE_ID")
        or device_id
    )

    missing = [
        name
        for name, value in (
            ("device_id", device_id),
            ("api_region", api_region),
            ("api_key", api_key),
            ("api_secret", api_secret),
            ("api_device_id", api_device_id),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "Missing required config: "
            + ", ".join(missing)
            + ". Set CLI flags or TUYA_API_REGION/TUYA_API_KEY/TUYA_API_SECRET/TUYA_API_DEVICE_ID."
        )

    cfg = CloudLookupConfig(
        env_file=env_file,
        device_id=str(device_id),
        api_region=str(api_region),
        api_key=str(api_key),
        api_secret=str(api_secret),
        api_device_id=str(api_device_id),
        write_env=bool(args.write_env),
    )
    logger.debug(
        "Resolved cloud lookup config env_file=%s device_id=%s api_region=%s api_device_id=%s write_env=%s",
        cfg.env_file,
        cfg.device_id,
        cfg.api_region,
        cfg.api_device_id,
        cfg.write_env,
    )
    return cfg


def fetch_device_local_key(cfg: CloudLookupConfig) -> DeviceKeyResult:
    """Fetch device metadata and local key from Tuya Cloud."""
    logger.debug("Creating TinyTuya Cloud client for region=%s", cfg.api_region)
    cloud = tinytuya.Cloud(
        apiRegion=cfg.api_region,
        apiKey=cfg.api_key,
        apiSecret=cfg.api_secret,
        apiDeviceID=cfg.api_device_id,
    )

    logger.debug("Requesting direct device details from Tuya Cloud")
    device_detail = cloud._getdevice("details", cfg.device_id)
    logger.debug("Direct device detail response: %s", device_detail)
    direct_result = device_detail.get("result") if isinstance(device_detail, dict) else None
    if isinstance(direct_result, dict):
        resolved = _device_result_from_cloud_record(cfg.device_id, direct_result)
        if resolved is not None:
            logger.info("Resolved local key from direct device details for %s", cfg.device_id)
            return resolved

    logger.debug("Requesting device list from Tuya Cloud")
    devices = cloud.getdevices(verbose=False)
    if isinstance(devices, dict):
        devices = devices.get("result")
    if not isinstance(devices, list):
        raise RuntimeError(f"Unexpected device list response: {devices!r}")

    logger.info("Fetched %s device records from Tuya Cloud", len(devices))
    for device in devices:
        resolved = _device_result_from_cloud_record(cfg.device_id, device)
        if resolved is None:
            continue
        logger.debug("Resolved Tuya device result from device list: %s", resolved)
        return resolved

    known_ids = [str(device.get("id") or "") for device in devices if device.get("id")]
    logger.debug("Device ID %s not found in returned device IDs: %s", cfg.device_id, known_ids)
    raise RuntimeError(f"Target device {cfg.device_id} was not found in Tuya Cloud device list.")


def _device_result_from_cloud_record(
    target_device_id: str,
    device: dict[str, Any] | None,
) -> DeviceKeyResult | None:
    """Build a result object when a cloud device record matches the target device."""
    if not isinstance(device, dict):
        return None
    if str(device.get("id") or "") != target_device_id:
        return None

    local_key = str(device.get("key") or device.get("local_key") or "").strip()
    if not local_key:
        raise RuntimeError(
            "Device found in Tuya Cloud, but no local key was returned. "
            "Confirm the cloud project is linked to the same Smart Life/Tuya app account."
        )

    return DeviceKeyResult(
        device_id=target_device_id,
        name=str(device.get("name") or ""),
        local_key=local_key,
        product_id=device.get("product_id"),
        uid=device.get("uid"),
    )


def update_env_local_key(env_file: Path, raw_lines: list[str], new_key: str) -> None:
    """Update AC_LOCAL_KEY in the env file while preserving line layout."""
    logger.debug("Updating AC_LOCAL_KEY in env file: %s", env_file)
    updated = False
    new_lines: list[str] = []

    for line in raw_lines:
        stripped = line.strip()
        if stripped.startswith("AC_LOCAL_KEY") and "=" in stripped:
            left, _ = line.split("=", 1)
            new_lines.append(f'{left}= "{new_key}"')
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f'AC_LOCAL_KEY = "{new_key}"')

    env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    logger.info("Updated AC_LOCAL_KEY in %s", env_file)


def print_result(result: DeviceKeyResult, as_json: bool) -> None:
    """Print the lookup result."""
    if as_json:
        print(json.dumps(asdict(result), indent=2))
        return

    print(f"Device ID: {result.device_id}")
    print(f"Name: {result.name or '-'}")
    print(f"Product ID: {result.product_id or '-'}")
    print(f"UID: {result.uid or '-'}")
    print(f"Local key: {result.local_key}")


def main() -> int:
    """Run the CLI."""
    args = parse_args()
    configure_logging(args.verbose)

    try:
        cfg = resolve_config(args)
        env_values, raw_lines = parse_env_file(cfg.env_file)
        logger.debug(
            "Loaded env file values for update path; AC_DEV_ID=%s", env_values.get("AC_DEV_ID")
        )
        result = fetch_device_local_key(cfg)
        print_result(result, args.json)
        if cfg.write_env:
            update_env_local_key(cfg.env_file, raw_lines, result.local_key)
        return 0
    except Exception as exc:
        logger.error("Failed to fetch Tuya local key: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
