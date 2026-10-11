import base64
import json
import uuid
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

from inventory import handler as app

SEED = [
    {"id": "41", "name": "Canvas tote bag", "price": Decimal("24"), "stock": Decimal("12")},
    {"id": "42", "name": "Ceramic mug", "price": Decimal("12.5"), "stock": Decimal("5")},
    {"id": "43", "name": "Dot grid notebook", "price": Decimal("8"), "stock": Decimal("0")},
]


class FakeTable:
    """In-memory stand-in for the boto3 Table calls the handler uses."""

    def __init__(self, items=(), page_size=2):
        self.items = {item["id"]: dict(item) for item in items}
        self.page_size = page_size

    def get_item(self, Key):
        item = self.items.get(Key["id"])
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression == "attribute_not_exists(id)" and Item["id"] in self.items:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "PutItem",
            )
        self.items[Item["id"]] = dict(Item)
        return {}

    def scan(self, ExclusiveStartKey=None):
        ids = sorted(self.items)
        start = ids.index(ExclusiveStartKey["id"]) + 1 if ExclusiveStartKey else 0
        page_ids = ids[start : start + self.page_size]
        page = {"Items": [dict(self.items[i]) for i in page_ids]}
        if start + self.page_size < len(ids):
            page["LastEvaluatedKey"] = {"id": page_ids[-1]}
        return page


class FakeContext:
    def __init__(self):
        self.aws_request_id = str(uuid.uuid4())


@pytest.fixture
def table(monkeypatch):
    fake = FakeTable(SEED)
    monkeypatch.setattr(app, "get_table", lambda: fake)
    return fake


def make_event(method, path, body=None, base64_body=False):
    raw = None if body is None else (body if isinstance(body, str) else json.dumps(body))
    if raw is not None and base64_body:
        raw = base64.b64encode(raw.encode()).decode()
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"content-type": "application/json"},
        "requestContext": {"http": {"method": method, "path": path}},
        "body": raw,
        "isBase64Encoded": base64_body,
    }


@pytest.fixture
def call(table):
    def _call(method, path, body=None, base64_body=False):
        context = FakeContext()
        response = app.handler(make_event(method, path, body, base64_body), context)
        return response, json.loads(response["body"]), context.aws_request_id

    return _call
