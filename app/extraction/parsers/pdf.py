"""The PDF parser."""

import io
from collections.abc import Sequence
from typing import Any

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError
from pdfplumber.page import Page
from pdfplumber.utils.exceptions import PdfminerException

from app.errors import (
    AppError,
    EncryptedFileError,
    NoTextFoundError,
    TooManyPagesError,
    UnreadableFileError,
)
from app.extraction.models import ExtractionWarning, Line, ParsedDocument, SourceFile
from app.extraction.parsers.hidden_text import coloured_regions, is_hidden, is_white
from app.extraction.parsers.noise import flag_page_noise

# Characters whose tops are at most this many points apart are read as one line.
# pdfplumber's default of 3 puts a clause number set in a larger font on a line of its
# own, above its title.
_LINE_Y_TOLERANCE = 5

# How much hidden text a warning quotes.
_MAX_HIDDEN_SAMPLES = 3
_MAX_HIDDEN_SAMPLE_LENGTH = 80

# A line of hidden text and the page it was on.
type HiddenLine = tuple[int, str]


class PdfParser:
    """Reads a PDF with pdfplumber.

    pdfplumber returns the lines of a page in visual order, top to bottom. Numbering
    the lines and finding page headers and footers both depend on that order.
    """

    def __init__(self, max_pages: int) -> None:
        self._max_pages = max_pages

    def parse(self, source: SourceFile) -> ParsedDocument:
        pages, hidden_lines = _read_pages(source.data, self._max_pages)
        noise_flags = flag_page_noise(pages)

        lines: list[Line] = []
        pages_without_text = 0
        for page_number, (texts, flags) in enumerate(zip(pages, noise_flags, strict=True), start=1):
            if all(flags):
                # Nothing on the page but page noise, or nothing at all.
                pages_without_text += 1
            for text, is_noise in zip(texts, flags, strict=True):
                lines.append(
                    Line(number=len(lines) + 1, text=text, page=page_number, is_noise=is_noise)
                )

        if pages_without_text == len(pages):
            raise NoTextFoundError(
                "No text could be read from the file. Scanned documents are not supported."
            )

        warnings: list[ExtractionWarning] = []
        if hidden_lines:
            warnings.append(_hidden_text_warning(hidden_lines))
        if pages_without_text:
            warnings.append(
                ExtractionWarning(
                    code="pages_without_text",
                    message=f"{pages_without_text} of {len(pages)} pages have no readable text. "
                    "They may be blank or contain only images.",
                )
            )
        return ParsedDocument(lines=tuple(lines), page_count=len(pages), warnings=tuple(warnings))


def _read_pages(data: bytes, max_pages: int) -> tuple[list[list[str]], list[HiddenLine]]:
    """Return the visible text lines of every page, and the hidden lines that were left out."""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > max_pages:
                raise TooManyPagesError(len(pdf.pages), max_pages)

            pages: list[list[str]] = []
            hidden_lines: list[HiddenLine] = []
            for page_number, page in enumerate(pdf.pages, start=1):
                visible, hidden = _split_off_hidden_text(page)
                pages.append(_text_lines(visible))
                if hidden is not None:
                    hidden_lines.extend((page_number, text) for text in _text_lines(hidden))
                # Release the page's cached layout, or a long contract keeps all of it in memory.
                page.close()
            return pages, hidden_lines
    except AppError:
        raise
    except PdfminerException as error:
        # pdfplumber wraps the underlying error without chaining it as the cause,
        # so the original is found on `__context__`.
        if isinstance(error.__context__, PDFEncryptionError):
            raise EncryptedFileError(
                "The PDF is password-protected. Remove the password and upload it again."
            ) from error
        raise UnreadableFileError("The PDF is damaged or incomplete.") from error
    except Exception as error:
        # A damaged PDF can fail inside the library in many ways. Whatever the error
        # is, the outcome for the caller is the same: this file cannot be read.
        raise UnreadableFileError("The PDF is damaged or incomplete.") from error


def _split_off_hidden_text(page: Page) -> tuple[Page, Page | None]:
    """Return the page without its hidden text, and a page holding only the hidden text.

    The second value is None when the page has no hidden text, which is the usual case.
    """
    if not any(is_white(char.get("non_stroking_color")) for char in page.chars):
        return page, None

    regions = coloured_regions([*page.rects, *page.curves], page.images)

    def hidden(obj: dict[str, Any]) -> bool:
        return obj["object_type"] == "char" and is_hidden(obj, regions)

    if not any(hidden(char) for char in page.chars):
        return page, None
    return page.filter(lambda obj: not hidden(obj)), page.filter(hidden)


def _text_lines(page: Page) -> list[str]:
    text = page.extract_text(y_tolerance=_LINE_Y_TOLERANCE) or ""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _hidden_text_warning(hidden_lines: Sequence[HiddenLine]) -> ExtractionWarning:
    """Say what was left out, so that nothing disappears from a contract silently."""
    count = "1 line" if len(hidden_lines) == 1 else f"{len(hidden_lines)} lines"
    samples = "; ".join(
        f'page {page_number}: "{_shorten(text)}"'
        for page_number, text in hidden_lines[:_MAX_HIDDEN_SAMPLES]
    )
    remaining = len(hidden_lines) - _MAX_HIDDEN_SAMPLES
    more = f"; and {remaining} more" if remaining > 0 else ""
    return ExtractionWarning(
        code="hidden_text_removed",
        message=f"Hidden text was left out: {count} of white text on a white background "
        f"({samples}{more}).",
    )


def _shorten(text: str) -> str:
    if len(text) <= _MAX_HIDDEN_SAMPLE_LENGTH:
        return text
    return text[: _MAX_HIDDEN_SAMPLE_LENGTH - 1] + "…"
