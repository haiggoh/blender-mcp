import probe_helpers as h


def test_unwrap_success_with_status_success():
    msg = {"result": {"content": [{"type": "text", "text": '{"status": "SUCCESS", "n": 3}'}]}}
    ok, payload, raw = h.unwrap_tool_result(msg)
    assert ok is True
    assert payload == {"status": "SUCCESS", "n": 3}
    assert '"status": "SUCCESS"' in raw


def test_unwrap_plain_text_no_payload_is_ok():
    msg = {"result": {"content": [{"type": "text", "text": "scene has 4 objects"}]}}
    ok, payload, raw = h.unwrap_tool_result(msg)
    assert ok is True
    assert payload is None
    assert raw == "scene has 4 objects"


def test_unwrap_transport_error():
    msg = {"error": {"code": -32000, "message": "boom"}}
    ok, payload, raw = h.unwrap_tool_result(msg)
    assert ok is False
    assert payload is None
    assert "boom" in raw


def test_unwrap_iserror_flag():
    msg = {"result": {"isError": True, "content": [{"type": "text", "text": "nope"}]}}
    ok, _, _ = h.unwrap_tool_result(msg)
    assert ok is False


def test_unwrap_text_starting_with_error():
    msg = {"result": {"content": [{"type": "text", "text": "Error: object not found"}]}}
    ok, _, _ = h.unwrap_tool_result(msg)
    assert ok is False


def test_unwrap_payload_status_error_is_failure():
    msg = {"result": {"content": [{"type": "text", "text": '{"status": "error", "message": "x"}'}]}}
    ok, payload, _ = h.unwrap_tool_result(msg)
    assert ok is False
    assert payload == {"status": "error", "message": "x"}


def test_classify_works():
    assert h.classify(ok=True, changed=True, verify_available=True) == "WORKS"


def test_classify_partial_when_ok_but_no_change():
    assert h.classify(ok=True, changed=False, verify_available=True) == "PARTIAL"


def test_classify_broken_when_not_ok():
    assert h.classify(ok=False, changed=False, verify_available=True) == "BROKEN"


def test_classify_inconclusive_when_verify_unavailable():
    assert h.classify(ok=True, changed=False, verify_available=False) == "INCONCLUSIVE"
    assert h.classify(ok=True, changed=True, verify_available=False) == "INCONCLUSIVE"


def test_parse_blender_versions_sorted_numeric_only():
    assert h.parse_blender_versions(["5.1", "4.0", "3.6", "config", ".DS_Store"]) == ["3.6", "4.0", "5.1"]


def test_parse_blender_versions_empty():
    assert h.parse_blender_versions(["config", "randomdir"]) == []


def test_build_summary_counts_by_status():
    results = [
        {"command": "a", "status": "WORKS", "detail": ""},
        {"command": "b", "status": "WORKS", "detail": ""},
        {"command": "c", "status": "BROKEN", "detail": ""},
        {"command": "d", "status": "INCONCLUSIVE", "detail": ""},
    ]
    assert h.build_summary(results) == {"WORKS": 2, "BROKEN": 1, "INCONCLUSIVE": 1}
