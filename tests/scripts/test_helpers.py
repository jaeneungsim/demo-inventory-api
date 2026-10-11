import json
from decimal import Decimal

import pytest

from scripts import preview_info, seed, stage_name


@pytest.mark.parametrize(
    ("head_ref", "title", "stage"),
    [
        ("INV-123-stock-in-detail", "Anything", "inv-123"),
        ("feature/INV-7", None, "inv-7"),
        ("stock-in-detail", "INV-124 Mark sold out items", "inv-124"),
        ("inv-123-lowercase", "lowercase inv-123", None),
        ("INV-123abc", "", None),
        (None, None, None),
    ],
)
def test_stage_from(head_ref, title, stage):
    assert stage_name.stage_from(head_ref, title) == stage


def test_stage_name_main(monkeypatch, capsys):
    monkeypatch.setenv("HEAD_REF", "INV-125-pages")
    monkeypatch.delenv("PR_TITLE", raising=False)
    assert stage_name.main() == 0
    assert capsys.readouterr().out == "stage=inv-125\n"
    monkeypatch.setenv("HEAD_REF", "no-key")
    assert stage_name.main() == 1


def test_preview_info():
    outputs = {"inv-api-preview-inv-123": {"ApiUrl": "https://abc.execute-api.example/"}}
    assert preview_info.preview_info(outputs, "inv-123") == {
        "stage": "inv-123",
        "stack": "inv-api-preview-inv-123",
        "api_url": "https://abc.execute-api.example/",
    }
    assert preview_info.stack_name("dev") == "inv-api-dev"
    with pytest.raises(SystemExit):
        preview_info.preview_info({}, "inv-123")


def test_preview_info_main(tmp_path, capsys):
    path = tmp_path / "outputs.json"
    path.write_text(json.dumps({"inv-api-dev": {"ApiUrl": "https://dev/"}}))
    assert preview_info.main(["--outputs", str(path), "--stage", "dev"]) == 0
    assert json.loads(capsys.readouterr().out)["stack"] == "inv-api-dev"


def test_seed_items_match_the_contract():
    items = {item["id"]: item for item in seed.load_items()}
    assert {key: items[key]["stock"] for key in items} == {"41": 12, "42": 5, "43": 0}
    assert all(isinstance(item["price"], Decimal) for item in items.values())
