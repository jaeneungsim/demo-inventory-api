import subprocess
import textwrap
import xml.etree.ElementTree as ET

import pytest

from scripts import test_relevance as tr

TEMPLATE_BODY = """## Ticket
INV-123

## Type
<!-- one of: feature, bugfix, refactor, tests, docs -->
feature

## Summary
Adds stock.
"""


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (TEMPLATE_BODY, "feature"),
        ("## Type\r\n\r\nBugfix\r\n", "bugfix"),
        ("## Type\n<!--\nfeature\n-->\nrefactor\n", "refactor"),
        ("## Type\n\n## Summary\nfeature\n", None),
        ("## Type\nsomething-else\n", None),
        ("## Summary\nfeature\n", None),
        ("", None),
    ],
)
def test_parse_pr_type(body, expected):
    assert tr.parse_pr_type(body) == expected


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["tests/test_items.py"], "tests"),
        (["README.md", "docs/guide.md"], "docs"),
        (["src/inventory/handler.py", "tests/test_handler.py"], "feature"),
        ([], "feature"),
    ],
)
def test_infer_pr_type(files, expected):
    assert tr.infer_pr_type(files) == expected


def test_is_test_module():
    assert tr.is_test_module("tests/test_handler.py")
    assert tr.is_test_module("tests/scripts/stock_test.py")
    assert not tr.is_test_module("tests/conftest.py")
    assert not tr.is_test_module("scripts/test_relevance.py")


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.test_items" file="tests/test_items.py" name="test_passes"/>
  <testcase classname="tests.test_items" file="tests/test_items.py" name="test_assert">
    <failure message="assert 'stock' in {}">E  assert 'stock' in {}

tests/test_items.py:9: AssertionError</failure>
  </testcase>
  <testcase classname="tests.test_items" file="tests/test_items.py" name="test_key_error">
    <failure message="KeyError: 'stock'">tests/test_items.py:12: KeyError</failure>
  </testcase>
  <testcase classname="tests.test_items" file="tests/test_items.py" name="test_import">
    <failure message="ImportError: cannot import name 'stock_of'">x</failure>
  </testcase>
  <testcase classname="tests.test_items.TestPages" file="tests/test_items.py" name="test_attr">
    <failure message="">tests/test_items.py:20: AttributeError</failure>
  </testcase>
  <testcase classname="tests.test_items" file="tests/test_items.py" name="test_fixture">
    <error message="failed on setup with &quot;NameError&quot;">x</error>
  </testcase>
  <testcase classname="tests.test_items" file="tests/test_items.py" name="test_skip">
    <skipped message="later"/>
  </testcase>
  <testcase classname="" file="tests/test_new.py" name="tests.test_new">
    <error message="collection failure">ModuleNotFoundError: No module named 'inventory.stock'</error>
  </testcase>
</testsuite></testsuites>
"""


def test_parse_junit():
    cases, broken = tr.parse_junit(JUNIT)
    assert cases == {
        "tests/test_items.py::test_passes": "passed",
        "tests/test_items.py::test_assert": "failed_assertion",
        "tests/test_items.py::test_key_error": "failed_assertion",
        "tests/test_items.py::test_import": "failed_error",
        "tests/test_items.py::TestPages::test_attr": "failed_error",
        "tests/test_items.py::test_fixture": "failed_error",
        "tests/test_items.py::test_skip": "not_run",
    }
    assert broken == {"tests/test_new.py"}


def test_exception_name_prefers_the_message_prefix():
    element = ET.fromstring('<failure message="pkg.errors.ImportError: nope">x: KeyError</failure>')
    assert tr.exception_name(element) == "ImportError"


def test_combine_marks_collection_errors_for_every_test_in_the_file():
    before = ({"tests/test_items.py::test_a": "passed"}, {"tests/test_new.py"})
    after = (
        {"tests/test_items.py::test_a": "passed", "tests/test_new.py::test_b": "passed"},
        set(),
    )
    assert tr.combine(before, after) == [
        {"id": "tests/test_items.py::test_a", "before": "passed", "after": "passed"},
        {"id": "tests/test_new.py::test_b", "before": "failed_error", "after": "passed"},
    ]


def test_combine_keeps_files_that_never_collected():
    tests = tr.combine(({}, {"tests/test_x.py"}), ({}, {"tests/test_x.py"}))
    assert tests == [
        {"id": "tests/test_x.py::(collection)", "before": "failed_error", "after": "failed_error"}
    ]


def case(before, after):
    return {"id": "t", "before": before, "after": after}


@pytest.mark.parametrize(
    ("pr_type", "src", "files", "tests", "verdict"),
    [
        ("docs", True, ["tests/test_a.py"], [], "skipped"),
        ("feature", False, [], [], "skipped"),
        ("feature", True, [], [], "no_tests"),
        ("bugfix", True, [], [], "no_tests"),
        ("feature", True, ["t"], [case("failed_assertion", "passed")], "ok"),
        (
            "bugfix",
            True,
            ["t"],
            [case("passed", "passed"), case("failed_assertion", "passed")],
            "ok",
        ),
        (
            "feature",
            True,
            ["t"],
            [case("failed_error", "passed"), case("passed", "passed")],
            "undetermined",
        ),
        ("feature", True, ["t"], [case("passed", "passed")], "meaningless"),
        ("feature", True, ["t"], [case("failed_assertion", "failed_assertion")], "meaningless"),
        ("refactor", True, [], [], "ok"),
        ("refactor", True, ["t"], [case("passed", "passed")], "ok"),
        ("tests", False, ["t"], [case("passed", "passed")], "ok"),
        ("tests", False, ["t"], [case("failed_assertion", "passed")], "unexpected_failure"),
        ("refactor", True, ["t"], [case("passed", "failed_assertion")], "unexpected_failure"),
    ],
)
def test_decide(pr_type, src, files, tests, verdict):
    expectation, result, message = tr.decide(pr_type, src, files, tests)
    assert result == verdict
    assert expectation == ("fail_before" if pr_type in {"feature", "bugfix"} else "pass_before")
    assert message


# --- the whole check on a throwaway git repo ------------------------------------------

BASE_SRC = """
def is_sold_out(stock):
    return stock < 0
"""
FIXED_SRC = """
def is_sold_out(stock):
    return stock <= 0


def restock(stock, amount):
    return stock + amount
"""


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def write(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(text).lstrip())


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "dev")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "commit.gpgsign", "false")
    write(
        repo,
        "pyproject.toml",
        """
        [tool.pytest.ini_options]
        pythonpath = ["src"]
        addopts = "--import-mode=importlib"
    """,
    )
    write(repo, "src/stock.py", BASE_SRC)
    write(
        repo,
        "tests/test_stock.py",
        """
        from stock import is_sold_out


        def test_positive_stock_is_not_sold_out():
            assert is_sold_out(3) is False
    """,
    )
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "INV-100: base")
    git(repo, "branch", "base")
    monkeypatch.chdir(repo)
    return repo


def commit_change(repo, files):
    for path, text in files.items():
        write(repo, path, text)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "INV-124: change")


def test_bugfix_with_a_test_that_fails_before_is_ok(repo):
    commit_change(
        repo,
        {
            "src/stock.py": FIXED_SRC,
            "tests/test_stock.py": """
            from stock import is_sold_out


            def test_positive_stock_is_not_sold_out():
                assert is_sold_out(3) is False


            def test_zero_stock_is_sold_out():
                assert is_sold_out(0) is True
        """,
        },
    )
    report = tr.build_report("base", "HEAD", "## Type\nbugfix\n")
    assert report["verdict"] == "ok", report
    assert report["expectation"] == "fail_before"
    by_id = {t["id"]: t for t in report["tests"]}
    assert by_id["tests/test_stock.py::test_zero_stock_is_sold_out"] == {
        "id": "tests/test_stock.py::test_zero_stock_is_sold_out",
        "before": "failed_assertion",
        "after": "passed",
    }


def test_feature_whose_tests_pass_on_the_base_is_meaningless(repo):
    commit_change(
        repo,
        {
            "src/stock.py": FIXED_SRC,
            "tests/test_stock.py": """
            from stock import is_sold_out


            def test_positive_stock_is_not_sold_out():
                assert is_sold_out(5) is False
        """,
        },
    )
    report = tr.build_report("base", "HEAD", "## Type\nfeature\n")
    assert report["verdict"] == "meaningless", report


def test_tests_for_a_new_function_are_undetermined(repo):
    commit_change(
        repo,
        {
            "src/stock.py": FIXED_SRC,
            "tests/test_restock.py": """
            from stock import restock


            def test_restock_adds_stock():
                assert restock(2, 3) == 5
        """,
        },
    )
    report = tr.build_report("base", "HEAD", "")
    assert report["pr_type"] == "feature"
    assert report["verdict"] == "undetermined", report
    assert report["tests"] == [
        {
            "id": "tests/test_restock.py::test_restock_adds_stock",
            "before": "failed_error",
            "after": "passed",
        }
    ]


def test_feature_without_tests(repo):
    commit_change(repo, {"src/stock.py": FIXED_SRC})
    report = tr.build_report("base", "HEAD", "## Type\nfeature\n")
    assert report["verdict"] == "no_tests"
    assert report["tests"] == []


def test_refactor_must_keep_tests_green_on_both_sides(repo):
    commit_change(
        repo,
        {
            "src/stock.py": "def is_sold_out(stock):\n    return not stock > -1\n",
            "tests/test_stock.py": """
            from stock import is_sold_out


            def test_positive_stock_is_not_sold_out():
                assert is_sold_out(3) is False


            def test_negative_stock_is_sold_out():
                assert is_sold_out(-1) is True
        """,
        },
    )
    report = tr.build_report("base", "HEAD", "## Type\nrefactor\n")
    assert report["expectation"] == "pass_before"
    assert report["verdict"] == "ok", report


def test_main_exits_1_for_failing_verdicts(repo, tmp_path, monkeypatch):
    commit_change(repo, {"src/stock.py": FIXED_SRC})
    monkeypatch.setenv("PR_BODY", "## Type\nbugfix\n")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    output = tmp_path / "test-relevance.json"
    assert tr.main(["--base", "base", "--output", str(output)]) == 1
    assert '"verdict": "no_tests"' in output.read_text()
