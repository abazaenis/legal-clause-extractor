"""Helpers shared by the tests."""

import io
import zipfile
from collections.abc import Callable, Mapping, Sequence
from xml.sax.saxutils import escape

from fastapi import FastAPI
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.config import Settings
from tests.fakes import FakeLLMProvider

# What the `build_app` fixture of the API tests returns: a function that builds an
# application with these settings, talking to this fake LLM and the test database.
AppBuilder = Callable[[Settings, FakeLLMProvider], FastAPI]


def draw_pdf(draw: Callable[[canvas.Canvas], None]) -> bytes:
    """Build a one-page PDF drawn directly on the canvas.

    For cases the line-based builder below cannot express, such as coloured text,
    shapes behind the text, or mixed font sizes.
    """
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    draw(pdf)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def build_pdf(
    pages: Sequence[Sequence[str]],
    *,
    header: str | None = None,
    footer: str | None = None,
    password: str | None = None,
) -> bytes:
    """Build a real PDF in memory.

    The sample contracts are not part of the repository, so the tests generate their
    own files. `pages` holds the text lines of each page; an empty list is a blank
    page. `footer` may use `{page}` and `{pages}`.
    """
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, encrypt=password)
    width, height = A4
    for page_number, lines in enumerate(pages, start=1):
        if header:
            pdf.drawString(72, height - 40, header)
        y = height - 90
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 16
        if footer:
            pdf.drawCentredString(width / 2, 30, footer.format(page=page_number, pages=len(pages)))
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


# --- Word documents ----------------------------------------------------------------------------

WORD_NAMESPACES = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
)

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELATIONSHIPS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Target="word/document.xml" Type="http://schemas.openxmlformats.org/'
    'officeDocument/2006/relationships/officeDocument"/>'
    "</Relationships>"
)


def build_docx(
    body: str,
    *,
    numbering: str | None = None,
    styles: str | None = None,
    parts: Mapping[str, bytes] | None = None,
) -> bytes:
    """Build a real Word file in memory.

    `body` is what goes inside the body of the document, written the way Word stores
    it (see `docx_paragraph` and `docx_table`). `numbering` and `styles` are the
    contents of those two parts. `parts` adds or replaces any part of the package as
    raw bytes, for tests of damaged or hostile files.
    """
    files: dict[str, bytes] = {
        "[Content_Types].xml": _CONTENT_TYPES.encode(),
        "_rels/.rels": _RELATIONSHIPS.encode(),
        "word/document.xml": _word_part("document", f"<w:body>{body}</w:body>"),
    }
    if numbering is not None:
        files["word/numbering.xml"] = _word_part("numbering", numbering)
    if styles is not None:
        files["word/styles.xml"] = _word_part("styles", styles)
    files.update(parts or {})

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def docx_paragraph(
    text: str = "",
    *,
    style: str | None = None,
    list_id: int | None = None,
    level: int | None = None,
) -> str:
    """One paragraph. `list_id` and `level` put it in a numbered list directly."""
    properties = f'<w:pStyle w:val="{style}"/>' if style is not None else ""
    if list_id is not None or level is not None:
        setting = f'<w:ilvl w:val="{level}"/>' if level is not None else ""
        setting += f'<w:numId w:val="{list_id}"/>' if list_id is not None else ""
        properties += f"<w:numPr>{setting}</w:numPr>"
    wrapped_properties = f"<w:pPr>{properties}</w:pPr>" if properties else ""
    return f"<w:p>{wrapped_properties}{docx_run(text) if text else ''}</w:p>"


def docx_run(text: str, properties: str = "") -> str:
    """A run of text inside a paragraph, with optional run properties."""
    wrapped_properties = f"<w:rPr>{properties}</w:rPr>" if properties else ""
    return f'<w:r>{wrapped_properties}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def docx_table(*rows: Sequence[str]) -> str:
    """A table. Every cell holds one paragraph with the given text."""
    return (
        "<w:tbl>"
        + "".join(
            "<w:tr>" + "".join(f"<w:tc>{docx_paragraph(cell)}</w:tc>" for cell in row) + "</w:tr>"
            for row in rows
        )
        + "</w:tbl>"
    )


def _word_part(root: str, content: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:{root} {WORD_NAMESPACES}>{content}</w:{root}>"
    ).encode()
