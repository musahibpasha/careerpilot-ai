"""Rules for the resume files you keep. No FastAPI or database imports here,
so these rules are easy to test on their own.

You can keep several PDF resumes and choose one as the active resume. A new
application records the resume that was active when it was created.

Privacy: these files contain personal details. They are stored in your local
MongoDB, which is fine while the app only runs on your own machine. Login and
protected storage (Stage 7) are needed before it is ever put online."""

import re
from io import BytesIO

from pypdf import PdfReader

MAX_RESUMES = 5
MAX_RESUME_BYTES = 5 * 1024 * 1024
MAX_LABEL_LENGTH = 100


def clean_label(label: str, filename: str) -> str:
    """Use the label if given, otherwise the file name without '.pdf'."""
    text = " ".join(str(label or "").split())
    if text:
        return text
    stem = re.sub(r"\.pdf$", "", str(filename or "").strip(), flags=re.IGNORECASE)
    return " ".join(stem.split())


def label_problems(label: str, other_labels: list[str]) -> list[str]:
    problems: list[str] = []
    if not label:
        problems.append("Give this resume a name")
    elif len(label) > MAX_LABEL_LENGTH:
        problems.append(f"Name is too long (max {MAX_LABEL_LENGTH} characters)")
    elif label.casefold() in {" ".join(o.split()).casefold() for o in other_labels}:
        problems.append(f"A resume called '{label}' already exists")
    return problems


def safe_filename(filename: str) -> str:
    """A file name that is safe to put in a download header."""
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", str(filename or "").strip())
    name = name.strip(" ._") or "resume"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name[:150]


def check_pdf(filename: str, data: bytes) -> tuple[int, list[str]]:
    """Return (page count, problems). An empty problems list means it is a usable PDF."""
    problems: list[str] = []

    if not str(filename or "").lower().endswith(".pdf"):
        problems.append("Only PDF files are supported")
    if len(data) == 0:
        return 0, problems + ["The file is empty"]
    if len(data) > MAX_RESUME_BYTES:
        problems.append(
            f"The file is larger than {MAX_RESUME_BYTES // (1024 * 1024)} MB"
        )
    if not data.startswith(b"%PDF-"):
        problems.append("This does not look like a real PDF file")
        return 0, problems
    if problems:
        return 0, problems

    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            return 0, ["Password-protected PDFs are not supported"]
        pages = len(reader.pages)
    except Exception:
        return 0, ["This PDF could not be read. Try exporting it again"]

    if pages == 0:
        return 0, ["This PDF has no pages"]
    return pages, []