"""Keep the files pr-triage reads (openapi.yaml, tickets, PR template) in the agreed format."""

import re
from pathlib import Path

import pytest
import yaml

from inventory import handler as app
from scripts import run_behavior

ROOT = Path(__file__).resolve().parent.parent
TICKETS = sorted((ROOT / "tickets").glob("*.md"))
REQUIREMENT_LINE = re.compile(r"^- (R\d+): \S.*$")


def test_openapi_lists_exactly_the_handler_routes():
    spec = yaml.safe_load((ROOT / "openapi.yaml").read_text())
    documented = {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert documented == set(app.ROUTES)


@pytest.mark.parametrize("ticket", TICKETS, ids=lambda path: path.stem)
def test_ticket_file_format(ticket):
    text = ticket.read_text()
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    assert match, "front matter between the first two --- lines"
    front = yaml.safe_load(match.group(1))
    assert set(front) == {"key", "title", "status", "repos"}
    assert front["key"] == ticket.stem
    assert re.fullmatch(r"INV-\d+", front["key"])
    assert "demo-inventory-api" in front["repos"]

    body = match.group(2).splitlines()
    assert body[0] == "## Requirements"
    ids = [REQUIREMENT_LINE.match(line).group(1) for line in body[1:] if line.strip()]
    assert ids == [f"R{n}" for n in range(1, len(ids) + 1)]


def test_contract_example_ticket():
    text = (ROOT / "tickets" / "INV-123.md").read_text()
    assert "repos: [demo-inventory-api, demo-inventory-web]" in text
    assert "- R1: The item API response includes the stock quantity (`stock`)." in text
    assert "- R2: The item detail page shows the stock quantity." in text


def test_pr_template_has_the_sections_and_a_valid_block():
    template = (ROOT / ".github" / "pull_request_template.md").read_text()
    headings = re.findall(r"^## (.+)$", template, re.M)
    assert headings == ["Ticket", "Type", "Summary", "Expected behavior"]
    items = run_behavior.parse_items(run_behavior.extract_block(template))
    assert [item["id"] for item in items] == ["B1", "B2"]
