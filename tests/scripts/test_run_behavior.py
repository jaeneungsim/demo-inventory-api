import json

import pytest

from scripts import run_behavior as rb

CONTRACT_BODY = """## Ticket
INV-123

## Type
feature

## Expected behavior
```yaml expected-behavior
- id: B1
  covers: [R1]
  type: api
  request: GET /items/42
  expect:
    status: 200
    body_has: [stock]
- id: B2
  covers: [R1]
  type: log
  after: B1
  expect:
    no_level: ERROR
- id: B3
  covers: [R2]
  type: ui
  page: /item.html?id=42
  step: The detail page for item 42 shows the stock quantity.
```
"""


def items_from(yaml_text):
    return rb.parse_items(yaml_text)


def test_extract_block_from_contract_example():
    text = rb.extract_block(CONTRACT_BODY)
    assert text.startswith("- id: B1")
    assert text.rstrip().endswith("shows the stock quantity.")


def test_extract_block_handles_crlf_and_longer_fences():
    body = "Intro\r\n````yaml expected-behavior\r\n- id: B1\r\n````\r\nAfter"
    assert rb.extract_block(body) == "- id: B1"


def test_extract_block_ignores_other_code_blocks():
    body = "```yaml\n- id: X\n```\n\n```yaml expected-behavior\n- id: B1\n```\n"
    assert rb.extract_block(body) == "- id: B1"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("", "empty"),
        ("No block here", "no ```yaml expected-behavior block"),
        ("```yaml expected-behavior\n- id: B1\n", "not closed"),
    ],
)
def test_extract_block_errors(body, message):
    with pytest.raises(rb.BehaviorError, match=message):
        rb.extract_block(body)


def test_parse_contract_example():
    items = items_from(rb.extract_block(CONTRACT_BODY))
    assert [item["id"] for item in items] == ["B1", "B2", "B3"]
    assert (items[0]["method"], items[0]["path"]) == ("GET", "/items/42")


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        ("{}", "non-empty YAML list"),
        ("[]", "non-empty YAML list"),
        ("- just text", "must be a mapping"),
        ("- {id: X1, covers: [R1], type: ui, step: s}", "`id` must look like B1"),
        (
            "- {id: B1, covers: [R1], type: ui, step: s}\n- {id: B1, covers: [R1], type: ui, step: t}",
            "used twice",
        ),
        ("- {id: B1, type: ui, step: s}", "`covers`"),
        ("- {id: B1, covers: [Q1], type: ui, step: s}", "`covers`"),
        ("- {id: B1, covers: [R1], type: sql}", "`type` must be api, log or ui"),
        (
            "- {id: B1, covers: [R1], type: api, request: FETCH /items, expect: {status: 200}}",
            "`request` must look like",
        ),
        (
            "- {id: B1, covers: [R1], type: api, request: 'GET http://evil/items', expect: {status: 200}}",
            "must start with /",
        ),
        (
            "- {id: B1, covers: [R1], type: api, request: GET //evil/items, expect: {status: 200}}",
            "must start with /",
        ),
        ("- {id: B1, covers: [R1], type: api, request: GET /items}", "`expect.status`"),
        (
            "- {id: B1, covers: [R1], type: api, request: GET /items, expect: {status: 200, foo: 1}}",
            "unknown expect keys: foo",
        ),
        (
            "- {id: B1, covers: [R1], type: api, request: GET /items, expect: {status: 200, body_has: stock}}",
            "`expect.body_has` must be a list",
        ),
        (
            "- {id: B1, covers: [R1], type: api, request: POST /items, body: [1], expect: {status: 201}}",
            "`body` must be a JSON object",
        ),
        (
            "- {id: B1, covers: [R1], type: api, request: GET /items, headers: {x: 1}, expect: {status: 200}}",
            "`headers` must map strings",
        ),
        (
            "- {id: B1, covers: [R1], type: log, after: B9, expect: {no_level: ERROR}}",
            "`after` must name an api item",
        ),
        (
            "- {id: B1, covers: [R1], type: api, request: GET /items, expect: {status: 200}}\n"
            "- {id: B2, covers: [R1], type: log, after: B1, expect: {}}",
            "need `expect.no_level`",
        ),
        (
            "- {id: B1, covers: [R1], type: api, request: GET /items, expect: {status: 200}}\n"
            "- {id: B2, covers: [R1], type: log, after: B1, expect: {no_level: LOUD}}",
            "log level",
        ),
        ("- {id: B1, covers: [R1], type: ui}", "need a `step`"),
        ("- {id: B1, covers: [R1], type: ui, step: s, page: item.html}", "`page` must be a path"),
        ("- [unclosed", "not valid YAML"),
    ],
)
def test_parse_items_errors(yaml_text, message):
    with pytest.raises(rb.BehaviorError, match=message):
        items_from(yaml_text)


API_ITEM = {
    "id": "B1",
    "type": "api",
    "expect": {"status": 200, "body_has": ["stock", "item.id"], "body_not_has": ["secret"]},
}


def test_check_api_passes():
    ok, detail = rb.check_api(API_ITEM, 200, {"stock": 5, "item": {"id": "42"}})
    assert ok
    assert detail == "status 200, body has stock, item.id, body has no secret"


def test_check_api_reports_every_problem():
    ok, detail = rb.check_api(API_ITEM, 404, {"secret": 1})
    assert not ok
    assert detail == (
        "status 404, expected 200; body has no `stock`; body has no `item.id`; body has `secret`"
    )


def test_check_api_needs_json_for_body_checks():
    ok, detail = rb.check_api(API_ITEM, 200, None)
    assert not ok
    assert "not JSON" in detail


def test_body_includes_is_a_deep_subset_match():
    item = {
        "id": "B1",
        "type": "api",
        "expect": {"status": 200, "body_includes": {"sold_out": True, "item": {"stock": 0}}},
    }
    assert rb.check_api(item, 200, {"sold_out": True, "item": {"stock": 0, "id": "43"}})[0]
    ok, detail = rb.check_api(item, 200, {"sold_out": 1, "item": {"stock": 0}})
    assert not ok
    assert "`sold_out` (got 1)" in detail
    ok, detail = rb.check_api(item, 200, {"sold_out": True, "item": {}})
    assert "`item.stock` (got missing)" in detail


def test_get_path_supports_list_indexes():
    assert rb.get_path({"items": [{"id": "41"}]}, "items.0.id") == (True, "41")
    assert rb.get_path({"items": []}, "items.0.id") == (False, None)


def test_list_includes_needs_the_same_length():
    assert rb.find_mismatch({"a": [1, 2]}, {"a": [1]}) == "a"
    assert rb.find_mismatch({"a": [1, {"b": 2}]}, {"a": [1, {"b": 3}]}) == "a.1.b"


LOG_ITEM = {"id": "B2", "type": "log", "after": "B1", "expect": {"no_level": "ERROR"}}


def test_check_log_passes_on_warnings_below_error():
    lines = [
        {"request_id": "r1", "level": "WARNING", "message": "slow"},
        {"request_id": "r2", "level": "ERROR", "message": "other request"},
    ]
    assert rb.check_log(LOG_ITEM, "r1", lines) == (True, "no ERROR lines for request r1")


def test_check_log_fails_on_error_lines():
    lines = [{"request_id": "r1", "level": "error", "message": "unhandled error: KeyError"}]
    ok, detail = rb.check_log(LOG_ITEM, "r1", lines)
    assert not ok
    assert "unhandled error: KeyError" in detail


def test_check_log_needs_lines_for_the_request():
    assert rb.check_log(LOG_ITEM, "r1", []) == (False, "no log lines for request r1")


def test_check_log_has_message():
    item = {"id": "B2", "type": "log", "after": "B1", "expect": {"has_message": "handled"}}
    lines = [{"request_id": "r1", "level": "INFO", "message": "request handled"}]
    assert rb.check_log(item, "r1", lines)[0]
    assert not rb.check_log(item, "r1", [{"request_id": "r1", "message": "x"}])[0]


def test_run_orders_results_and_links_log_items_to_requests():
    items = items_from(rb.extract_block(CONTRACT_BODY))
    calls = []

    def send(base_url, item):
        calls.append((base_url, item["method"], item["path"]))
        return 200, {"id": "42", "stock": 5}, "req-1"

    logs = [{"request_id": "req-1", "level": "INFO", "message": "request handled"}]
    results = rb.run(items, "http://api", lambda: logs, send=send, wait_seconds=0)
    assert calls == [("http://api", "GET", "/items/42")]
    assert [(r["id"], r["ok"]) for r in results] == [("B1", True), ("B2", True), ("B3", None)]
    assert results[0]["request_id"] == "req-1"
    report = rb.build_report(results)
    assert report["passed"] is True
    assert report["source"] == "ci-local"


def test_run_marks_failed_requests():
    items = items_from(rb.extract_block(CONTRACT_BODY))

    def send(base_url, item):
        raise OSError("connection refused")

    results = rb.run(items, "http://api", lambda: [], send=send, wait_seconds=0)
    assert results[0]["ok"] is False
    assert "connection refused" in results[0]["detail"]
    assert results[1] == {
        "id": "B2",
        "type": "log",
        "ok": False,
        "detail": "B1 has no request id to look up",
    }
    assert rb.build_report(results)["passed"] is False


def test_report_for_an_invalid_block():
    report = rb.build_report(error="The PR description is empty.")
    assert report == {
        "version": 1,
        "source": "ci-local",
        "passed": False,
        "error": "The PR description is empty.",
        "items": [],
    }


def test_main_writes_an_error_report(tmp_path, monkeypatch):
    output = tmp_path / "behavior-results.json"
    monkeypatch.setenv("PR_BODY", "no block")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert rb.main(["--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["passed"] is False
    assert "no ```yaml expected-behavior block" in report["error"]


def test_read_log_file_skips_non_json_lines(tmp_path):
    log = tmp_path / "server.log"
    log.write_text('Serving\n{"request_id": "r1", "level": "INFO"}\n[1, 2]\n')
    assert rb.read_log_file(log) == [{"request_id": "r1", "level": "INFO"}]
    assert rb.read_log_file(tmp_path / "missing.log") == []
