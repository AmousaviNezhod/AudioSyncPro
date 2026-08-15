from __future__ import annotations

import json
import sys
import time
import traceback
from typing import Any, Dict

from .engine import process_sync_request


def send_response(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def run_server() -> int:
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                send_response({"success": False, "error": f"Invalid JSON: {e}"})
                continue
            if not isinstance(request, dict):
                send_response({"success": False, "error": "Request must be a JSON object"})
                continue
            action = request.get("action", "sync")
            try:
                if action == "sync":
                    result = process_sync_request(request)
                elif action == "ping":
                    result = {"success": True, "pong": True, "time": time.time()}
                elif action == "normalize":
                    # Normalization is now integrated into sync; return no-op.
                    result = {"success": True, "operations": []}
                else:
                    result = {"success": False, "error": f"Unknown action: {action}"}
                send_response(result)
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                send_response({"success": False, "error": str(e), "traceback": traceback.format_exc()[:500]})
        except KeyboardInterrupt:
            break
    return 0
