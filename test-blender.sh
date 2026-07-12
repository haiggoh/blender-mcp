#!/usr/bin/env bash
# Blender MCP smoke test + capability probe launcher.
#   ./test-blender.sh                 # smoke (default): read-only get_scene_info connectivity
#   ./test-blender.sh --capabilities  # full functional probe (probe-capabilities.py)
# Spawns the SAME server Claude uses: `uvx blender-mcp` (stdio JSON-RPC), which relays to the
# in-Blender add-on socket on 127.0.0.1:9876. Blender must be running with the add-on connected.
set -euo pipefail

REPO="/Users/bra0002h/mcp-servers/blender-mcp"
BLENDER_CFG="/Users/bra0002h/Library/Application Support/Blender"
# Resolve node/python/uvx from Homebrew + user-local bins regardless of caller PATH.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin"

MODE="${1:-smoke}"

# --- Blender version detection ------------------------------------------------
echo "Detecting installed Blender version(s) under: $BLENDER_CFG"
FOUND=""
if [ -d "$BLENDER_CFG" ]; then
  for d in "$BLENDER_CFG"/*/; do
    name="$(basename "$d")"
    if printf '%s' "$name" | grep -Eq '^[0-9]+\.[0-9]+$'; then
      echo "  - Blender $name"
      FOUND="$FOUND $name"
    fi
  done
fi
if [ -z "$FOUND" ]; then
  echo "  (no version-tagged Blender config dirs found)"
fi

# Test-only short-circuit: print versions and exit (used by test_launcher_versiondetect.py).
if [ "${BLMCP_PRINT_VERSIONS_ONLY:-0}" = "1" ]; then
  exit 0
fi

# --- capabilities mode: delegate to the full probe ---------------------------
case "$MODE" in
  --capabilities|capabilities)
    echo ""
    echo "Running full capability probe (probe-capabilities.py)..."
    exec python3 -u "$REPO/probe-capabilities.py"
    ;;
esac

# --- smoke mode: read-only get_scene_info connectivity ------------------------
echo ""
echo "Smoke test: spawning 'uvx blender-mcp' and calling get_scene_info (read-only)..."

STDERR_LOG="/tmp/blender-mcp-smoke-stderr.log"
: > "$STDERR_LOG"

# Spawn the server; capture its PID so the EXIT trap can kill ONLY what we started.
python3 - "$STDERR_LOG" <<'PYEOF'
import json, os, subprocess, sys, threading, time

stderr_log = sys.argv[1]
sink = open(stderr_log, "w")
proc = subprocess.Popen(
    ["uvx", "blender-mcp"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sink,
    text=True, bufsize=1, env={"PATH": os.environ["PATH"]},
)

resp = {}
lock = threading.Lock()

def reader():
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        if isinstance(m, dict) and m.get("id") is not None:
            with lock:
                resp[m["id"]] = m

threading.Thread(target=reader, daemon=True).start()

def send(o):
    proc.stdin.write(json.dumps(o) + "\n")
    proc.stdin.flush()

def wait_for(i, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        with lock:
            if i in resp:
                return resp.pop(i)
        time.sleep(0.08)
    return None

rc = 1
try:
    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "smoke", "version": "1"}}})
    if wait_for(1, 15) is None:
        print("SMOKE FAIL: no response to initialize (is `uvx blender-mcp` installed?).")
        raise SystemExit(1)
    time.sleep(0.5)
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    time.sleep(0.5)
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
          "params": {"name": "get_scene_info",
                     "arguments": {"user_prompt": "blender mcp smoke test"}}})
    m = wait_for(2, 20)
    if m is None:
        print("SMOKE FAIL: no response to get_scene_info within 20s.")
        print("  Likely: Blender not running, add-on not enabled, or 'Connect to Claude' not clicked (no listener on 9876).")
        raise SystemExit(1)
    if "error" in m:
        print("SMOKE FAIL: get_scene_info returned an error:")
        print("  " + json.dumps(m["error"])[:400])
        print("  Likely: add-on socket (127.0.0.1:9876) not connected. Click 'Connect to Claude' in the BlenderMCP N-panel.")
        raise SystemExit(1)
    res = m.get("result", {}) or {}
    content = res.get("content") or []
    text = content[0].get("text") if content and isinstance(content[0], dict) else ""
    if (text or "").lower().startswith("error"):
        print("SMOKE FAIL: get_scene_info returned an application error:")
        print("  " + (text or "")[:400])
        print("  (a response arrived, but the tool itself errored — not a real connectivity pass).")
        raise SystemExit(1)
    print("SMOKE PASS: get_scene_info responded.")
    print("  scene: " + (text or "")[:400])
    rc = 0
finally:
    try:
        proc.terminate()
    except Exception:
        pass
sys.exit(rc)
PYEOF
SMOKE_RC=$?

echo ""
echo "(server stderr log: $STDERR_LOG)"
exit "$SMOKE_RC"
