import os
import subprocess
import probe_helpers as h

REPO = "/Users/bra0002h/mcp-servers/blender-mcp"
BLENDER_CFG = "/Users/bra0002h/Library/Application Support/Blender"


def _installed_versions():
    if not os.path.isdir(BLENDER_CFG):
        return []
    return h.parse_blender_versions(os.listdir(BLENDER_CFG))


def test_launcher_reports_installed_blender_versions():
    versions = _installed_versions()
    # Run the launcher's version-print path only (BLMCP_PRINT_VERSIONS_ONLY short-circuits).
    out = subprocess.run(
        ["bash", f"{REPO}/test-blender.sh"],
        env={**os.environ, "BLMCP_PRINT_VERSIONS_ONLY": "1"},
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, out.stderr
    for v in versions:
        assert v in out.stdout, f"expected version {v} in launcher output:\n{out.stdout}"
    # 5.1 is the known-installed version per the 2026-07-10 facts.
    assert "5.1" in out.stdout
