from __future__ import annotations

import json
import sys
import time
import traceback
from typing import Any, Dict

import os

from .engine import process_normalize_request, process_sync_request


def _notify_startup() -> None:
    """Write a startup marker to the process stderr handle.

    This bypasses sys.stderr (which may be a NullWriter in a PyInstaller
    --noconsole build) so CEP's process.stderr callback receives it.
    """
    try:
        os.write(2, b"server started\n")
    except Exception:
        try:
            sys.stderr.write("server started\n")
            sys.stderr.flush()
        except Exception:
            pass


_stdout_broken = False


def _safe_print_exc() -> None:
    """Print traceback to stderr without failing if stderr is closed."""
    try:
        traceback.print_exc(file=sys.stderr)
    except Exception:
        pass


def send_response(obj: dict) -> None:
    global _stdout_broken
    if _stdout_broken:
        return
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        # The panel/host closed the stdio pipe. Stop sending output.
        _stdout_broken = True


def run_server() -> int:
    _notify_startup()
    while True:
        if _stdout_broken:
            break
        try:
            try:
                line = sys.stdin.readline()
            except (BrokenPipeError, OSError):
                break
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
            # UI sends "op"; keep "action" as the canonical key.
            action = request.get("action", request.get("op", "sync"))
            request_id = request.get("request_id")
            try:
                if action == "sync":
                    result = process_sync_request(request)
                elif action == "ping":
                    result = {"success": True, "pong": True, "time": time.time()}
                elif action == "normalize":
                    result = process_normalize_request(request)
                else:
                    result = {"success": False, "error": f"Unknown action: {action}"}
                if request_id is not None:
                    result["request_id"] = request_id
                send_response(result)
            except Exception as e:
                _safe_print_exc()
                send_response({"success": False, "error": str(e), "traceback": traceback.format_exc()[:500]})
        except KeyboardInterrupt:
            break
    return 0
