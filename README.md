# demo-inventory-api

A small inventory API (API Gateway HTTP API, one Python Lambda function, DynamoDB) that serves
as the review target of [pr-triage](https://github.com/jaeneungsim/pr-triage). AI coding agents
open PRs here from the tickets in `tickets/`, and pr-triage reviews each PR before a person does.
The shared names and formats are in pr-triage's `docs/contracts.md`.

## How a PR here gets reviewed

| Check | What it does | Level |
|---|---|---|
| `ci` | ruff, pytest, CDK tests and `cdk synth` | v1+ |
| `test-relevance` | runs the changed tests on the base code: a feature or bug fix needs a test that fails there and passes on the PR | v2+ |
| `behavior` | starts the PR's API with DynamoDB Local and runs the expected behavior from the PR description | v2+ |
| `preview-deploy` | deploys `inv-api-preview-<ticket>` to AWS for PRs from this repo | v3 |
| `pr-triage` | the review result (approvable, changes requested, needs human review), posted by the pr-triage GitHub App | all |

`preview-destroy` removes the ticket stack when the PR closes, and `deploy-dev` deploys
`inv-api-dev` after every merge into `dev`, where pr-triage runs a read-only post-merge check.
The repo variable `TRIAGE_LEVEL` (`v1`, `v2`, `v3`) turns the level-gated checks on or off.
`test-relevance` and `behavior` run their check scripts from the base commit, so a PR cannot
change the checks that judge it.

## Layout

```
src/inventory/handler.py   the Lambda handler (all routes)
tests/                     pytest, with an in-memory table
openapi.yaml               the API contract
tickets/                   ticket files; reviews read them from the dev branch
seed/items.json            seed items 41, 42 and 43 (used locally and by every stack)
scripts/                   local server, seed, expected-behavior runner, test-relevance check
infra/                     CDK app (TypeScript); context `stage` = dev, ci or inv-<n>
.github/                   workflows and the PR template
AGENTS.md                  rules for AI coding agents
```

## Run the tests

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/pytest
cd infra && npm ci && npm test && npx cdk synth -c stage=inv-123
```

## Run the API locally

```sh
docker run --rm -p 8000:8000 amazon/dynamodb-local:3.3.1
.venv/bin/python scripts/seed.py
.venv/bin/python scripts/local_server.py --port 8080 > server.log &
curl http://127.0.0.1:8080/items/42
```

The local server answers CORS preflight requests, so the web demo can run against it.

To try an expected-behavior block the way CI does, put a PR description in a file and run:

```sh
PR_BODY="$(cat pr-body.md)" BASE_URL=http://127.0.0.1:8080 \
  .venv/bin/python scripts/run_behavior.py --log-file server.log
```

## Deploy

The workflows deploy with GitHub OIDC roles that pr-triage's PreviewPlatform stack creates.
Every role the stacks create carries the `inv-preview-boundary` permissions boundary. Set these
repo variables from that stack's outputs (pr-triage's `scripts/configure_demo_repos.sh` does it):
`AWS_REGION`, `PREVIEW_ROLE_ARN`, `DEV_ROLE_ARN`, `CFN_EXEC_ROLE_ARN` and `TRIAGE_LEVEL`.
Deploy jobs skip themselves until the role variables exist.

Tickets: `INV-100` is the base API (done). `INV-123`, `INV-124` and `INV-125` are the demo
tickets that AI agents implement in PRs.
