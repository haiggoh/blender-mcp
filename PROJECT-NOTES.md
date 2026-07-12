# Blender MCP — project notes (running record + plan)

Home: `~/mcp-servers/blender-mcp/` (clone of `ahujasid/blender-mcp`, produced by Workstream A).
This is the first worked instance of the reusable **mcp-smoke-test** method
(`~/ClaudeWorkspace/mcp-smoke-test/`), adapted from the Adobe Photoshop MCP artifact shape.

## Branch layout (important)

- **`fix/set-texture-bl4-separate-color`** — the upstreamable fix ONLY (Workstream A): the
  version-guarded `set_texture` + a static test. This is the branch behind **PR #280** to
  `ahujasid/blender-mcp`. Keep it clean — no tooling here.
- **`tooling/mcp-smoke-test`** — the Workstream B artifacts below (novel additions). Kept off the
  PR branch on purpose so the focused fix PR stays minimal.

## What this repo contains (Workstream B artifacts, on `tooling/mcp-smoke-test`)

| File | Role |
|---|---|
| `test-blender.sh` | Launcher: `smoke` (default, read-only `get_scene_info`) vs `--capabilities` (full probe). Prints installed Blender version(s); EXIT-trap kills any spawned server. |
| `probe-capabilities.py` | Functional probe: spawns `uvx blender-mcp`, JSON-RPC over stdio, per-tool before/after assertions, WORKS/PARTIAL/BROKEN/INCONCLUSIVE/FIXED-PENDING-RELOAD, fail-fast on stall, artifact cleanup, writes `capability-report.json`. |
| `probe_helpers.py` | Pure helpers (unwrap/classify/version-parse/summary), unit-tested. |
| `test_probe_helpers.py`, `test_launcher_versiondetect.py` | Unit tests (run with `pytest` — see note below). |
| `capability-report.json` | Machine-readable probe output (source of truth for the manual). |
| `BLENDER-MCP-CAPABILITIES.md` | Evidence-based manual, seeded from the 2026-07-12 probe. |
| `interactive-guide.md` | Ordered prereq ladder (DETECT/GUIDE, loop-until-green). |

## Latest result (2026-07-12 probe)

`9 WORKS, 1 FIXED-PENDING-RELOAD` on Blender 5.1.2. WORKS: `get_scene_info`, `execute_blender_code`,
`get_object_info`, `get_viewport_screenshot`, `download_polyhaven_asset`, `get_polyhaven_status`,
`get_hyper3d_status`, `get_sketchfab_status`, `get_hunyuan3d_status`. `set_texture` =
FIXED-PENDING-RELOAD (fix on disk + verified via node-logic; awaiting an add-on reload to go live).

## Facts the probe discovered (not in the original research)

- **Every tool requires a `user_prompt` argument** (server-side pydantic); a bare `{}` errors.
- **`get_viewport_screenshot` returns an inline image**, not a filepath (the MCP server manages the
  temp file). Verify by image content, not file existence.
- **`execute_blender_code` output is prefixed** with `Code executed successfully:` — strip it.
- **`get_object_info`'s MCP param is `object_name`** (not `name`).
- A freshly-spawned probe `uvx blender-mcp` **coexists** with Claude's own server on `:9876`.

## Diff surface (for publishing)

Two distinct categories — keep them separate when deciding what goes upstream:

- **Upstreamable bug fix (Workstream A, PR #280):** the version-guarded `set_texture` fix in
  `addon.py` (`ShaderNodeSeparateRGB` → branch to `ShaderNodeSeparateColor` on Blender 4.0+).
  Additive/backward-compatible. Workstream B only *verifies* it (probe) and *documents* it (manual).
- **Novel additions (Workstream B, this branch's new files):** launcher, probe, helpers, manual,
  interactive guide, report. Candidates to generalize into the `mcp-smoke-test` skill (Workstream C),
  NOT to push to the add-on upstream.

## Two-tier testing framing

- **Unit tier = the probe's per-command functional checks** (deterministic given a connected Blender):
  each tool has a before/after assertion that can actually FAIL (object-count delta, node presence,
  image bytes, toggle consistency) — never "no exception."
  - Genuinely-pure logic (result unwrapping, verdict classification, version parsing, summary) is
    covered by `test_probe_helpers.py` + `test_launcher_versiondetect.py` (14 tests).
- **Integration / e2e tier = a small real-scene goal** exercising the toolset together:
  textured plane + a primitive + a PolyHaven material + a screenshot. Run via
  `./test-blender.sh --capabilities` (which walks most of it).

## Reliability rules baked into the probe (hard-won)

1. Drain server stderr to a FILE (`/tmp/blender-probe-mcp-stderr.log`), never an unread PIPE — an
   unread PIPE deadlocks after ~64KB (~a dozen commands). Load-bearing.
2. One MCP call at a time; small pace between calls.
3. Retry a no-response once; a 2nd consecutive timeout → `_abort_stall` writes a partial report and
   hard-exits. Never stamp downstream commands BROKEN off one stall.
4. `try/finally` terminates the subprocess; launcher installs an EXIT trap for anything it spawned.
5. Clean up scene artifacts the probe creates — objects (`ASSERT_CUBE`, `ASSERT_PLANE`), the
   downloaded material, `set_texture`'s `<id>_material_<obj>` material, and orphaned images.

## Testing note

`python3` here is Homebrew python@3.14 (PEP 668 externally-managed). Run unit tests with the
pipx-installed **`pytest`** command (`cd ~/mcp-servers/blender-mcp && pytest -q`), NOT
`python3 -m pytest`.

## Status / next steps

- [x] Workstream A: `set_texture` version-guard fix applied to all 3 addon.py copies; verified live
  on 5.1.2 via direct node-logic execution + EEVEE render; **PR #280 open** to `ahujasid/blender-mcp`.
- [x] Workstream B artifacts authored and live-verified against Blender 5.1.2 (9 WORKS, 1 FPR).
- [ ] To flip `set_texture` to WORKS live: reload the "Interface: Blender MCP" add-on (or restart
  Blender) + re-Connect, then re-run `./test-blender.sh --capabilities`.
- [ ] Workstream C: extract templates from these proven artifacts into the `mcp-smoke-test` skill
  (Blender is the reference instance; generalize from what worked, not in the abstract).

## Caveats

- Only Blender 5.1 is installed; the pre-4.0 branch of the `set_texture` fix is reasoned/reviewed
  but not locally exercised — stated in the PR.
- PolyHaven download tests hit the network → INCONCLUSIVE (not BROKEN) when offline.
- The probe must spawn the SAME server Claude uses (`uvx blender-mcp`, `env: {}` + PATH); do not
  substitute a different build or the verdicts won't reflect Claude's actual path.
