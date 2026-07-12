#!/usr/bin/env python3
"""Blender MCP functional capability probe.

Spawns the SAME server Claude uses (`uvx blender-mcp`, stdio JSON-RPC), which relays to the
in-Blender add-on socket on 127.0.0.1:9876. For each tool it captures observable scene state
BEFORE and AFTER the call and asserts the expected change actually happened — classifying
WORKS / PARTIAL / BROKEN / INCONCLUSIVE by FUNCTIONAL checks, never by "no error" (return
status lies for broken commands). Writes capability-report.json.

Reliability rules (hard-won):
  - stderr is drained to a FILE, never an unread PIPE (see comment at _stderr_sink).
  - one call at a time; each call paces to avoid racing the per-call socket.
  - retry-once then fail-fast on a 2nd consecutive timeout (real stall != capability result).
  - try/finally terminates the subprocess; scene artifacts the probe creates are cleaned up.
  - every tools/call carries a user_prompt argument (the blender-mcp server requires it).
"""
import json
import os
import subprocess
import threading
import time

import probe_helpers as h

HOME = os.path.expanduser("~")
REPO = f"{HOME}/mcp-servers/blender-mcp"
REPORT = f"{REPO}/capability-report.json"
STDERR_LOG = "/tmp/blender-probe-mcp-stderr.log"
PACE = 0.15  # small gap between calls; the add-on socket is per-call/relayed

results = []

# --- spawn the MCP server -----------------------------------------------------
# IMPORTANT: drain stderr to a FILE, not an unread PIPE. blender-mcp (like the Adobe
# server) logs verbosely to stderr; with stderr=PIPE that we never read, the OS pipe
# buffer (~64KB) fills after ~a dozen commands, the server blocks on its next stderr
# write, and stops responding — a classic unread-pipe deadlock that manufactured a false
# "stalls after ~13 commands". A file sink has no buffer limit, so the writer never blocks,
# and the log stays inspectable afterward.
_stderr_sink = open(STDERR_LOG, "w")
# env is the empty-relative equivalent of the Claude config ("env": {}); we only pass PATH
# so `uvx` resolves. Nothing else is inherited.
_env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:" + HOME + "/.local/bin:/usr/bin:/bin"}
proc = subprocess.Popen(
    ["uvx", "blender-mcp"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=_stderr_sink,
    text=True, bufsize=1, env=_env,
)

# --- stdout reader thread: file responses by id under a lock ------------------
_resp = {}
_lock = threading.Lock()


def _reader():
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        if isinstance(m, dict) and m.get("id") is not None:
            with _lock:
                _resp[m["id"]] = m


threading.Thread(target=_reader, daemon=True).start()


def _send(obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


# --- fail-fast stall handler --------------------------------------------------
def _abort_stall(tool):
    """A 2nd consecutive no-response is a real stall, NOT a capability verdict. Print a loud
    diagnosis, write a PARTIAL report marked aborted, terminate the server, hard-exit. Flush
    stdout first because os._exit skips buffer flush (the printed table would be lost)."""
    print(f"\n!! STALLED on '{tool}' — no response within timeout. NOT a capability result.")
    print("   Likely: Blender not running, add-on not enabled, 'Connect to Claude' not clicked")
    print("   (no listener on 127.0.0.1:9876), an open modal in Blender, or a zombie MCP server.")
    print("   Remaining commands intentionally NOT run (a stall falsifies every downstream verdict).")
    results.append({"command": tool, "status": "STALL-ABORT",
                    "detail": "aborted here (blender down / add-on disconnected / modal / zombie)"})
    with open(REPORT, "w") as f:
        json.dump({"blender_version": os.environ.get("BL_VERSION", "unknown"),
                   "aborted": True, "aborted_on": tool, "results": results}, f, indent=2)
    try:
        proc.terminate()
    except Exception:
        pass
    import sys as _sys
    _sys.stdout.flush()
    os._exit(2)


# --- one JSON-RPC tools/call, retry-once, fail-fast on 2nd stall --------------
_id = [100]


def call(tool, args=None, timeout=30):
    """Issue exactly one tools/call and return (ok, payload, raw). One call at a time.

    Every call carries a `user_prompt` argument — the blender-mcp server declares it
    required (e.g. get_scene_info) and accepts it on every other tool, so injecting it
    universally is safe and avoids a pydantic 'Field required' validation error."""
    time.sleep(PACE)
    args = dict(args or {})
    args.setdefault("user_prompt", f"capability probe: {tool}")
    for attempt in (1, 2):
        _id[0] += 1
        i = _id[0]
        _send({"jsonrpc": "2.0", "id": i, "method": "tools/call",
               "params": {"name": tool, "arguments": args}})
        m = None
        t0 = time.time()
        while time.time() - t0 < timeout:
            with _lock:
                if i in _resp:
                    m = _resp.pop(i)
                    break
            time.sleep(0.08)
        if m is not None:
            break
        if attempt == 1:
            time.sleep(0.6)  # transport hiccup — one retry (the hung call never executed)
            continue
        _abort_stall(tool)  # 2nd consecutive timeout == real stall
        return (False, None, "TIMEOUT")  # unreachable
    return h.unwrap_tool_result(m)


def bl(code, timeout=30):
    """Run Python in Blender via execute_blender_code; return captured stdout text or None.

    The add-on captures print() output and returns it; we extract that text so the probe can
    read back independently-measured scene facts (object counts, types, etc.)."""
    ok, payload, raw = call("execute_blender_code", {"code": code}, timeout=timeout)
    if not ok:
        return None
    if isinstance(payload, dict):
        for k in ("result", "output", "stdout"):
            if k in payload and isinstance(payload[k], str):
                return payload[k]
        return json.dumps(payload)
    return raw


def _handshake():
    _send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
           "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                      "clientInfo": {"name": "probe", "version": "1"}}})
    t0 = time.time()
    while time.time() - t0 < 15:
        with _lock:
            if 1 in _resp:
                _resp.pop(1)
                break
        time.sleep(0.08)
    else:
        _abort_stall("initialize")
    time.sleep(0.5)
    _send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    time.sleep(0.5)


if __name__ == "__main__":
    # TEMPORARY harness check (replaced by the full probe in Task 4): handshake + one read.
    try:
        _handshake()
        ok, payload, raw = call("get_scene_info", {}, timeout=20)
        print("HARNESS OK" if ok else "HARNESS CALL FAILED")
        print("scene:", (raw or "")[:300])
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
