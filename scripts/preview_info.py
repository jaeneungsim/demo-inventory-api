"""Turn `cdk deploy --outputs-file` output into preview.json for the PR artifact."""

import argparse
import json
import sys
from pathlib import Path


def stack_name(stage):
    return "inv-api-dev" if stage == "dev" else f"inv-api-preview-{stage}"


def preview_info(outputs, stage):
    stack = stack_name(stage)
    try:
        api_url = outputs[stack]["ApiUrl"]
    except KeyError:
        raise SystemExit(f"No ApiUrl output for {stack} in the outputs file.") from None
    return {"stage": stage, "stack": stack, "api_url": api_url}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", required=True, help="file written by cdk --outputs-file")
    parser.add_argument("--stage", required=True)
    args = parser.parse_args(argv)
    outputs = json.loads(Path(args.outputs).read_text(encoding="utf-8"))
    print(json.dumps(preview_info(outputs, args.stage), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
