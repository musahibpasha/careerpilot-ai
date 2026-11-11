# CareerPilot AI Candidate-Scoped Workflow Test Plan

## Scope

Verify that recruiter job discovery, recommendations, saved jobs, and application preparation use an explicitly selected MongoDB candidate while preserving legacy jobs that have no candidate association.

## Environment and test data

- Start the backend and frontend using the repository setup instructions.
- Configure Firebase authentication, MongoDB, and any required job-board/provider credentials in a local environment; do not use production credentials for testing.
- Use a test recruiter account with at least two candidates whose skills, target roles, locations, and resumes differ.
- Include one archived candidate and one legacy job/application without `candidate_id`.
- Do not use real applicant personal data unless authorized.

## Automated coverage

Run from `backend`:

```powershell
pytest -q tests/test_candidates.py tests/test_recommendations.py tests/test_jobspy_search.py
python -m compileall -q app tests
```

Run from `frontend`:

```powershell
npm.cmd run build
```

Run the complete backend suite when validating the repository:

```powershell
pytest -q
```

## Manual scenarios

1. Sign in as a recruiter and open Dashboard and Find Jobs. Confirm the candidate selector and candidate-name search display the recruiter's real, non-archived candidates.
2. Select candidate A, search for jobs, and confirm the search criteria and recommendations use candidate A's profile and attached resume details.
3. Switch to candidate B. Confirm previous search results/recommendations are cleared and any new results reflect candidate B rather than candidate A.
4. Search the candidate selector by partial name and confirm only matching candidates remain selectable.
5. Try discovery/recommendations without a candidate, with a nonexistent candidate ID, and with an archived candidate ID. Confirm the API responds with an explicit validation/not-found error rather than falling back to recruiter profile data.
6. Save a discovered job and confirm its candidate association persists after refresh. Inspect saved jobs and application cards for the associated candidate name.
7. Attempt to associate that saved job/application with a different candidate. Confirm the API rejects the conflicting reassignment and preserves existing data.
8. Open application preparation and the Application Assistant for a candidate-linked application. Confirm candidate profile data and only that candidate's resumes are offered.
9. Exercise the legacy path with an unassociated saved job/application. Confirm the existing profile-based behavior remains available.
10. Inspect existing candidate records, saved jobs, application history, and status history before and after the scenarios. Confirm unrelated records are unchanged.
11. At a narrow mobile viewport, verify candidate selection, search, result cards, and application controls remain usable.

## Acceptance criteria

- Candidate-specific searches and recommendations use the selected candidate, not the recruiter profile.
- A candidate switch cannot display stale results attributed to the newly selected candidate.
- Candidate associations persist and cannot be silently transferred.
- Application Assistant only offers resumes belonging to the linked candidate.
- Legacy unassociated records remain readable and retain their prior workflow.
- Existing application history and candidate records are preserved.
- Error, empty, loading, and responsive states are usable and do not imply false success.
