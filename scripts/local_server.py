"""Serve the Lambda handler over HTTP for local runs and CI.

Each request becomes an HTTP API (payload v2) event and is passed to the handler in-process.
The handler's JSON log lines go to stdout, so redirect stdout to a file to keep a server log.
"""

import argparse
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parent.parent

LOCAL_DEFAULTS = {
    "TABLE_NAME": "inventory-items-local",
    "DYNAMODB_ENDPOINT": "http://localhost:8000",
    "AWS_REGION": "ap-southeast-2",
    "AWS_DEFAULT_REGION": "ap-southeast-2",
    "AWS_ACCESS_KEY_ID": "local",
    "AWS_SECRET_ACCESS_KEY": "local",
}
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
}


class FakeContext:
    function_name = "inventory-local"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:local:000000000000:function:inventory-local"

    def __init__(self):
        self.aws_request_id = str(uuid.uuid4())

    def get_remaining_time_in_millis(self):
        return 10_000


def to_event(method, target, headers, body):
    """Build an HTTP API payload v2 event."""
    parts = urlsplit(target)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    lowered = {key.lower(): value for key, value in headers.items()}
    now = time.time()
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": parts.path or "/",
        "rawQueryString": parts.query,
        "headers": lowered,
        "queryStringParameters": query or None,
        "requestContext": {
            "http": {
                "method": method,
                "path": parts.path or "/",
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
                "userAgent": lowered.get("user-agent", ""),
            },
            "requestId": str(uuid.uuid4()),
            "routeKey": "$default",
            "stage": "$default",
            "time": time.strftime("%d/%b/%Y:%H:%M:%S +0000", time.gmtime(now)),
            "timeEpoch": int(now * 1000),
        },
        "body": body.decode("utf-8") if body else None,
        "isBase64Encoded": False,
    }


def invoke(method, target, headers, body):
    """Call the Lambda handler and return (status, headers, body bytes)."""
    from inventory import handler as app

    response = app.handler(to_event(method, target, headers, body), FakeContext())
    payload = (response.get("body") or "").encode("utf-8")
    return response["statusCode"], dict(response.get("headers") or {}), payload


class RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status, headers, payload):
        self.send_response(status)
        for key, value in {**headers, **CORS_HEADERS}.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self._send(*invoke(self.command, self.path, dict(self.headers.items()), body))

    def do_OPTIONS(self):
        self._send(204, {}, b"")

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

    def log_message(self, format, *args):
        """Keep stdout for the handler's JSON log lines only."""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    parser.add_argument(
        "--src",
        default=str(ROOT / "src"),
        help="folder that contains the inventory package (CI passes the PR's src)",
    )
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(args.src).resolve()))
    for key, value in LOCAL_DEFAULTS.items():
        os.environ.setdefault(key, value)
    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    print(f"Serving the inventory API on http://{args.host}:{args.port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
