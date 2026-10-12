import json
from decimal import Decimal

import pytest

from inventory import handler as app


def log_lines(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_health(call):
    response, body, request_id = call("GET", "/health")
    assert response["statusCode"] == 200
    assert body == {"ok": True}
    assert response["headers"]["x-request-id"] == request_id
    assert response["headers"]["content-type"] == "application/json"


def test_list_items_returns_every_page_sorted_with_stock(call):
    response, body, _ = call("GET", "/items")
    assert response["statusCode"] == 200
    assert [item["id"] for item in body["items"]] == ["41", "42", "43"]
    assert body["items"][1] == {
        "id": "42",
        "name": "Ceramic mug",
        "price": 12.5,
        "stock": 5,
        "sold_out": False,
    }


def test_list_items_sorts_numeric_ids_numerically(call, table):
    table.put_item(Item={"id": "100", "name": "Pen", "price": Decimal("2"), "stock": 1})
    _, body, _ = call("GET", "/items")
    assert [item["id"] for item in body["items"]] == ["41", "42", "43", "100"]


def test_get_item(call):
    response, body, _ = call("GET", "/items/42")
    assert response["statusCode"] == 200
    assert body == {"id": "42", "name": "Ceramic mug", "price": 12.5, "stock": 5, "sold_out": False}


def test_item_in_stock_is_not_sold_out(call):
    _, body, _ = call("GET", "/items/41")
    assert body.get("sold_out") is False


def test_item_without_stock_is_sold_out(call):
    _, body, _ = call("GET", "/items/43")
    assert body.get("sold_out") is True


def test_get_item_returns_zero_stock(call):
    _, body, _ = call("GET", "/items/43")
    assert body["stock"] == 0


def test_get_missing_item(call):
    response, body, _ = call("GET", "/items/99")
    assert response["statusCode"] == 404
    assert body == {"error": "not_found"}


@pytest.mark.parametrize("item_id", ["a%20b", "%2E%2E", "x" * 65])
def test_get_item_with_invalid_id_is_not_found(call, item_id):
    response, body, _ = call("GET", f"/items/{item_id}")
    assert response["statusCode"] == 404
    assert body == {"error": "not_found"}


def test_create_item(call, table):
    response, body, _ = call("POST", "/items", {"id": "44", "name": " Pencil case ", "price": 9})
    assert response["statusCode"] == 201
    assert body == {"id": "44", "name": "Pencil case", "price": 9.0, "stock": 0, "sold_out": True}
    assert table.items["44"]["stock"] == 0
    assert table.items["44"]["price"] == Decimal("9")


def test_create_item_accepts_base64_body(call):
    response, body, _ = call(
        "POST", "/items", {"id": "45", "name": "Sticker", "price": 1.5}, base64_body=True
    )
    assert response["statusCode"] == 201
    assert body["price"] == 1.5


def test_create_duplicate_item_conflicts(call):
    response, body, _ = call("POST", "/items", {"id": "42", "name": "Mug", "price": 1})
    assert response["statusCode"] == 409
    assert body["error"] == "conflict"


def test_create_item_rejects_invalid_json(call):
    response, body, _ = call("POST", "/items", "{not json")
    assert response["statusCode"] == 400
    assert body["error"] == "invalid_json"


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        ([], "body must be a JSON object"),
        ({"id": "46", "name": "Pen", "price": 1, "stock": 3}, "unknown field: stock"),
        ({"name": "Pen", "price": 1}, "id must be"),
        ({"id": "4 6", "name": "Pen", "price": 1}, "id must be"),
        ({"id": "46", "price": 1}, "name is required"),
        ({"id": "46", "name": "   ", "price": 1}, "name is required"),
        ({"id": "46", "name": "x" * 101, "price": 1}, "name must be at most"),
        ({"id": "46", "name": "Pen"}, "price must be a number"),
        ({"id": "46", "name": "Pen", "price": "1"}, "price must be a number"),
        ({"id": "46", "name": "Pen", "price": True}, "price must be a number"),
        ({"id": "46", "name": "Pen", "price": -1}, "price must be between"),
        ({"id": "46", "name": "Pen", "price": 1_000_001}, "price must be between"),
    ],
)
def test_create_item_validation(call, table, payload, detail):
    response, body, _ = call("POST", "/items", payload)
    assert response["statusCode"] == 400
    assert body["error"] == "invalid_item"
    assert body["detail"].startswith(detail)
    assert "46" not in table.items


def test_create_item_rejects_non_finite_price():
    with pytest.raises(app.BadRequest, match="price must be a number"):
        app.validate_new_item({"id": "46", "name": "Pen", "price": float("inf")})


def test_unknown_path(call):
    response, body, _ = call("GET", "/stock")
    assert response["statusCode"] == 404
    assert body == {"error": "not_found"}


def test_wrong_method(call):
    response, body, _ = call("DELETE", "/items/42")
    assert response["statusCode"] == 405
    assert body == {"error": "method_not_allowed"}


def test_logs_one_json_line_per_request(call, capsys):
    capsys.readouterr()
    _, _, request_id = call("GET", "/items/42")
    assert log_lines(capsys) == [
        {
            "level": "INFO",
            "request_id": request_id,
            "message": "request handled",
            "route": "GET /items/{id}",
            "status": 200,
        }
    ]


def test_client_errors_log_a_warning(call, capsys):
    capsys.readouterr()
    call("GET", "/items/99")
    assert [line["level"] for line in log_lines(capsys)] == ["WARNING"]


def test_unhandled_error_returns_500_and_logs_error(call, table, capsys, monkeypatch):
    def broken(Key):
        raise RuntimeError("boom")

    monkeypatch.setattr(table, "get_item", broken)
    capsys.readouterr()
    response, body, request_id = call("GET", "/items/42")
    assert response["statusCode"] == 500
    assert body == {"error": "internal_error"}
    lines = log_lines(capsys)
    assert [line["level"] for line in lines] == ["ERROR", "ERROR"]
    assert lines[0]["message"] == "unhandled error: RuntimeError"
    assert all(line["request_id"] == request_id for line in lines)


def test_get_table_uses_the_local_endpoint(monkeypatch):
    captured = {}

    class FakeResource:
        def Table(self, name):
            captured["name"] = name
            return "table"

    def fake_resource(service, endpoint_url=None):
        captured["service"] = service
        captured["endpoint_url"] = endpoint_url
        return FakeResource()

    monkeypatch.setattr(app, "_table", None)
    monkeypatch.setattr(app.boto3, "resource", fake_resource)
    monkeypatch.setenv("TABLE_NAME", "items-local")
    monkeypatch.setenv("DYNAMODB_ENDPOINT", "http://localhost:8000")
    assert app.get_table() == "table"
    assert captured == {
        "service": "dynamodb",
        "endpoint_url": "http://localhost:8000",
        "name": "items-local",
    }
