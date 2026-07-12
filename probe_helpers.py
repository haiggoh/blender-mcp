"""Pure, unit-testable helpers for the Blender MCP capability probe.

These functions contain NO I/O and NO Blender/network dependency, so they can be
tested deterministically. Everything that needs a live Blender lives in
probe-capabilities.py and is verified live.
"""
import json
import re


def unwrap_tool_result(msg):
    """Unwrap an MCP tools/call JSON-RPC response.

    MCP wraps tool output in result.content[0].text. Returns (ok, payload, raw_text):
      - ok: functional success = no transport error, not isError, text not "error...",
            and (no JSON payload OR payload.status == "SUCCESS").
      - payload: the json.loads of the text if it parsed, else None.
      - raw_text: the first 300 chars of the text (or the error blob).
    """
    if "error" in msg:
        return (False, None, json.dumps(msg["error"])[:300])
    res = msg.get("result", {}) or {}
    content = res.get("content") or []
    text = ""
    if content and isinstance(content[0], dict):
        text = content[0].get("text") or ""
    is_err = bool(res.get("isError")) or text.lower().startswith("error")
    payload = None
    try:
        payload = json.loads(text)
    except Exception:
        payload = None
    status_ok = True
    if isinstance(payload, dict) and "status" in payload:
        status_ok = payload.get("status") == "SUCCESS"
    ok = (not is_err) and status_ok
    return (ok, payload, text[:300])


def classify(ok, changed, verify_available):
    """Functional verdict.

    - INCONCLUSIVE if the verification READ itself was unavailable (can't trust either way).
    - WORKS if ok and the observed state changed as expected.
    - PARTIAL if the call reported success but the expected change did not happen.
    - BROKEN if the call did not report success.
    """
    if not verify_available:
        return "INCONCLUSIVE"
    if not ok:
        return "BROKEN"
    return "WORKS" if changed else "PARTIAL"


def parse_blender_versions(dir_names):
    """From a list of directory names, keep only version-like ones (e.g. '5.1', '4.0')
    and return them sorted by numeric tuple ascending."""
    versions = [d for d in dir_names if re.fullmatch(r"\d+\.\d+", d)]
    versions.sort(key=lambda v: tuple(int(p) for p in v.split(".")))
    return versions


def build_summary(results):
    """Count results by their 'status' key -> {status: count}."""
    summary = {}
    for r in results:
        s = r.get("status", "UNKNOWN")
        summary[s] = summary.get(s, 0) + 1
    return summary
