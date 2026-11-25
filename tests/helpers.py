"""Helpers shared by the tests."""

import io
from collections.abc import Callable, Sequence

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
