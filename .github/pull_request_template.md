## Ticket
INV-000

## Type
<!-- one of: feature, bugfix, refactor, tests, docs -->
feature

## Summary
<!-- One or two sentences: what changes and why. -->

## Expected behavior
<!--
Derive the items from the ticket requirements (R1, R2, ...), not from the code.
Every item lists the requirements it covers. Item types:
  api: request "<METHOD> <path>", expect status, body_has, body_not_has, body_includes
  log: after <api item id>, expect no_level: ERROR or has_message
  ui:  step (what a person checks), optional page
Update this block whenever you push more commits.
-->
```yaml expected-behavior
- id: B1
  covers: [R1]
  type: api
  request: GET /items/42
  expect:
    status: 200
    body_has: [id]
- id: B2
  covers: [R1]
  type: log
  after: B1
  expect:
    no_level: ERROR
```
