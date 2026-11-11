# ThoughtLabs CoPilot Manual Test Report

## Status

**Manual authenticated end-to-end testing: Not performed.** No live Firebase account, MongoDB workspace, job-board access, or OpenRouter configuration was used in this validation. Follow the scenarios in [CAREERPILOT_TEST_PLAN.md](./CAREERPILOT_TEST_PLAN.md) before release.

## Automated validation performed

- Candidate/discovery/recommendation tests: **66 passed**, 1 warning.
- Python compile check (`python -m compileall -q app tests`): **passed**.
- Full backend suite: **318 passed, 9 skipped, 2 failed**.
- Frontend production build: **passed** (`tsc -b && vite build`); Vite emitted a chunk-size warning for the 557.49 kB minified JavaScript bundle.
- `git diff --check`: passed.

The two full-suite failures are:

- `tests/test_diagnostics.py::TestDiagnosticArtifactAPI::test_get_run_exposes_has_trace_and_has_screenshot`
- `tests/test_diagnostics.py::TestDiagnosticArtifactAPI::test_state_endpoint_exposes_has_trace_and_has_screenshot`

Both fail because the run/state responses report `has_trace` as false where the tests expect true. These diagnostic-artifact cases are outside the candidate-scoped workflow; this report does not claim whether they predate the change.

## Coverage not established

- Firebase-authenticated candidate selection in a browser.
- Live MongoDB persistence across separate sessions.
- Real job-board discovery and recommendations.
- Live OpenRouter response and candidate-specific resume selection.
- Visual behavior on mobile and desktop with production data.
- Legacy records in the user's actual workspace.

## Known workflow limitation

A saved job/application is associated with one candidate. A conflicting candidate reassignment is rejected; associating the same listing independently with multiple candidates is not supported by this implementation.
