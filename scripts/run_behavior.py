"""Run the expected behavior from the PR description against a running API.

Reads the PR description from PR_BODY, runs every api item against BASE_URL, checks log
items in the server log file by request id, and writes behavior-results.json.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

BLOCK_INFO = "yaml expected-behavior"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
ITEM_ID = re.compile(r"^B\d+$")
REQUIREMENT_ID = re.compile(r"^R\d+$")
OPENING_FENCE = re.compile(r"^ {0,3}(`{3,})[ \t]*(.*?)[ \t]*$")
API_EXPECT_KEYS = {"status", "body_has", "body_not_has", "body_includes"}
LOG_EXPECT_KEYS = {"no_level", "has_message"}
REQUEST_TIMEOUT = 10
LOG_WAIT_SECONDS = 3.0


class BehaviorError(ValueError):
    """The expected-behavior block is missing or invalid."""


def extract_block(body):
    """Return the text inside the first ```yaml expected-behavior fence."""
    if not body or not body.strip():
        raise BehaviorError("The PR description is empty.")
    lines = body.replace("\r\n", "\n").split("\n")
    for index, line in enumerate(lines):
        match = OPENING_FENCE.match(line)
        if not match or match.group(2) != BLOCK_INFO:
            continue
        closing = re.compile(rf"^ {{0,3}}{match.group(1)}`*[ \t]*$")
        content = []
        for inner in lines[index + 1 :]:
            if closing.match(inner):
                return "\n".join(content)
            content.append(inner)
        raise BehaviorError("The expected-behavior block is not closed.")
    raise BehaviorError("The PR description has no ```yaml expected-behavior block.")


def parse_items(text):
    """Parse and validate the block. Returns items with `method` and `path` added for api items."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise BehaviorError(f"The expected-behavior block is not valid YAML: {exc}") from None
    if not isinstance(data, list) or not data:
        raise BehaviorError("The expected-behavior block must be a non-empty YAML list.")

    items, seen = [], set()
    for position, raw in enumerate(data, start=1):
        if not isinstance(raw, dict):
            raise BehaviorError(f"Item {position} must be a mapping.")
        item = dict(raw)
        item_id = item.get("id")
        label = item_id if isinstance(item_id, str) else f"Item {position}"
        if not isinstance(item_id, str) or not ITEM_ID.match(item_id):
            raise BehaviorError(f"{label}: `id` must look like B1.")
        if item_id in seen:
            raise BehaviorError(f"{label}: `id` is used twice.")
        seen.add(item_id)
        covers = item.get("covers")
        if (
            not isinstance(covers, list)
            or not covers
            or not all(isinstance(c, str) and REQUIREMENT_ID.match(c) for c in covers)
        ):
            raise BehaviorError(f"{label}: `covers` must list requirement ids such as [R1].")
        kind = item.get("type")
        if kind == "api":
            _validate_api(label, item)
        elif kind == "log":
            _validate_log(label, item)
        elif kind == "ui":
            _validate_ui(label, item)
        else:
            raise BehaviorError(f"{label}: `type` must be api, log or ui.")
        items.append(item)

    api_ids = {item["id"] for item in items if item["type"] == "api"}
    for item in items:
        if item["type"] == "log" and item["after"] not in api_ids:
            raise BehaviorError(f"{item['id']}: `after` must name an api item.")
    return items


def _validate_api(label, item):
    request = item.get("request")
    parts = request.split() if isinstance(request, str) else []
    if len(parts) != 2 or parts[0] not in METHODS:
        raise BehaviorError(f'{label}: `request` must look like "GET /items/42".')
    method, path = parts
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        raise BehaviorError(f"{label}: the request path must start with / and have no host.")
    item["method"], item["path"] = method, path

    headers = item.get("headers", {})
    if not isinstance(headers, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
    ):
        raise BehaviorError(f"{label}: `headers` must map strings to strings.")
    if "body" in item and not isinstance(item["body"], dict):
        raise BehaviorError(f"{label}: `body` must be a JSON object.")

    expect = item.get("expect")
    if not isinstance(expect, dict) or not isinstance(expect.get("status"), int):
        raise BehaviorError(f"{label}: `expect.status` must be an HTTP status code.")
    unknown = sorted(set(expect) - API_EXPECT_KEYS)
    if unknown:
        raise BehaviorError(f"{label}: unknown expect keys: {', '.join(unknown)}.")
    for key in ("body_has", "body_not_has"):
        value = expect.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise BehaviorError(f"{label}: `expect.{key}` must be a list of keys.")
    if "body_includes" in expect and not isinstance(expect["body_includes"], dict):
        raise BehaviorError(f"{label}: `expect.body_includes` must be an object.")


def _validate_log(label, item):
    if not isinstance(item.get("after"), str):
        raise BehaviorError(f"{label}: `after` must name an api item.")
    expect = item.get("expect")
    if not isinstance(expect, dict) or not expect:
        raise BehaviorError(f"{label}: log items need `expect.no_level` or `expect.has_message`.")
    unknown = sorted(set(expect) - LOG_EXPECT_KEYS)
    if unknown:
        raise BehaviorError(f"{label}: unknown expect keys: {', '.join(unknown)}.")
    if "no_level" in expect and str(expect["no_level"]).upper() not in LEVELS:
        raise BehaviorError(f"{label}: `expect.no_level` must be a log level such as ERROR.")
    if "has_message" in expect and not isinstance(expect["has_message"], str):
        raise BehaviorError(f"{label}: `expect.has_message` must be text.")


def _validate_ui(label, item):
    if not isinstance(item.get("step"), str) or not item["step"].strip():
        raise BehaviorError(f"{label}: ui items need a `step`.")
    page = item.get("page")
    if page is not None and (not isinstance(page, str) or not page.startswith("/")):
        raise BehaviorError(f"{label}: `page` must be a path that starts with /.")


def get_path(body, dotted):
    """Follow a dotted path such as `item.stock` or `items.0.id`. Returns (found, value)."""
    current = body
    for part in dotted.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return False, None
    return True, current


def find_mismatch(actual, expected, path=""):
    """Return the first path where `expected` is not a deep subset of `actual`, or None."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return path or "(body)"
        for key, value in expected.items():
            child = f"{path}.{key}" if path else str(key)
            if key not in actual:
                return child
            mismatch = find_mismatch(actual[key], value, child)
            if mismatch is not None:
                return mismatch
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return path or "(body)"
        for index, (a, e) in enumerate(zip(actual, expected, strict=True)):
            mismatch = find_mismatch(a, e, f"{path}.{index}" if path else str(index))
            if mismatch is not None:
                return mismatch
        return None
    if isinstance(expected, bool) or isinstance(actual, bool):
        same = isinstance(expected, bool) and isinstance(actual, bool) and actual == expected
    else:
        same = actual == expected
    return None if same else (path or "(body)")


def check_api(item, status, body):
    """Compare one api response with the item's expectations. Returns (ok, detail)."""
    expect = item["expect"]
    problems = []
    if status != expect["status"]:
        problems.append(f"status {status}, expected {expect['status']}")
    needs_json = any(key in expect for key in ("body_has", "body_not_has", "body_includes"))
    if needs_json and not isinstance(body, dict | list):
        problems.append("the response body is not JSON")
    else:
        for key in expect.get("body_has", []):
            if not get_path(body, key)[0]:
                problems.append(f"body has no `{key}`")
        for key in expect.get("body_not_has", []):
            if get_path(body, key)[0]:
                problems.append(f"body has `{key}`")
        if "body_includes" in expect:
            mismatch = find_mismatch(body, expect["body_includes"])
            if mismatch is not None:
                found, value = get_path(body, mismatch)
                actual = json.dumps(value) if found else "missing"
                problems.append(f"body does not include the expected `{mismatch}` (got {actual})")
    if problems:
        return False, "; ".join(problems)
    checked = [f"status {status}"]
    if expect.get("body_has"):
        checked.append(f"body has {', '.join(expect['body_has'])}")
    if expect.get("body_not_has"):
        checked.append(f"body has no {', '.join(expect['body_not_has'])}")
    if "body_includes" in expect:
        checked.append("body includes the expected values")
    return True, ", ".join(checked)


def check_log(item, request_id, lines):
    """Check log lines for one request. Returns (ok, detail)."""
    expect = item["expect"]
    mine = [line for line in lines if line.get("request_id") == request_id]
    if not mine:
        return False, f"no log lines for request {request_id}"
    problems = []
    if "no_level" in expect:
        threshold_name = str(expect["no_level"]).upper()
        threshold = LEVELS[threshold_name]
        bad = [
            line for line in mine if LEVELS.get(str(line.get("level", "")).upper(), 0) >= threshold
        ]
        if bad:
            problems.append(
                f"{len(bad)} log line(s) at {threshold_name} or above: {bad[0].get('message')}"
            )
    if "has_message" in expect:
        wanted = expect["has_message"]
        if not any(wanted in str(line.get("message", "")) for line in mine):
            problems.append(f"no log line contains {wanted!r}")
    if problems:
        return False, "; ".join(problems)
    parts = []
    if "no_level" in expect:
        parts.append(f"no {str(expect['no_level']).upper()} lines")
    if "has_message" in expect:
        parts.append(f"found {expect['has_message']!r}")
    return True, f"{' and '.join(parts)} for request {request_id}"


def call_api(base_url, item):
    """Send one api item's request. Returns (status, parsed body or None, request id)."""
    url = base_url.rstrip("/") + item["path"]
    headers = {"accept": "application/json", **item.get("headers", {})}
    data = None
    if "body" in item:
        data = json.dumps(item["body"]).encode()
        headers.setdefault("content-type", "application/json")
    request = urllib.request.Request(url, data=data, method=item["method"], headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            status, raw, response_headers = response.status, response.read(), response.headers
    except urllib.error.HTTPError as err:
        status, raw, response_headers = err.code, err.read(), err.headers
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = None
    return status, body, response_headers.get("x-request-id")


def read_log_file(path):
    """Read JSON log lines from a file, skipping anything that is not a JSON object."""
    lines = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return lines
    for raw in text.splitlines():
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            lines.append(parsed)
    return lines


def run(items, base_url, read_logs, send=call_api, wait_seconds=LOG_WAIT_SECONDS):
    """Run every item. `read_logs` returns the current log lines (list of dicts)."""
    results, request_ids = [], {}
    for item in items:
        if item["type"] != "api":
            continue
        result = {"id": item["id"], "type": "api", "request": item["request"]}
        try:
            status, body, request_id = send(base_url, item)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            result.update(ok=False, status=None, request_id=None, detail=f"request failed: {exc}")
        else:
            ok, detail = check_api(item, status, body)
            result.update(ok=ok, status=status, request_id=request_id, detail=detail)
            request_ids[item["id"]] = request_id
        results.append(result)

    for item in items:
        if item["type"] != "log":
            continue
        request_id = request_ids.get(item["after"])
        if not request_id:
            results.append(
                {
                    "id": item["id"],
                    "type": "log",
                    "ok": False,
                    "detail": f"{item['after']} has no request id to look up",
                }
            )
            continue
        deadline = time.monotonic() + wait_seconds
        while True:
            ok, detail = check_log(item, request_id, read_logs())
            if ok or time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        results.append({"id": item["id"], "type": "log", "ok": ok, "detail": detail})

    for item in items:
        if item["type"] == "ui":
            results.append(
                {"id": item["id"], "type": "ui", "ok": None, "detail": "checked by a person"}
            )

    order = {item["id"]: index for index, item in enumerate(items)}
    results.sort(key=lambda result: order[result["id"]])
    return results


def build_report(results=None, error=None):
    results = results or []
    passed = error is None and not any(result["ok"] is False for result in results)
    return {"version": 1, "source": "ci-local", "passed": passed, "error": error, "items": results}


def summary_markdown(report):
    if report["error"]:
        return f"### Expected behavior\n\n:x: {report['error']}\n"
    marks = {True: ":white_check_mark:", False: ":x:", None: ":bust_in_silhouette:"}
    rows = [
        f"| {marks[r['ok']]} | {r['id']} | {r['type']} | {r['detail']} |" for r in report["items"]
    ]
    verdict = "passed" if report["passed"] else "failed"
    table = "\n".join(["| | Item | Type | Result |", "|---|---|---|---|", *rows])
    return f"### Expected behavior: {verdict}\n\n{table}\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-file", help="server log file with JSON log lines")
    parser.add_argument("--output", default="behavior-results.json")
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL"))
    args = parser.parse_args(argv)

    try:
        items = parse_items(extract_block(os.environ.get("PR_BODY", "")))
        if any(item["type"] == "api" for item in items) and not args.base_url:
            raise BehaviorError("BASE_URL is not set.")
    except BehaviorError as exc:
        report = build_report(error=str(exc))
    else:

        def read_logs():
            return read_log_file(args.log_file) if args.log_file else []

        report = build_report(run(items, args.base_url, read_logs))

    Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = summary_markdown(report)
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(summary)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
