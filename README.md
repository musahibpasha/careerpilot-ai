<div align="center">

# 🚀 CareerPilot AI

### 🤖 Your AI-Powered Job Hunt Copilot

[![Typing SVG](https://readme-typing-svg.demolab.com?font=Fira+Code&size=21&pause=1000&color=36BCF7&center=true&vCenter=true&width=650&lines=Discover+Better+Opportunities+%F0%9F%8E%AF;Match+Your+Skills+With+AI+%F0%9F%A7%A0;Prepare+Smarter+Applications+%E2%9C%A8;Your+Career%2C+Your+Decisions+%F0%9F%9B%A1%EF%B8%8F)](https://git.io/typing-svg)

**Discover smarter. Match better. Apply with confidence.**

An AI-assisted job-search platform for organizing opportunities, understanding resume-to-job fit, preparing applications, and tracking the job hunt — while keeping important decisions under human control.

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-61DAFB?style=for-the-badge&logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-47A248?style=for-the-badge&logo=mongodb&logoColor=white)
![Status](https://img.shields.io/badge/Status-In%20Development-orange?style=for-the-badge)

[Features](#-features) • [Architecture](#-architecture) • [Roadmap](#-development-roadmap) • [Setup](#-getting-started) • [Safety](#-safety-and-privacy)

</div>

---

## 📌 About the Project

CareerPilot AI is a personal job-hunt assistant designed to make job searching more organized and less repetitive. It brings a candidate profile, resume information, saved jobs, basic matching, and application-status tracking into one workspace.

The long-term goal is to support a human-approved workflow: find relevant opportunities, understand why a role may fit, prepare truthful application materials, and let the user review and approve any application before a submission action.

> **Guiding principle:** AI can assist with research and preparation, but the user stays in control of application decisions.

## ✨ Features

### ✅ Built

- **Candidate profile editor** — save contact details, professional summary, skills, work experience and internships, projects, education, certifications, and achievements.
- **PDF resume upload and parsing** — extract resume information with an AI-assisted parser using OpenRouter.
- **Review before saving** — inspect extracted resume fields and correct them before updating the profile.
- **Required-field validation** — frontend and backend checks for required profile fields.
- **Skill normalization** — treat common variations such as `React`, `React.js`, and `ReactJS` as the same skill for matching.
- **Manual job entry** — add a job with a title and company; a supplied link is validated.
- **Basic duplicate detection** — reject a job when its source ID or link already exists.
- **Basic skill-overlap match score** — calculate an initial score based on normalized skill overlap.
- **Job feed filtering and sorting** — filter by status, minimum match score, and source; sort by best match, newest, or company.
- **Application-status tracking with history** — statuses are Discovered, Saved, Under review, Ready for approval, Approved, Submitted, Interview, Offer, Rejected, and Withdrawn. Every change is recorded with a timestamp and shown in the tracker. Approved and Submitted are locked: the backend refuses them until the approval workflow is built. Applications saved under the older statuses are converted automatically on startup.
- **Job preferences** — save preferred locations, experience level, work arrangement (remote, hybrid, on-site), and salary expectation (minimum, maximum, currency, per year or month). Everything is optional, and target roles stay in the profile. Preferences feed the match score (see Explainable matching).
- **Multiple resumes with an active resume** — in your profile, keep up to 5 PDF resumes (5 MB each), view them, rename them, delete them, and choose one as the active resume (shown with an "Active" badge). A new application records the resume that was active when it was created and shows it in the tracker. Choosing a resume as active (or uploading your first one) reads it with the same AI parser as auto-fill and fills your profile form from it. The reading is kept with the resume, so switching back is instant, and "Re-read" reads it again. Nothing is saved to your profile until you review the form and click Save profile; closing the window without saving undoes it. The "Upload resume (PDF) and auto-fill" button at the top of the profile saves the file under My resumes and fills the form from it (when you already have 5 saved resumes it only fills the form and keeps nothing); "Add a resume" saves the file and fills the form only if it becomes your active resume. Uploading never changes your saved profile by itself. Resume files are stored in your local MongoDB, which is fine on your own machine, but login and protected storage are needed before the app is ever put online.
- **Explainable matching** — each job is scored 0-100 from weighted factors: skills (50, must-have vs nice-to-have), role fit against your target roles (15), location and work style (15), experience level (10) and salary (10). Every factor shows a plain-English reason, missing skills are listed, and a factor that cannot be judged (the job or your preferences do not say enough) is left out instead of counted as a miss. A low/medium/high confidence label shows how much of the picture was judged. Scores update when you save your profile or preferences, or call `POST /api/jobs/rescore`. Salary is only compared when both sides use the same currency. Each job card has a "Why this score?" panel with a bar per factor, the confidence label, missing skills, and the factors that were not counted; the add-job form has an optional "More details" section (nice-to-have skills, work style, level, salary).
- **Application preparation plan** — Match analysis shows a deterministic fit summary, strengths, gaps, resume focus, positioning guidance, and interview prompts based on the saved job and profile. It is guidance, not AI-generated document text.
- **AI cover-letter and resume wording drafts** — from a job's Match analysis, choose **Generate cover letter and resume drafts** to request a tailored cover letter, professional-summary rewrite, relevant saved skills to emphasize, and suggestions for rewriting existing experience/project bullets. Drafts use the saved profile and job posting, are editable in the panel, and are not submitted or saved automatically. Contact details are excluded from the AI request. Review all text for accuracy. The reviewed cover letter can be downloaded as a real PDF containing the candidate's saved contact details and the selected job context.
- **Tailored resume export** — download a printable HTML resume built from your saved profile and the edited AI summary/bullet suggestions. Open the file in a browser and choose Print / Save as PDF. Existing education, work history, projects, and skills are preserved; generated wording is only used where its source still matches the saved profile. The original uploaded PDF and saved profile are not changed.
- **Not interested, with a reason** — dismiss a job from its card and say why (role, skills, location, pay, level, company, other). Dismissed jobs move to a collapsible list with a Restore button, and after two dismissals for the same reason the app points you to the profile or preference setting that controls it. This is only recorded for now; it does not change scores.
- **Paste-a-job import** — in Add job, paste a whole posting and press Fill the form. Rules (not AI) read the title, company, location, work style, level, salary (LPA, lakh, $ ranges, monthly or yearly) and known skills, splitting must-have from nice-to-have. You review the filled form and save; nothing is saved automatically, and details it cannot find are listed so you can add them.
- **Job link import** — in Add job, leave the paste box empty, type a public job posting link in *Job posting URL* and press Fill the form. The backend (`app/job_fetch.py`) opens the page, pulls out the readable text (and the page's structured job data when it has some), then hands it to the same rule-based parser as pasted text, so you get the same kind of draft to review and edit before saving. If you paste text as well, the pasted text is used and the link is only kept as the posting URL. Safety rules: only `http`/`https` on ports 80/443; links to localhost, private, link-local, cloud-metadata and other internal addresses are refused (the name is looked up by the app, every answer is checked, and the connection goes to the checked address, so a name cannot be switched to an internal address afterwards); every redirect is re-checked, at most 5; 8-second total limit, 1 MB size limit, only HTML or plain-text pages; hidden page text and invisible characters are dropped, and text that looks like instructions to an AI is flagged and never followed. It does not log in, solve CAPTCHAs or get around blocking: if a site refuses, needs JavaScript or a login, the message tells you to paste the description instead. No new dependency was added.
- **LinkedIn Jobs search navigation** — Find Jobs offers editable search criteria and can seed role keywords and location from the saved profile and job preferences. It opens LinkedIn's search interface with keywords and location only; other selected filters must be applied manually on LinkedIn. CareerPilot does not retrieve or rank LinkedIn listings, scrape pages, or send the resume to LinkedIn. Manual verification of this feature is pending.
- **Live remote-job discovery (Jobicy)** — Find Jobs searches Jobicy's public, no-key [Jobs API](https://jobicy.com/jobs-rss-feed), which documents use for discovery products and prototypes. Results are transient until saved, then use the existing duplicate checks and match scoring. The API provides remote roles published in the past seven days, keyword search, broad region filters (including APAC and Europe), and cursor pagination. It does not provide India- or city-specific filters; the optional location text is checked locally against each listing's stated eligibility, which may be broad or unavailable. Listings link to Jobicy and display Jobicy attribution as required. Public results link to the Jobicy listing, not a direct employer application URL. No credentials or paid API are used.
- **Safer loading behavior** — the job tracker can still load if the profile request fails.
- **Automated tests** — cover areas including matching, normalization, duplicates, profile validation, resume parsing, status transitions, status history, the approval block, the status migration, job preferences, and resume files. Run `cd backend && python -m pytest -q`.

### 🚧 Planned

Features below are planned, not yet implemented unless explicitly listed in the Built section.

- Using the active uploaded resume directly in job matching and importing the exported resume back into the stored resume library.
- Application-answer drafts, document checklists, and ATS keyword checks.
- Permitted job-source connectors, scheduled scans, and expired-listing checks.
- Playwright-assisted form prefill, with pauses for login, CAPTCHA, or uncertainty.
- A clear application review and approval screen backed by a server-side approval gate.
- Application activity logs and screenshots where appropriate.
- Advanced interview preparation tools beyond the basic prompts in the application plan.
- Dashboard analytics, alerts, and daily digests.
- Login and private user data storage before wider online deployment.
- Further security, privacy, deployment, and reliability improvements.

## 🧭 Development Roadmap

Work is being delivered incrementally so each stage can be tested before moving on.

| Stage | Focus | Current state |
|---|---|---|
| 1 | Stabilize the existing app, database, tests, and basic approval block | ✅ Built |
| 2 | Profile and application tracking | ✅ Profile, preferences, multiple resumes, status history, and Kanban board built |
| 3 | Smart, explainable job matching | ✅ Complete |
| 4 | Job discovery, import, and permitted source connectors | 🟡 Manual entry, posting import, and a Jobicy remote-job connector are available; broader country/city coverage and additional permitted sources remain. |
| 5 | Application preparation and browser-assisted prefill | 🟡 Rule-based prep plans, editable AI cover-letter/resume wording, cover-letter PDF export, and printable tailored-resume export are available; application answers and safe form prefill remain. |
| 6 | Explicit approval workflow and server-side safeguards | 🔜 Planned |
| 7 | Analytics, alerts, interview preparation, authentication, security, and deployment | 🔜 Planned |

### Current implementation checkpoint

- **Current feature:** Downloading a reviewed, job-specific cover letter as PDF from the saved job's Match analysis panel.
- **Status:** The PDF is rendered from the current edited draft and selected job, with contact details from the saved profile. AI draft generation requires an OpenRouter API key. Draft edits are currently held in frontend state and are not persisted across a browser reload; the original resume is unchanged and no application is submitted.
- **LinkedIn Jobs search:** Find Jobs creates an external LinkedIn search link, not retrieved LinkedIn results.
- **Profile handling:** Suggested keywords come only from saved target roles (or saved skills if no roles exist); location and optional preferences can be seeded from saved profile/preferences. The user can edit every criterion before opening LinkedIn.
- **LinkedIn access:** The reviewed official Job Posting API is partner-restricted and is for posting jobs, not searching listings. No listing retrieval, scraping, session access, or application automation is implemented.
- **Preserved integration:** The current workspace has a Jobicy connector, not a Jobify connector. Jobicy remains intact; the existing public job URL importer is unchanged.
- **Automated checks:** `npm run build` passed; `python -m pytest -q` passed (172 tests; one existing Starlette/httpx deprecation warning).
- **Local browser smoke check:** The profile action populated the criteria from the saved profile and updated the encoded LinkedIn URL. No LinkedIn page was opened and no data was saved.
- **Manual confirmation:** Not yet confirmed by the user.
- **Next after user verification:** Complete tailored-resume document downloads beyond printable HTML (PDF/DOCX where feasible), then add persistence/versioning for edited job-specific drafts. Wait for the user to verify this PDF export before starting another feature.

### Suggested implementation order

1. ~~Stage 2: Kanban board, status history, job preferences, multiple resumes.~~ Done.
2. ~~Stage 3: explainable matching and skill-gap insights.~~ Done.
3. Complete search filters and result normalization, including the limits of each source's available data.
4. Continue Stage 5: application-answer drafts, safe form prefill, and active-resume-aware matching.
5. Build Stage 6: a complete approval flow enforced by the backend.
6. Build Stage 7: analytics, notifications, authentication, and production hardening.

## 🏗️ Architecture

```mermaid
flowchart TD
    U[User] --> FE[React + TypeScript frontend]
    FE --> API[FastAPI backend]
    API --> DB[(MongoDB)]
    API --> RES[PDF resume extraction]
    RES --> AI[OpenRouter model]
    AI --> RES
    API --> MATCH[Skill normalization and basic matching]
    API --> JOBICY[Jobicy public remote-jobs API]
    JOBICY --> API
    MATCH --> API
    API --> FE
    FE --> U
```

### Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Frontend | React, TypeScript, Vite | User interface and job-tracking workspace |
| Backend | Python, FastAPI, Uvicorn | API, validation, and application logic |
| Database | MongoDB, PyMongo | Store profile and job-tracking data |
| Resume extraction | pypdf | Extract text from PDF resumes |
| PDF generation | ReportLab | Render reviewed cover letters as downloadable PDFs |
| AI extraction | OpenRouter | Assist with extracting structured profile fields |
| Testing | Python test suite | Validate backend behaviors across profiles, jobs, matching, imports, and application tracking |

## 📂 Project Structure

```text
careerpilot-ai/
├── frontend/
│   └── src/
│       ├── App.tsx
│       └── styles.css
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── jobicy.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── schemas.py
│   │   ├── preferences.py
│   │   ├── resumes.py
│   │   ├── matching.py
│   │   └── workflow.py
│   ├── tests/
│   │   └── test_jobicy.py
│   ├── .env                 # Local secrets; do not commit
│   └── .venv/               # Local virtual environment; do not commit
├── .github/
│   └── workflows/
│       └── snake.yml        # Optional contribution animation workflow
├── .gitignore
└── README.md
```

The exact structure may grow as new features are implemented.

## ⚙️ Getting Started

### Prerequisites

Install the following before running the project:

- Python 3.10 or a compatible newer version.
- Node.js and npm.
- A running MongoDB instance or MongoDB connection string.
- An OpenRouter API key if you want to use AI-assisted resume extraction and application-draft generation.

### 1. Clone the Repository

Replace `musahibpasha` with your GitHub username.

```bash
git clone https://github.com/musahibpasha/careerpilot-ai.git
cd careerpilot-ai
```

### 2. Start the Backend

Open a terminal:

```bash
cd backend
python -m venv .venv
```

On Windows PowerShell, activate the environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create `backend/.env` using the variable names expected by `backend/app/config.py`. Add your own MongoDB connection settings and OpenRouter API key locally.

**Never paste secrets into this README or commit your `.env` file.**

Start the API:

```bash
python -m uvicorn app.main:app --reload
```

If `requirements.txt` does not exist in your current repository yet, add a maintained dependency file based on the project's actual backend dependencies before relying on the install command above.

### 3. Start the Frontend

Open a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the local URL printed by Vite in the terminal.

### 4. Run Tests

From the backend directory, activate the virtual environment and run the project's test command. For a standard pytest setup:

```bash
python -m pytest
```

Use the test command configured in the repository if it differs.

## 🔐 Safety and Privacy

CareerPilot AI is designed around human oversight.

- **No invented qualifications:** generated material should be based on the user's actual profile and resume.
- **Review before saving:** AI-extracted resume fields should be reviewed by the user before being saved.
- **No silent applications:** an application must not be submitted without explicit approval for that specific application.
- **Server-side enforcement matters:** a disabled frontend button alone is not a sufficient approval safeguard; the backend approval gate remains a planned improvement.
- **Respect website controls:** use permitted APIs, feeds, and automation methods. Do not bypass CAPTCHA, login requirements, or anti-bot protections.
- **Pause when uncertain:** automation should stop and ask the user when a page or form is unclear.
- **Protect secrets:** keep API keys, credentials, private resumes, and personal data out of source control and logs.
- **Secure before deployment:** authentication and private per-user storage are planned before broader online use.

The current match score is a basic skill-overlap indicator, not a guarantee of job suitability or hiring outcomes.

## 🧪 Testing

The test suite (run with `cd backend && python -m pytest -q`) covers key areas such as:

- Skill normalization.
- Basic matching.
- Duplicate-job checks.
- Profile required-field validation.
- Resume parsing behavior.
- Application status transitions, timestamped status history, and the migration from the old statuses.
- The application-approval block: `approved` and `submitted` are refused by the backend.
- Cover-letter PDF export: checks the PDF response and verifies the reviewed letter and selected job/profile context in extracted PDF text.
- Job link import (`tests/test_job_fetch.py`, `tests/test_job_import_url.py`): valid public HTML, structured job data, malformed/empty pages, login and CAPTCHA pages, unsupported content types, timeouts, oversized responses, redirect limit, localhost/private/metadata/IPv6 targets, redirects to private addresses, names that resolve to internal addresses, and the pasted-text regression. No test opens a real website.

As features are added, tests should be added for both successful and failure cases. The count may change as the project evolves.

## 🐍 Optional GitHub Contribution Snake

A contribution-snake animation is optional decoration and is separate from the application's functionality.

If the workflow at `.github/workflows/snake.yml` is configured and has successfully published the SVG to the `output` branch, you can display it here. Replace `musahibpasha` with your username:

![GitHub contribution snake](https://raw.githubusercontent.com/musahibpasha/careerpilot-ai/output/github-contribution-grid-snake.svg)

If the image does not appear, check that the workflow ran successfully, that the `output` branch exists, and that the image path matches the generated filename. You can remove this section if you do not want the animation.

## 🛣️ Project Status

CareerPilot AI is an active work in progress. The profile editor, PDF resume parsing and review, explainable matching, job discovery/import, application tracking, editable AI cover-letter and resume-wording drafts, and printable tailored-resume export are implemented. Application answers, browser-assisted workflows, the complete approval gate, analytics, authentication, and production deployment remain on the roadmap.

## 🤝 Contributing

This is currently a personal project. Suggestions, bug reports, and improvements are welcome as the project becomes more mature.

When contributing:

1. Keep changes focused on one feature at a time.
2. Add or update tests for changed behavior.
3. Never commit API keys, `.env` files, private resumes, or personal application data.
4. Preserve the human-approval requirement for any application submission workflow.

## 📄 License

No license has been selected yet. Until a license is added to this repository, do not assume that the code is available for unrestricted reuse.

---

<div align="center">

**Built to make job hunting more organized — with AI assistance and human control.** 🚀

</div>