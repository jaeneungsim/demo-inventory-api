"""Print the preview stage for a PR as `stage=<value>` (for $GITHUB_OUTPUT).

The stage is the ticket key in lower case, taken from the head branch (HEAD_REF) or,
failing that, from the PR title (PR_TITLE).
"""

import os
import re
import sys

TICKET_KEY = re.compile(r"\bINV-(\d+)\b")


def stage_from(head_ref, title):
    for text in (head_ref, title):
        match = TICKET_KEY.search(text or "")
        if match:
            return f"inv-{match.group(1)}"
    return None


def main():
    stage = stage_from(os.environ.get("HEAD_REF"), os.environ.get("PR_TITLE"))
    if stage is None:
        print("No ticket key (INV-123) in the branch name or the PR title.", file=sys.stderr)
        return 1
    print(f"stage={stage}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
