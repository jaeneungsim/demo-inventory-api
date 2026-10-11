"""Check that the PR's changed tests actually test the change.

Runs the changed test files on the base code (in a git worktree) and on the PR, then writes
test-relevance.json. For features and bug fixes, at least one changed test must fail on the
base code with an assertion and pass on the PR. For refactors and test-only PRs, every changed
test must pass on both.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

PR_TYPES = ("feature", "bugfix", "refactor", "tests", "docs")
FAIL_BEFORE_TYPES = {"feature", "bugfix"}
STRUCTURAL_ERRORS = {"ImportError", "ModuleNotFoundError", "NameError", "AttributeError"}
FAILING_VERDICTS = {"meaningless", "no_tests", "unexpected_failure"}
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
MESSAGE_PREFIX = re.compile(r"^\s*([A-Za-z_][\w.]*):")
LAST_LINE_EXCEPTION = re.compile(r":\s*([A-Za-z_][\w.]*)\s*$")


def parse_pr_type(body):
    """Return the type from the `## Type` section, or None when it is missing or unknown."""
    if not body:
        return None
    text = HTML_COMMENT.sub("", body.replace("\r\n", "\n"))
    in_section = False
    for line in text.split("\n"):
        heading = HEADING.match(line)
        if heading:
            if in_section:
                return None
            in_section = heading.group(1).strip().lower() == "type"
            continue
        if in_section and line.strip():
            value = line.strip().lower()
            return value if value in PR_TYPES else None
    return None


def infer_pr_type(changed_files):
    if changed_files and all(path.startswith("tests/") for path in changed_files):
        return "tests"
    if changed_files and all(path.lower().endswith(".md") for path in changed_files):
        return "docs"
    return "feature"


def is_test_module(path):
    name = PurePosixPath(path).name
    return (
        path.startswith("tests/")
        and name.endswith(".py")
        and (name.startswith("test_") or name.endswith("_test.py"))
    )


def exception_name(element):
    """Exception class name of a junit <failure>/<error> element, without the module."""
    match = MESSAGE_PREFIX.match(element.get("message") or "")
    if not match:
        lines = [line for line in (element.text or "").strip().splitlines() if line.strip()]
        match = LAST_LINE_EXCEPTION.search(lines[-1]) if lines else None
    return match.group(1).rsplit(".", 1)[-1] if match else None


def classify_case(testcase):
    if testcase.find("skipped") is not None:
        return "not_run"
    if testcase.find("error") is not None:
        return "failed_error"
    failure = testcase.find("failure")
    if failure is not None:
        return (
            "failed_error" if exception_name(failure) in STRUCTURAL_ERRORS else "failed_assertion"
        )
    return "passed"


def module_path(dotted):
    return dotted.replace(".", "/") + ".py"


def make_test_id(file, classname, name):
    if not file:
        return f"{module_path(classname)}::{name}" if classname else name
    module = file[:-3].replace("/", ".") if file.endswith(".py") else file
    if classname.startswith(module + "."):
        classes = classname[len(module) + 1 :].replace(".", "::")
        return f"{file}::{classes}::{name}"
    return f"{file}::{name}"


def parse_junit(xml_text):
    """Return ({test id: outcome}, {files that failed to collect}) from pytest junit XML."""
    cases, collection_errors = {}, set()
    root = ET.fromstring(xml_text)
    for testcase in root.iter("testcase"):
        file = testcase.get("file") or ""
        classname = testcase.get("classname") or ""
        name = testcase.get("name") or ""
        if not classname and testcase.find("error") is not None:
            collection_errors.add(file or module_path(name))
            continue
        cases[make_test_id(file, classname, name)] = classify_case(testcase)
    return cases, collection_errors


def combine(before, after):
    """Merge two parse_junit results into the per-test list for the report."""
    before_cases, before_broken = before
    after_cases, after_broken = after
    ids = sorted(set(before_cases) | set(after_cases))
    tests = []
    for tid in ids:
        file = tid.split("::", 1)[0]
        tests.append(
            {
                "id": tid,
                "before": "failed_error"
                if file in before_broken
                else before_cases.get(tid, "not_run"),
                "after": "failed_error"
                if file in after_broken
                else after_cases.get(tid, "not_run"),
            }
        )
    known_files = {tid.split("::", 1)[0] for tid in ids}
    for file in sorted((before_broken | after_broken) - known_files):
        tests.append(
            {
                "id": f"{file}::(collection)",
                "before": "failed_error" if file in before_broken else "not_run",
                "after": "failed_error" if file in after_broken else "not_run",
            }
        )
    return tests


def decide(pr_type, src_changed, test_files, tests):
    """Return (expectation, verdict, message) per the contract's test-relevance rules."""
    expectation = "fail_before" if pr_type in FAIL_BEFORE_TYPES else "pass_before"
    if pr_type == "docs":
        return expectation, "skipped", "Docs PR: there is no behavior to test."
    if not src_changed and not test_files:
        return expectation, "skipped", "Nothing under src/ or tests/ changed."

    if expectation == "fail_before":
        if not test_files:
            if src_changed:
                return (
                    expectation,
                    "no_tests",
                    (
                        f"This {pr_type} changes src/ but no test file. Add a test that fails "
                        "on the base code and passes with the change."
                    ),
                )
            return expectation, "skipped", "No code or test changes to check."
        meaningful = [
            t for t in tests if t["before"] == "failed_assertion" and t["after"] == "passed"
        ]
        if meaningful:
            return (
                expectation,
                "ok",
                (
                    f"{len(meaningful)} changed test(s) fail on the base code with an assertion "
                    "and pass on the PR."
                ),
            )
        if any(t["before"] == "failed_error" for t in tests):
            return (
                expectation,
                "undetermined",
                (
                    "The changed tests fail on the base code only because names they use do not "
                    "exist there yet, so this check cannot tell what they verify. The review reads "
                    "the tests instead."
                ),
            )
        return (
            expectation,
            "meaningless",
            ("No changed test fails on the base code, so the tests do not show the change."),
        )

    if not test_files:
        return expectation, "ok", "No test file changed; ci runs the existing tests."
    broken = [t for t in tests if t["before"] != "passed" or t["after"] != "passed"]
    if broken:
        names = ", ".join(t["id"] for t in broken[:3])
        return (
            expectation,
            "unexpected_failure",
            (
                f"A {pr_type} PR should not change behavior, but {len(broken)} changed test(s) "
                f"do not pass on both the base code and the PR: {names}."
            ),
        )
    return expectation, "ok", "Every changed test passes on the base code and on the PR."


def git(*args, cwd=None):
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout


def changed_files(base, head, *paths, diff_filter=None):
    args = ["diff", "--name-only"]
    if diff_filter:
        args.append(f"--diff-filter={diff_filter}")
    args.append(f"{base}...{head}")
    if paths:
        args += ["--", *paths]
    return [line for line in git(*args).splitlines() if line]


def run_pytest(cwd, files, junit_path):
    """Run pytest on `files` in `cwd`; return parse_junit output (empty when nothing ran)."""
    command = [
        sys.executable,
        "-m",
        "pytest",
        *files,
        "-o",
        "junit_family=xunit1",
        f"--junitxml={junit_path}",
        "-p",
        "no:cacheprovider",
        "-q",
        "--no-header",
    ]
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    print(f"$ pytest {' '.join(files)}  (in {cwd}, exit {result.returncode})")
    print("\n".join(result.stdout.strip().splitlines()[-15:]))
    if not Path(junit_path).exists():
        return {}, set()
    return parse_junit(Path(junit_path).read_text(encoding="utf-8"))


def build_report(base, head, pr_body):
    all_changed = changed_files(base, head)
    pr_type = parse_pr_type(pr_body) or infer_pr_type(all_changed)
    src_changed = any(path.startswith("src/") for path in all_changed)
    tests_changed = changed_files(base, head, "tests/", diff_filter="ACMR")
    test_files = [path for path in tests_changed if is_test_module(path)]

    tests = []
    run_tests = test_files and pr_type != "docs"
    if run_tests:
        merge_base = git("merge-base", base, head).strip()
        workdir = Path(tempfile.mkdtemp(prefix="test-relevance-"))
        base_tree = workdir / "base"
        git("worktree", "add", "--detach", str(base_tree), merge_base)
        try:
            for path in tests_changed:
                target = base_tree / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            before = run_pytest(base_tree, test_files, workdir / "before.xml")
            after = run_pytest(Path.cwd(), test_files, workdir / "after.xml")
        finally:
            git("worktree", "remove", "--force", str(base_tree))
            shutil.rmtree(workdir, ignore_errors=True)
        tests = combine(before, after)

    expectation, verdict, message = decide(pr_type, src_changed, test_files, tests)
    return {
        "version": 1,
        "pr_type": pr_type,
        "expectation": expectation,
        "tests": tests,
        "verdict": verdict,
        "message": message,
    }


def summary_markdown(report):
    rows = [f"| {t['id']} | {t['before']} | {t['after']} |" for t in report["tests"]]
    table = "\n".join(["| Test | Base code | PR |", "|---|---|---|", *rows]) if rows else ""
    return (
        f"### Test relevance: {report['verdict']}\n\n"
        f"Type: {report['pr_type']} ({report['expectation']}). {report['message']}\n\n{table}\n"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="base commit (the PR's base sha)")
    parser.add_argument("--head", default="HEAD", help="head commit (checked out in the cwd)")
    parser.add_argument("--output", default="test-relevance.json")
    args = parser.parse_args(argv)

    report = build_report(args.base, args.head, os.environ.get("PR_BODY", ""))
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = summary_markdown(report)
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(summary)
    return 1 if report["verdict"] in FAILING_VERDICTS else 0


if __name__ == "__main__":
    sys.exit(main())
