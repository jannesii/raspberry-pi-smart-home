#!/usr/bin/env python3
"""Send WebSocket RPC commands to a temperature ESP through Redis.

The temperature firmware no longer exposes a local HTTP API. Devices connect
outbound to the shared esp32_ws service, and operational commands are routed
through Redis:

  python esp_api_tester.py --device-id temperature_kitchen read_now
  python esp_api_tester.py --device-id temperature_kitchen get_config
  python esp_api_tester.py --device-id temperature_kitchen update_config \
    --params-json '{"post_interval_sec": 120}'
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from typing import Any

import redis


COMMANDS_CHANNEL = "esp32:temperature:commands"
RESULTS_CHANNEL = "esp32:temperature:rpc_results"


def _load_params(args: argparse.Namespace) -> dict[str, Any]:
    if args.params_json and args.params_file:
        raise SystemExit("Use either --params-json or --params-file, not both")
    if args.params_file:
        with open(args.params_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    elif args.params_json:
        data = json.loads(args.params_json)
    else:
        data = {}
    if not isinstance(data, dict):
        raise SystemExit("Params must decode to a JSON object")
    return data


def send_rpc(args: argparse.Namespace) -> int:
    request_id = args.request_id or str(uuid.uuid4())
    payload = {
        "type": "rpc_request",
        "request_id": request_id,
        "device_id": args.device_id,
        "action": args.action,
        "params": _load_params(args),
    }

    client = redis.Redis.from_url(args.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    pubsub.subscribe(RESULTS_CHANNEL)

    print(f"Publishing {args.action} to {args.device_id} request_id={request_id}")
    client.publish(COMMANDS_CHANNEL, json.dumps(payload, separators=(",", ":")))

    deadline = time.monotonic() + args.timeout
    for message in pubsub.listen():
        if time.monotonic() >= deadline:
            print("Timed out waiting for RPC response", file=sys.stderr)
            return 1
        if message.get("type") != "message":
            continue
        try:
            result = json.loads(message.get("data") or "{}")
        except json.JSONDecodeError:
            continue
        if result.get("request_id") != request_id:
            continue
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 2
    return 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--redis-url",
        default=os.getenv("REDIS_URL", "redis://localhost:6379"),
        help="Redis URL used by esp32_ws [default: %(default)s]",
    )
    parser.add_argument("--device-id", required=True, help="Target temperature ESP device_id")
    parser.add_argument("--request-id", default=None, help="Optional explicit RPC request id")
    parser.add_argument("--timeout", type=float, default=10.0, help="Response timeout in seconds")
    parser.add_argument("--params-json", help="Inline JSON object for RPC params")
    parser.add_argument("--params-file", help="Path to JSON object file for RPC params")
    parser.add_argument(
        "action",
        choices=[
            "get_status",
            "read_now",
            "get_config",
            "update_config",
            "save_config",
            "discard_config",
            "factory_reset",
            "task_control",
            "get_metrics",
            "get_logs",
            "clear_logs",
            "restart_esp",
        ],
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return send_rpc(parse_args(argv or sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
