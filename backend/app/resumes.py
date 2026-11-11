"""Resume validation and text extraction helpers, independent of API/database code."""

import re
from io import BytesIO
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader
#resumes limitations
MAX_RESUMES = 50
MAX_CANDIDATE_RESUMES = 5
MAX_RESUME_BYTES = 5 * 1024 * 1024
MAX_LABEL_LENGTH = 100
MAX_DOCX_XML_BYTES = 10 * 1024 * 1024


def clean_label(label: str, filename: str) -> str:
    """Use the label if given, otherwise the file name without '.pdf'."""
    text = " ".join(str(label or "").split())
    if text:
        return text
    stem = re.sub(r"\.(pdf|docx)$", "", str(filename or "").strip(), flags=re.IGNORECASE)
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


def safe_resume_filename(filename: str) -> str:
    """Keep the original supported extension in a safe download filename."""
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", str(filename or "").strip())
    name = name.strip(" ._") or "resume"
    extension = ".docx" if name.lower().endswith(".docx") else ".pdf"
    stem = re.sub(r"\.(pdf|docx)$", "", name, flags=re.IGNORECASE).strip(" ._")
    return f"{stem[:145] or 'resume'}{extension}"


def extract_docx_text(data: bytes) -> tuple[str, list[str]]:
    """Extract plain text from the DOCX main part without unpacking to disk."""
    if not data:
        return "", ["The file is empty"]
    if len(data) > MAX_RESUME_BYTES:
        return "", [
            f"The file is larger than {MAX_RESUME_BYTES // (1024 * 1024)} MB"
        ]
    try:
        with ZipFile(BytesIO(data)) as archive:
            document = archive.getinfo("word/document.xml")
            if document.file_size > MAX_DOCX_XML_BYTES:
                return "", ["The document contains too much text to process"]
            root = ElementTree.fromstring(archive.read(document))
    except (BadZipFile, KeyError, ElementTree.ParseError, OSError):
        return "", ["This DOCX file could not be read. Try exporting it again"]

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(
            node.text or "" for node in paragraph.iter(f"{namespace}t")
        ).strip()
        if text:
            paragraphs.append(text)
    if not paragraphs:
        return "", ["No readable text was found in this DOCX file"]
    return "\n".join(paragraphs), []


def check_resume_file(
    filename: str,
    data: bytes,
    *,
    allow_docx: bool = False,
) -> tuple[int, list[str]]:
    extension = str(filename or "").lower()
    if extension.endswith(".pdf"):
        return check_pdf(filename, data)
    if extension.endswith(".docx") and allow_docx:
        _, problems = extract_docx_text(data)
        if not data.startswith(b"PK"):
            problems.insert(0, "This does not look like a real DOCX file")
        return 0, problems
    return 0, ["Only PDF files are supported"]


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