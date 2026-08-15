#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audio Sync Pro — Python bridge for Adobe CEP panel.

Modes:
  CLI:
    python sync_bridge.py <request.json> <response.json>

  Server (stdio, one JSON object per line):
    python sync_bridge.py --server
"""

import json
import os
import sys
import time
import traceback

# Ensure local package import works when running as a script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audiosyncpro import __version__  # noqa: E402
from audiosyncpro.engine import process_sync_request, process_normalize_request  # noqa: E402
from audiosyncpro.server import run_server  # noqa: E402


def _write_json(path: str, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _load_request(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    if "--server" in sys.argv or "-s" in sys.argv:
        return run_server()

    if len(sys.argv) >= 3:
        request_path = sys.argv[1]
        response_path = sys.argv[2]
        try:
            request = _load_request(request_path)
        except Exception as e:
            _write_json(response_path, {"success": False, "error": f"Failed to read request: {e}"})
            return 1
        try:
            op = request.get("op", request.get("action", "sync"))
            if op == "normalize":
                result = process_normalize_request(request)
            else:
                result = process_sync_request(request)
            _write_json(response_path, result)
            return 0
        except Exception as e:
            traceback.print_exc()
            _write_json(response_path, {"success": False, "error": str(e), "traceback": traceback.format_exc()[:1000]})
            return 1

    # Default server mode.
    return run_server()


if __name__ == "__main__":
    sys.exit(main())
