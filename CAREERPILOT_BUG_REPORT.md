# ThoughtLabs CoPilot Bug and Limitation Report

## Test failures

### Diagnostic artifact flags missing in two API tests

- **Severity:** Medium (test-suite issue; release impact not assessed)
- **Affected tests:** `tests/test_diagnostics.py::TestDiagnosticArtifactAPI::test_get_run_exposes_has_trace_and_has_screenshot` and `tests/test_diagnostics.py::TestDiagnosticArtifactAPI::test_state_endpoint_exposes_has_trace_and_has_screenshot`
- **Observed:** Full backend suite returns `has_trace: false`; tests expect `true`.
- **Reproduction:** Run `pytest -q` from `backend`.
- **Candidate workflow relevance:** No direct relationship identified.
- **Disposition:** Not changed as part of this candidate-scoping work. The root cause and whether this is pre-existing have not been established.

## Known implementation limitation

- Each saved job/application has at most one candidate association. Attempts to switch an existing association are rejected rather than silently changing ownership. Multi-candidate tracking for the same listing would require a separate association model and uniqueness rules.

## Not confirmed as bugs

- Authenticated UI behavior, real MongoDB persistence, and live job-board/provider behavior were not manually tested because corresponding external credentials/services were not used.
- Scanned PDFs without selectable text require OCR, as documented by the existing resume parser.
