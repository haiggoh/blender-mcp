# Blender MCP — interactive setup guide (detect → guide → re-check, loop until green)

Purpose: walk from "nothing connected" to "all green" for the Blender MCP, checking **each prereq
in order** and re-checking after every user action. Each rung is either **DETECT** (machine-checkable
from disk/process/socket — the assistant runs the command) or **GUIDE** (an in-app action the user
must take — prompt, wait for confirmation, then re-run the rung's check). Do NOT advance to the next
rung until the current one is green. If a later rung fails, drop back to the lowest failing rung.

The single end-to-end green signal for the whole ladder is:
`./test-blender.sh` printing `SMOKE PASS: get_scene_info responded.`

---

## Rung 1 — blender MCP server ENABLED in Claude  *(DETECT)*

The server can be disabled per-project via `disabledMcpServers` in `~/.claude.json`. Check whether
`"blender"` is listed as disabled for THIS project:

```sh
python3 - <<'PY'
import json, os
cfg = json.load(open(os.path.expanduser("~/.claude.json")))
proj = os.getcwd()
entry = cfg.get("projects", {}).get(proj, {})
disabled = entry.get("disabledMcpServers", [])
print("DISABLED" if "blender" in disabled else "ENABLED", "(project:", proj + ")")
PY
```

- **Green:** prints `ENABLED`.
- **If `DISABLED` → GUIDE:** tell the user to run `/mcp`, select **blender**, and enable it for this
  project. Then re-run the DETECT command above. (Also confirm the user-scope entry exists:
  `python3 -c "import json,os; print('blender' in json.load(open(os.path.expanduser('~/.claude.json'))).get('mcpServers',{}))"` should print `True`.)

## Rung 2 — Blender RUNNING  *(DETECT)*

The macOS binary is named `Blender`:

```sh
pgrep -x Blender && echo "RUNNING" || echo "NOT RUNNING"
```

- **Green:** prints one or more PIDs then `RUNNING`.
- **If `NOT RUNNING` → GUIDE:** ask the user to launch Blender. Wait for confirmation, then re-run.

## Rung 3 — Add-on INSTALLED + ENABLED  *(DETECT presence / GUIDE enablement)*

**3a. Presence (DETECT).** The running add-on is the installed COPY:

```sh
test -f "$HOME/Library/Application Support/Blender/5.1/scripts/addons/addon.py" \
  && echo "ADDON FILE PRESENT" || echo "ADDON FILE MISSING"
```

- **If MISSING → GUIDE:** in Blender, Edit → Preferences → Add-ons → *Install from Disk…* → choose
  `~/ClaudeWorkspace/blender-mcp/addon.py` → then continue to 3b. Re-run 3a after install.

**3b. Enablement (GUIDE — in-app, not detectable from disk).** Ask the user to confirm the add-on
is enabled: Preferences → Add-ons → search "Blender MCP" → checkbox ticked on **Interface: Blender MCP**.
There is no reliable disk signal for the enabled checkbox; enablement is proven by Rung 4 succeeding
(the socket only opens when the add-on is enabled AND Connect is clicked). Prompt, wait for
confirmation, proceed to Rung 4.

## Rung 4 — "Connect to Claude" LISTENING on 9876  *(DETECT)*

```sh
lsof -nP -iTCP:9876 -sTCP:LISTEN && echo "SOCKET LISTENING" || echo "NO LISTENER"
```

- **Green:** a `Blender ... TCP 127.0.0.1:9876 (LISTEN)` line then `SOCKET LISTENING`.
- **If `NO LISTENER` → GUIDE:** in Blender, open the N-panel (press **N** in the 3D viewport) →
  **BlenderMCP** tab → click **Connect to Claude**. Wait for confirmation, then re-run this DETECT.
  (If it still fails after Connect, drop back to Rung 3b — the add-on may not actually be enabled.)

## Rung 5 — (Optional) integrations  *(DETECT status / GUIDE enablement)*

These are not required for core use. Report current state, and only guide enablement if the user
wants a specific one.

**Report (DETECT)** — once Rungs 1–4 are green, the assistant can read the toggles live:

```sh
./test-blender.sh --capabilities 2>/dev/null | grep -E "get_(polyhaven|sketchfab|hyper3d|hunyuan3d)_status"
```

(Or run the probe directly.) Interpretation:
- **PolyHaven** — the only one exercised so far; enable via the BlenderMCP N-panel toggle if the
  user wants texture download. Needed for `download_polyhaven_asset` / `set_texture`.
- **Sketchfab / Hyper3D / Hunyuan3D** — require API keys; **GUIDE** the user to the respective
  N-panel toggle + key field only on request. Out of default scope.

---

## Final green check *(DETECT — the whole-ladder signal)*

```sh
./test-blender.sh
```

Expected: the Blender version line(s), then `SMOKE PASS: get_scene_info responded.` with a `scene:`
summary. If this passes, all required rungs (1–4) are green and the MCP is ready. If it prints
`SMOKE FAIL`, return to the lowest failing rung above (the failure hint names the likely rung) and
loop.

> Note: for the `set_texture` tool specifically, if you applied the Blender-4.0+ fix (PR #280) this
> session, the live tool activates only after an add-on reload (Rung 3b/4) — see the
> `FIXED-PENDING-RELOAD` note in `BLENDER-MCP-CAPABILITIES.md`.
