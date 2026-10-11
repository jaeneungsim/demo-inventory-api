"""Inventory API: one Lambda function behind an HTTP API ($default route)."""

import base64
import json
import math
import os
import re
from decimal import Decimal
from urllib.parse import unquote

import boto3
from botocore.exceptions import ClientError

ITEM_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_NAME_LENGTH = 100
MAX_PRICE = 1_000_000
NEW_ITEM_FIELDS = {"id", "name", "price"}

PATHS = (
    ("/health", re.compile(r"^/health$")),
    ("/items", re.compile(r"^/items$")),
    ("/items/{id}", re.compile(r"^/items/(?P<id>[^/]+)$")),
)

_table = None


class BadRequest(Exception):
    def __init__(self, error: str, detail: str):
        super().__init__(detail)
        self.error = error
        self.detail = detail


def get_table():
    global _table
    if _table is None:
        endpoint = os.environ.get("DYNAMODB_ENDPOINT") or None
        _table = boto3.resource("dynamodb", endpoint_url=endpoint).Table(os.environ["TABLE_NAME"])
    return _table


def handler(event, context):
    request_id = context.aws_request_id
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET").upper()
    path = unquote(event.get("rawPath") or http.get("path") or "/")

    template, params = match_path(path)
    route = f"{method} {template or path}"
    action = ROUTES.get((method, template))
    try:
        if template is None:
            status, body = 404, {"error": "not_found"}
        elif action is None:
            status, body = 405, {"error": "method_not_allowed"}
        else:
            status, body = action(event, **params)
    except BadRequest as exc:
        status, body = 400, {"error": exc.error, "detail": exc.detail}
    except Exception as exc:  # noqa: BLE001 - every failure must become a logged 500
        log("ERROR", request_id, f"unhandled error: {type(exc).__name__}", route, 500)
        status, body = 500, {"error": "internal_error"}

    log(level_for(status), request_id, "request handled", route, status)
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "x-request-id": request_id},
        "body": json.dumps(body),
    }


def match_path(path: str):
    for template, pattern in PATHS:
        match = pattern.match(path)
        if match:
            return template, match.groupdict()
    return None, {}


def health(event):
    return 200, {"ok": True}


def list_items(event):
    table = get_table()
    items = []
    kwargs = {}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    items.sort(key=sort_key)
    return 200, {"items": [public_item(item) for item in items]}


def get_item(event, id):
    if not ITEM_ID.match(id):
        return 404, {"error": "not_found"}
    item = get_table().get_item(Key={"id": id}).get("Item")
    if item is None:
        return 404, {"error": "not_found"}
    return 200, public_item(item)


def create_item(event):
    data = parse_json_body(event)
    item = validate_new_item(data)
    try:
        get_table().put_item(Item=item, ConditionExpression="attribute_not_exists(id)")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return 409, {"error": "conflict", "detail": f"item {item['id']} already exists"}
        raise
    return 201, public_item(item)


ROUTES = {
    ("GET", "/health"): health,
    ("GET", "/items"): list_items,
    ("POST", "/items"): create_item,
    ("GET", "/items/{id}"): get_item,
}


def parse_json_body(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise BadRequest("invalid_json", f"body is not valid JSON: {exc}") from None


def validate_new_item(data):
    if not isinstance(data, dict):
        raise BadRequest("invalid_item", "body must be a JSON object")
    unknown = sorted(set(data) - NEW_ITEM_FIELDS)
    if unknown:
        raise BadRequest("invalid_item", f"unknown field: {', '.join(unknown)}")

    item_id = data.get("id")
    if not isinstance(item_id, str) or not ITEM_ID.match(item_id):
        raise BadRequest("invalid_item", "id must be 1-64 letters, digits, '-' or '_'")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BadRequest("invalid_item", "name is required")
    if len(name) > MAX_NAME_LENGTH:
        raise BadRequest("invalid_item", f"name must be at most {MAX_NAME_LENGTH} characters")

    price = data.get("price")
    if isinstance(price, bool) or not isinstance(price, int | float) or not math.isfinite(price):
        raise BadRequest("invalid_item", "price must be a number")
    if price < 0 or price > MAX_PRICE:
        raise BadRequest("invalid_item", f"price must be between 0 and {MAX_PRICE}")

    # New items start without stock; restocking is a separate flow.
    return {"id": item_id, "name": name.strip(), "price": Decimal(str(price)), "stock": 0}


def public_item(item):
    return {
        "id": item["id"],
        "name": item["name"],
        "price": float(item["price"]),
        "stock": int(item.get("stock", 0)),
    }


def sort_key(item):
    item_id = item["id"]
    return (0, int(item_id), item_id) if item_id.isdigit() else (1, 0, item_id)


def level_for(status: int) -> str:
    if status >= 500:
        return "ERROR"
    if status >= 400:
        return "WARNING"
    return "INFO"


def log(level: str, request_id: str, message: str, route: str, status: int) -> None:
    line = {
        "level": level,
        "request_id": request_id,
        "message": message,
        "route": route,
        "status": status,
    }
    print(json.dumps(line), flush=True)
