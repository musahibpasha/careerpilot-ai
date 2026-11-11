# ThoughtLabs CoPilot Regression Checklist

Legend: **Automated pass** means covered by the focused backend tests or production build; **Manual** means not established in this run.

## Candidate context and discovery

- [x] Candidate-required discovery and recommendation API behavior — automated pass.
- [x] Candidate profile and attached resume data are used for matching — automated pass.
- [x] Missing, invalid, and archived candidate handling — automated pass.
- [x] Saved-job and application candidate association integrity — automated pass.
- [x] Candidate-aware rescoring — automated pass.
- [x] Application Assistant limits resume choices to the linked candidate — automated pass.
- [ ] Candidate selector name search and visual behavior with authenticated records — manual.
- [ ] Candidate changes clear stale UI results and update recommendations — manual.
- [ ] Candidate labels are correct on saved-job and application screens — manual.

## Existing behavior to protect

- [ ] Find Jobs layout and workflow with a real authenticated session — manual.
- [ ] Dashboard recommendations with live data — manual.
- [ ] Legacy unassociated jobs/applications retain prior behavior — manual.
- [ ] Existing MongoDB candidate records and application/status history remain unchanged — manual.
- [ ] Firebase authentication, resume upload, and resume extraction work with configured services — manual.
- [ ] Responsive behavior at desktop and mobile sizes — manual.

## Build and test results

- [x] Focused backend tests: 66 passed.
- [x] Python compile check passed.
- [x] Frontend production build passed (`tsc -b && vite build`); Vite reported a 557.49 kB minified JavaScript chunk, above its 500 kB advisory threshold.
- [ ] Full backend suite: not clean (318 passed, 9 skipped, 2 failures in diagnostic-artifact tests; see [CAREERPILOT_BUG_REPORT.md](./CAREERPILOT_BUG_REPORT.md)).
