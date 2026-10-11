"""End-to-end: the local server wraps the handler and run_behavior checks it."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from scripts import local_server
from scripts import run_behavior as rb

pytestmark = pytest.mark.usefixtures("table")


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), local_server.RequestHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_to_event_builds_a_v2_event():
    event = local_server.to_event(
        "POST", "/items?limit=2", {"Content-Type": "application/json"}, b'{"id": "1"}'
    )
    assert event["rawPath"] == "/items"
    assert event["rawQueryString"] == "limit=2"
    assert event["queryStringParameters"] == {"limit": "2"}
    assert event["headers"] == {"content-type": "application/json"}
    assert event["requestContext"]["http"]["method"] == "POST"
    assert event["body"] == '{"id": "1"}'


def test_server_returns_handler_responses_with_cors(server):
    with urllib.request.urlopen(f"{server}/items/42") as response:
        assert response.status == 200
        assert json.loads(response.read()) == {
            "id": "42",
            "name": "Ceramic mug",
            "price": 12.5,
            "stock": 5,
        }
        assert response.headers["x-request-id"]
        assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_server_answers_preflight(server):
    request = urllib.request.Request(f"{server}/items", method="OPTIONS")
    with urllib.request.urlopen(request) as response:
        assert response.status == 204
        assert response.headers["Access-Control-Allow-Methods"] == "GET, POST, PATCH, OPTIONS"
        assert response.headers["Access-Control-Allow-Headers"] == "content-type"


def test_expected_behavior_against_the_base_api(server, capsys):
    body = """```yaml expected-behavior
- id: B1
  covers: [R1]
  type: api
  request: GET /items/42
  expect:
    status: 200
    body_has: [name, price, stock]
- id: B2
  covers: [R1]
  type: log
  after: B1
  expect:
    no_level: ERROR
    has_message: request handled
- id: B3
  covers: [R1]
  type: api
  request: POST /items
  body: {id: "44", name: Pencil case, price: 9}
  expect:
    status: 201
    body_includes: {id: "44", price: 9}
- id: B4
  covers: [R2]
  type: api
  request: GET /items/43
  expect:
    status: 200
    body_has: [sold_out]
```"""
    seen = []

    def read_logs():
        seen.extend(
            json.loads(line)
            for line in capsys.readouterr().out.splitlines()
            if line.startswith("{")
        )
        return seen

    results = rb.run(rb.parse_items(rb.extract_block(body)), server, read_logs, wait_seconds=1)
    outcome = {r["id"]: (r["ok"], r["detail"]) for r in results}
    assert outcome["B1"][0] is True
    assert outcome["B2"][0] is True, outcome["B2"]
    assert outcome["B3"][0] is True, outcome["B3"]
    assert outcome["B4"] == (False, "body has no `sold_out`")
    assert rb.build_report(results)["passed"] is False
