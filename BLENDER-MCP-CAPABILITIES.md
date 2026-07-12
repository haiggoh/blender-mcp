# Blender MCP — Capabilities (evidence-based)

> This manual records what the Blender MCP (`ahujasid/blender-mcp`, driven via `uvx blender-mcp`)
> can actually do **on this machine**, verified by functional assertions — not by "the call
> returned without error." Regenerate it from a probe run (see **Update route**).

## Verification status

- **Date verified:** 2026-07-12 (functional capability probe; smoke test first confirmed 2026-07-10).
- **Result:** **9 WORKS, 1 FIXED-PENDING-RELOAD** (see `capability-report.json`).
- **Blender:** 5.1.2 (only version installed; config tree `~/Library/Application Support/Blender/5.1/`).
- **Add-on:** "Interface: Blender MCP" — installed via *Install from Disk* (the running add-on is
  a COPY at `~/Library/Application Support/Blender/5.1/scripts/addons/addon.py`, distinct from the
  source clone). Socket listens on `127.0.0.1:9876` after clicking **Connect to Claude**.
- **MCP server:** `uvx blender-mcp` (stdio), the exact command in the user-scope `mcpServers.blender`
  entry Claude uses. A freshly-spawned probe server connects to `:9876` fine alongside Claude's own.
- **Network context:** PolyHaven downloads require connectivity; a probe run offline marks those
  INCONCLUSIVE (not BROKEN).
- **Optional integrations:** PolyHaven is toggled ON; Sketchfab / Hyper3D / Hunyuan3D are OFF
  (need API keys) and are out of scope this round — their `get_*_status` calls correctly report OFF.

## Two facts every caller needs (discovered by the probe)

1. **Every tool requires a `user_prompt` argument.** The `uvx blender-mcp` server declares
   `user_prompt` on each tool (required on e.g. `get_scene_info`); a call with `arguments: {}`
   fails pydantic validation (`user_prompt: Field required`). The probe injects it on every call.
2. **`get_viewport_screenshot` returns the image inline.** The MCP tool takes only `max_size`
   (+ `user_prompt`) and returns image content in the response (the server manages a temp file
   internally); it does not accept a `filepath`. Verify it by inspecting the returned image bytes.

## Status legend

`WORKS` (functional assertion passed) · `PARTIAL` (call ok but expected change absent) ·
`BROKEN` (call failed) · `INCONCLUSIVE` (the verification read itself was unavailable) ·
**`FIXED-PENDING-RELOAD`** (the fix is present in `addon.py` on disk, but the *running* add-on
still executes the old code because Blender has not reloaded the add-on this session).

## Headline viability

**Is Blender-MCP viable for driving Blender from Claude right now? Yes** — scene inspection,
arbitrary scripted scene mutation, screenshots, and PolyHaven material download all pass
functional checks. The one historical defect — `set_texture` on Blender 4.0+ — is fixed by a
version-guarded patch (Workstream A, PR #280) that is verified correct on 5.1 via direct
node-logic execution; the live `set_texture` *MCP tool* activates once the add-on is reloaded.

## Confirmed WORKING (2026-07-12)

Each verified by a functional assertion in `probe-capabilities.py`:

- **`get_scene_info`** — reported `object_count` equals an independently measured
  `len(bpy.context.scene.objects)` (3 == 3).
- **`get_object_info`** — a known `ASSERT_CUBE` reports `type=MESH`; a non-existent name errors
  (proves the lookup is real, not a stub).
- **`execute_blender_code`** — adding a cube increases the live object count by exactly 1 (proves
  both stdout capture and real scene mutation). *(The server prefixes returned output with
  `Code executed successfully:` — strip it to read the printed value.)*
- **`get_viewport_screenshot`** — returns an inline PNG image (~62 KB base64 observed, > 1 KB).
- **`download_polyhaven_asset`** (textures, 1k) — creates `bpy.data.materials['dark_wood']` whose
  node tree contains 7 `TEX_IMAGE` nodes. *(Requires PolyHaven toggle ON + network.)*
- **`get_polyhaven_status`** — reported `enabled` matches the live
  `bpy.context.scene.blendermcp_use_polyhaven` toggle (both True).
- **`get_hyper3d_status` / `get_sketchfab_status` / `get_hunyuan3d_status`** — each reports OFF,
  matching the live scene toggle (all False). (These are gated by API keys.)

## set_texture — FIXED-PENDING-RELOAD

- **Historical symptom (Blender 4.0+, this machine 5.1.2):** `set_texture` raised
  `ShaderNodeSeparateRGB` undefined when applying a PolyHaven texture whose ARM map (Ambient
  Occlusion / Roughness / Metallic) must be split.
- **Root cause:** Blender 4.0 removed `ShaderNodeSeparateRGB` (renamed to `ShaderNodeSeparateColor`,
  introduced in 3.3). The stock `set_texture` created the removed node unconditionally.
- **Fix (Workstream A, PR #280 → `ahujasid/blender-mcp`):** a runtime branch on
  `bpy.app.version >= (4, 0)` uses `ShaderNodeSeparateColor` (input `Color`; outputs
  `Red`/`Green`/`Blue`) on 4.0+ and the original `ShaderNodeSeparateRGB` (input `Image`; outputs
  `R`/`G`/`B`) below 4.0 — additive and backward-compatible. Applied to the source clone AND the
  running add-on copy on disk.
- **Why FIXED-PENDING-RELOAD, not WORKS:** Blender does not hot-reload add-ons, and the running
  add-on cannot be safely reloaded over its own MCP socket (that would drop the `:9876` channel).
  So the *live* `set_texture` tool still executes the old code until the add-on is reloaded. The
  fix itself is verified correct on 5.1 by executing the fixed ARM-split logic directly
  (a `ShaderNodeSeparateColor` node with inputs `['Color']`, outputs `['Red','Green','Blue']`
  wired into the Principled BSDF; EEVEE render confirmed).
- **To activate the live tool:** disable/re-enable "Interface: Blender MCP" (or restart Blender),
  then click **Connect to Claude**. After that the probe's `set_texture` assertion reads WORKS.
- **Workaround without reloading:** `download_polyhaven_asset` already builds a valid material —
  assign it directly (`obj.data.materials.append(bpy.data.materials[<asset_id>])` via
  `execute_blender_code`) instead of calling `set_texture`. You lose only the ARM re-wiring step.

## Practical guidance

- Prefer `execute_blender_code` for anything not covered by a dedicated tool — it is the most
  reliable surface and round-trips real scene state. Remember the `Code executed successfully:` prefix.
- For PolyHaven textures on Blender 4.0+: reload the add-on to get the patched `set_texture`, or
  use the direct-assign workaround above.
- Pass `user_prompt` on every call.
- The optional integrations (Sketchfab/Hyper3D/Hunyuan3D) are gated by scene toggles AND API keys;
  a `get_*_status` reporting OFF usually means the key/toggle is unset, not a bug.

## Required setup

1. `blender` MCP server **enabled** in Claude (`/mcp`; not in the project's `disabledMcpServers`).
2. **Blender running.**
3. **Add-on installed + enabled** ("Interface: Blender MCP" — *Install from Disk* the add-on, then
   enable it in Preferences → Add-ons).
4. **Connect to Claude** clicked (N-panel → BlenderMCP tab) so the socket listens on
   `127.0.0.1:9876`.
5. *(Optional)* PolyHaven toggle ON for texture download; Sketchfab/Hyper3D/Hunyuan3D need keys.

See `interactive-guide.md` for the ordered, re-checked walkthrough.

## Update route

This document is a snapshot; the machine-readable source of truth is **`capability-report.json`**.
To refresh:

1. Ensure the prereqs above are green (run `interactive-guide.md` if unsure).
2. `./test-blender.sh --capabilities` — runs `probe-capabilities.py`, streams the verdict table,
   and (re)writes `capability-report.json` (`{blender_version, results:[{command,status,detail}], summary}`).
3. Edit the WORKING / set_texture sections above from the fresh report; bump the **Date verified**
   and **Blender** lines under *Verification status*.
