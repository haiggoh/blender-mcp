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

    The add-on captures print() output and returns it; the MCP server prefixes that with
    'Code executed successfully:' — we strip the banner so callers get just the printed text."""
    ok, payload, raw = call("execute_blender_code", {"code": code}, timeout=timeout)
    if not ok:
        return None
    text = raw
    if isinstance(payload, dict):
        for k in ("result", "output", "stdout"):
            if k in payload and isinstance(payload[k], str):
                text = payload[k]
                break
        else:
            text = json.dumps(payload)
    banner = "Code executed successfully:"
    if text and text.startswith(banner):
        text = text[len(banner):].lstrip()
    return text


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


def call_full(tool, args=None, timeout=30):
    """Like call() but returns the full JSON-RPC message dict (needed to inspect image
    content for get_viewport_screenshot). Same user_prompt injection + retry/fail-fast."""
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
            return m
        if attempt == 1:
            time.sleep(0.6)
            continue
        _abort_stall(tool)
    return None


def _reported_enabled(raw):
    """Best-effort read of an integration-status message's enabled/disabled sense."""
    t = (raw or "").lower()
    if any(neg in t for neg in ("not enabled", "disabled", "not available", "is not")):
        return False
    return any(pos in t for pos in ("enabled", "ready", "true"))


# --- recorder + scene-state read helpers -------------------------------------
def rec(command, status, detail):
    results.append({"command": command, "status": status, "detail": detail})
    print(f"  [{status:18}] {command:26} {detail}")


def obj_count():
    """Independently-measured live object count in the scene, or -1 if the read failed."""
    out = bl("import bpy; print(len(bpy.context.scene.objects))")
    if out is None:
        return -1
    try:
        return int(out.strip().splitlines()[-1])
    except Exception:
        return -1


def obj_exists(name):
    out = bl(f"import bpy; print('YES' if bpy.data.objects.get({name!r}) else 'NO')")
    return (out or "").strip().endswith("YES")


def mat_exists(name):
    out = bl(f"import bpy; print('YES' if bpy.data.materials.get({name!r}) else 'NO')")
    return (out or "").strip().endswith("YES")


def cleanup(obj_names, mat_names):
    """Delete objects/materials the probe created; restore toward the pre-probe set."""
    code = (
        "import bpy\n"
        f"for n in {obj_names!r}:\n"
        "    o = bpy.data.objects.get(n)\n"
        "    if o: bpy.data.objects.remove(o, do_unlink=True)\n"
        f"for m in {mat_names!r}:\n"
        "    mm = bpy.data.materials.get(m)\n"
        "    if mm:\n"
        "        mm.use_fake_user = False\n"
        "        bpy.data.materials.remove(mm)\n"
        "print('CLEANED')\n"
    )
    out = bl(code)
    print("  cleanup:", (out or "no-response").strip().splitlines()[-1] if out else "no-response")


def run_probe():
    created_objs = []
    created_mats = []

    # 1) get_scene_info — reported count must equal an independently-measured count.
    ok, payload, raw = call("get_scene_info", {}, timeout=20)
    measured = obj_count()
    reported = None
    if isinstance(payload, dict):
        r = payload.get("result", payload)
        if isinstance(r, dict):
            reported = r.get("object_count")
    verify_ok = measured != -1 and reported is not None
    changed = verify_ok and int(reported) == measured
    rec("get_scene_info",
        h.classify(ok, changed, verify_ok),
        f"reported object_count={reported} vs measured={measured}")

    # 2) execute_blender_code — add a cube, object count must go up by exactly 1.
    c0 = obj_count()
    ok, _, raw = call("execute_blender_code",
                      {"code": "import bpy; bpy.ops.mesh.primitive_cube_add(); "
                               "bpy.context.active_object.name='ASSERT_CUBE'; print('ADDED')"},
                      timeout=30)
    if obj_exists("ASSERT_CUBE"):
        created_objs.append("ASSERT_CUBE")
    c1 = obj_count()
    verify_ok = c0 != -1 and c1 != -1
    changed = verify_ok and c1 == c0 + 1
    rec("execute_blender_code",
        h.classify(ok, changed, verify_ok),
        f"object count {c0}->{c1} (expected +1); named ASSERT_CUBE")

    # 3) get_object_info — known cube must report MESH; a bad name must error.
    ok, payload, raw = call("get_object_info", {"object_name": "ASSERT_CUBE"}, timeout=20)
    info = payload.get("result", payload) if isinstance(payload, dict) else {}
    otype = info.get("type") if isinstance(info, dict) else None
    is_cube = otype == "MESH"
    neg_ok, _, _ = call("get_object_info", {"object_name": "__does_not_exist__"}, timeout=20)
    verify_ok = otype is not None
    changed = is_cube and (neg_ok is False)
    rec("get_object_info",
        h.classify(ok, changed, verify_ok),
        f"ASSERT_CUBE type={otype} (want MESH); missing-name errored={neg_ok is False}")

    # 4) get_viewport_screenshot — the MCP server returns an inline image; assert image content.
    m = call_full("get_viewport_screenshot", {"max_size": 400}, timeout=30)
    has_image = False
    data_len = 0
    is_err = False
    if isinstance(m, dict):
        if "error" in m:
            is_err = True
        res = m.get("result", {}) or {}
        for c in (res.get("content") or []):
            if isinstance(c, dict) and c.get("type") == "image":
                has_image = True
                data_len = len(c.get("data") or "")
        if res.get("isError"):
            is_err = True
    verify_ok = m is not None
    changed = has_image and data_len > 1000
    rec("get_viewport_screenshot",
        h.classify((not is_err) and verify_ok, changed, verify_ok),
        f"inline image={has_image} base64_len={data_len} (want image >1KB)")

    # 5) get_polyhaven_status — reported flag must match the live scene toggle.
    ok, payload, raw = call("get_polyhaven_status", {}, timeout=20)
    live = bl("import bpy; print(bool(getattr(bpy.context.scene,'blendermcp_use_polyhaven',False)))")
    live_on = (live or "").strip().endswith("True")
    reported_on = _reported_enabled(raw)
    verify_ok = live is not None
    changed = verify_ok and (reported_on == live_on)
    rec("get_polyhaven_status",
        h.classify(ok, changed, verify_ok),
        f"reported_enabled={reported_on} vs live_toggle={live_on}")

    # 6) download_polyhaven_asset — needs PolyHaven ON + network. Material + a TEX_IMAGE node.
    running_addon = os.path.expanduser(
        "~/Library/Application Support/Blender/5.1/scripts/addons/addon.py")
    try:
        fix_in_running = "ShaderNodeSeparateColor" in open(running_addon).read()
    except Exception:
        fix_in_running = False

    if not live_on:
        rec("download_polyhaven_asset", "SKIPPED",
            "PolyHaven integration is OFF (blendermcp_use_polyhaven=False) — enable to test")
        rec("set_texture", "SKIPPED", "depends on download_polyhaven_asset (PolyHaven OFF)")
    else:
        asset = "dark_wood"
        ok, payload, raw = call("download_polyhaven_asset",
                                {"asset_id": asset, "asset_type": "textures", "resolution": "1k"},
                                timeout=90)
        made_mat = mat_exists(asset)
        node_out = bl(
            "import bpy\n"
            f"m = bpy.data.materials.get({asset!r})\n"
            "n = 0\n"
            "if m and m.use_nodes:\n"
            "    n = sum(1 for nd in m.node_tree.nodes if nd.type=='TEX_IMAGE')\n"
            "print(n)\n"
        )
        try:
            tex_nodes = int((node_out or "0").strip().splitlines()[-1])
        except Exception:
            tex_nodes = -1
        if made_mat:
            created_mats.append(asset)
        offline = (not ok) and any(s in (raw or "").lower()
                                   for s in ("network", "resolve", "timed out", "connection"))
        if offline:
            rec("download_polyhaven_asset", "INCONCLUSIVE",
                "network unreachable (PolyHaven download needs connectivity) — re-run online")
            rec("set_texture", "INCONCLUSIVE", "cannot test: material download inconclusive (offline)")
        else:
            verify_ok = tex_nodes != -1
            changed = made_mat and tex_nodes > 0
            rec("download_polyhaven_asset",
                h.classify(ok, changed, verify_ok),
                f"material '{asset}' created={made_mat}, TEX_IMAGE nodes={tex_nodes}")

            # 7) set_texture — honest about the reload boundary (Workstream A fix).
            bl("import bpy; bpy.ops.mesh.primitive_plane_add(); "
               "bpy.context.active_object.name='ASSERT_PLANE'; print('PLANE')")
            if obj_exists("ASSERT_PLANE"):
                created_objs.append("ASSERT_PLANE")
            ok_st, payload_st, raw_st = call("set_texture",
                                             {"object_name": "ASSERT_PLANE", "texture_id": asset},
                                             timeout=45)
            sep_out = bl(
                "import bpy\n"
                "o = bpy.data.objects.get('ASSERT_PLANE')\n"
                "found = False; assigned = False\n"
                "if o and o.data.materials:\n"
                "    assigned = len(o.data.materials) > 0\n"
                "    for m in o.data.materials:\n"
                "        if m and m.use_nodes:\n"
                "            for nd in m.node_tree.nodes:\n"
                "                if nd.type in ('SEPARATE_COLOR','SEPRGB','SEPARATE_RGB'):\n"
                "                    found = True\n"
                "print(f'{assigned}|{found}')\n"
            )
            parts = (sep_out or "False|False").strip().splitlines()[-1].split("|")
            assigned = parts[0] == "True"
            has_sep = len(parts) > 1 and parts[1] == "True"
            sepRGB_err = "separatergb" in (raw_st or "").lower()
            if ok_st and assigned and has_sep and not sepRGB_err:
                rec("set_texture", "WORKS",
                    "material assigned + ARM-split node present, no ShaderNodeSeparateRGB error (fix live)")
            elif sepRGB_err and fix_in_running:
                rec("set_texture", "FIXED-PENDING-RELOAD",
                    "running add-on still executes old code (ShaderNodeSeparateRGB error), but the "
                    "fix IS present in addon.py on disk — reload the add-on to activate. Fix itself "
                    "verified separately via direct node-logic execution (see PR #280).")
            elif sepRGB_err and not fix_in_running:
                rec("set_texture", "BROKEN",
                    "ShaderNodeSeparateRGB undefined on Blender 4.0+ and the fix is NOT in addon.py")
            else:
                rec("set_texture",
                    h.classify(ok_st, assigned and has_sep, sep_out is not None),
                    f"assigned={assigned}, ARM-split node present={has_sep}, sepRGB_err={sepRGB_err}")

    # 8) the remaining status calls — toggle/consistency (no writes).
    for tool, toggle in (("get_hyper3d_status", "blendermcp_use_hyper3d"),
                         ("get_sketchfab_status", "blendermcp_use_sketchfab"),
                         ("get_hunyuan3d_status", "blendermcp_use_hunyuan3d")):
        ok, payload, raw = call(tool, {}, timeout=20)
        live = bl(f"import bpy; print(bool(getattr(bpy.context.scene,'{toggle}',False)))")
        live_on = (live or "").strip().endswith("True")
        reported_on = _reported_enabled(raw)
        verify_ok = live is not None
        changed = verify_ok and (reported_on == live_on)
        rec(tool, h.classify(ok, changed, verify_ok),
            f"reported_enabled={reported_on} vs live_toggle={live_on}")

    return created_objs, created_mats


if __name__ == "__main__":
    print("Blender MCP capability probe — functional checks (WORKS/PARTIAL/BROKEN/INCONCLUSIVE)")
    created_objs, created_mats = [], []
    try:
        _handshake()
        ver = bl("import bpy; print('.'.join(str(x) for x in bpy.app.version))")
        os.environ["BL_VERSION"] = (ver or "unknown").strip().splitlines()[-1] if ver else "unknown"
        created_objs, created_mats = run_probe()
    finally:
        try:
            if created_objs or created_mats:
                cleanup(created_objs, created_mats)
        except Exception as e:
            print("  cleanup error (non-fatal):", e)
        try:
            summary = h.build_summary(results)
            with open(REPORT, "w") as f:
                json.dump({"blender_version": os.environ.get("BL_VERSION", "unknown"),
                           "results": results, "summary": summary}, f, indent=2)
            print("\nSummary:", json.dumps(summary))
            print("Report written:", REPORT)
        except Exception as e:
            print("  report write error:", e)
        try:
            proc.terminate()
        except Exception:
            pass
