"""The Word (.docx) parser."""

import io
import math
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from xml.etree import ElementTree
from xml.etree.ElementTree import Element
from xml.parsers import expat

from app.errors import (
    AppError,
    NoTextFoundError,
    TooManyPagesError,
    UnpackedFileTooLargeError,
    UnreadableFileError,
)
from app.extraction.models import ExtractionWarning, Line, ParsedDocument, SourceFile
from app.extraction.parsers.docx_numbering import BULLET, NumberLabels, W, is_on
from app.extraction.parsers.noise import flag_body_page_numbers
from app.extraction.text import shorten, squeeze

# The part of the package that holds the text. Every program that writes Word files
# puts it here.
MAIN_PART = "word/document.xml"
_STYLES_PART = "word/styles.xml"
_NUMBERING_PART = "word/numbering.xml"

# A .docx is a ZIP file, and a small ZIP file can unpack to gigabytes. No real
# contract comes near these limits.
_MAX_PARTS = 10_000
_MAX_PART_BYTES = 50 * 1024 * 1024

# A Word document has no pages, so its length is estimated from its text.
CHARACTERS_PER_PAGE = 3_000

_MAX_HIDDEN_SAMPLES = 3
_DAMAGED = "The Word document is damaged or incomplete."

_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_PARAGRAPH = f"{W}p"
_TABLE = f"{W}tbl"
_ROW = f"{W}tr"
_CELL = f"{W}tc"
_RUN = f"{W}r"
_CONTENT_CONTROL = f"{W}sdt"
_CONTENT_CONTROL_CONTENT = f"{W}sdtContent"
# Elements that only wrap other content.
_WRAPPERS = frozenset({f"{W}customXml", f"{W}smartTag", f"{W}ins", f"{W}moveTo"})
_INLINE_WRAPPERS = _WRAPPERS | {f"{W}hyperlink", f"{W}fldSimple", f"{W}dir", f"{W}bdo"}
# Tracked changes: text someone added, and text someone took out.
_INSERTIONS = frozenset({f"{W}ins", f"{W}moveTo"})
_DELETIONS = frozenset({f"{W}del", f"{W}moveFrom"})
_SPACES = frozenset({f"{W}tab", f"{W}ptab", f"{W}cr"})
_NOTE_REFERENCES = frozenset({f"{W}footnoteReference", f"{W}endnoteReference"})
_DRAWINGS = frozenset({f"{W}drawing", f"{W}pict", f"{W}object", f"{_MC}AlternateContent"})
_TEXT_BOX = f"{W}txbxContent"
_BLOCKS = frozenset({_PARAGRAPH, _TABLE})


class DocxParser:
    """Reads a Word document with the standard library.

    A line is a paragraph, with the number Word shows in front of it, or a row of a
    table. A Word document has no pages: where a page ends is decided by the program
    that displays it. So the lines carry no page, and the document no page count.
    """

    def __init__(self, max_pages: int) -> None:
        self._max_pages = max_pages

    def parse(self, source: SourceFile) -> ParsedDocument:
        package = _read_package(source.data)
        labels = NumberLabels(package.numbering, package.styles)
        body = _BodyReader(labels)
        body.read(package.body)

        texts = [entry.text for entry in body.entries]
        noise_flags = flag_body_page_numbers(texts, [not entry.in_table for entry in body.entries])
        lines = tuple(
            Line(number=number, text=text, is_noise=is_noise)
            for number, (text, is_noise) in enumerate(zip(texts, noise_flags, strict=True), start=1)
        )

        characters = sum(len(line.text) for line in lines if not line.is_noise)
        if characters == 0:
            raise NoTextFoundError("No text could be read from the Word document.")
        estimated_pages = math.ceil(characters / CHARACTERS_PER_PAGE)
        if estimated_pages > self._max_pages:
            raise TooManyPagesError(estimated_pages, self._max_pages, estimated=True)

        return ParsedDocument(lines=lines, warnings=tuple(_warnings(body, labels)))


# --- Opening the file ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Package:
    """The parts of a Word file that the parser reads."""

    body: Element
    styles: Element | None
    numbering: Element | None


def _read_package(data: bytes) -> _Package:
    """Open the ZIP file and read the three parts, without trusting what is in them."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > _MAX_PARTS:
                raise UnreadableFileError(_DAMAGED)
            document = _read_part(archive, MAIN_PART)
            styles = _read_part(archive, _STYLES_PART)
            numbering = _read_part(archive, _NUMBERING_PART)
    except AppError:
        raise
    except Exception as error:
        # A damaged file can fail in the ZIP reader or in the XML parser in many ways.
        # Whatever the error is, the outcome for the caller is the same.
        raise UnreadableFileError(_DAMAGED) from error

    body = None
    if document is not None and document.tag == f"{W}document":
        body = document.find(f"{W}body")
    if body is None:
        raise UnreadableFileError(
            "The file is not a standard Word document. Save it as .docx or as a PDF and "
            "upload it again."
        )
    return _Package(body=body, styles=styles, numbering=numbering)


def _read_part(archive: zipfile.ZipFile, name: str) -> Element | None:
    """Read and parse one part. None if the package does not have it."""
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    # The size the directory declares is checked first. The read is limited as well,
    # because the directory is written by whoever made the file.
    if info.file_size > _MAX_PART_BYTES:
        raise UnpackedFileTooLargeError(_MAX_PART_BYTES)
    with archive.open(info) as part:
        content = part.read(_MAX_PART_BYTES + 1)
    if len(content) > _MAX_PART_BYTES:
        raise UnpackedFileTooLargeError(_MAX_PART_BYTES)
    _refuse_document_type_declaration(content)
    return ElementTree.fromstring(content)


class _DeclaresDocumentType(Exception):
    pass


class _RootReached(Exception):
    pass


def _refuse_document_type_declaration(content: bytes) -> None:
    """Refuse XML that declares a document type.

    A document type declaration is where entities are defined, and entities are how a
    few hundred bytes of XML are made to expand into gigabytes. No program writes one
    into a Word file. The check uses the XML parser's own hook, so it does not depend
    on how the file is encoded, and it stops at the first element: a declaration can
    only come before it.
    """

    def refuse(
        name: str, system_id: str | None, public_id: str | None, has_internal_subset: bool
    ) -> None:
        raise _DeclaresDocumentType

    def stop(name: str, attributes: dict[str, str]) -> None:
        raise _RootReached

    checker = expat.ParserCreate()
    checker.StartDoctypeDeclHandler = refuse
    checker.StartElementHandler = stop
    try:
        checker.Parse(content, True)
    except _RootReached:
        return
    except _DeclaresDocumentType:
        raise UnreadableFileError(
            "The Word document declares a document type, which no Word file does. It was not read."
        ) from None


# --- Reading the body ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Entry:
    """One line to be: a paragraph, or a row of a table."""

    text: str
    in_table: bool


class _BodyReader:
    """Walks the body of the document in reading order and collects its lines."""

    def __init__(self, labels: NumberLabels) -> None:
        self._labels = labels
        self.entries: list[_Entry] = []
        self.hidden_texts: list[str] = []
        self.tracked_changes = 0
        self.text_boxes = 0
        self.notes = 0

    def read(self, body: Element) -> None:
        self._read_blocks(body, cell=None)

    def _read_blocks(self, container: Element, *, cell: list[str] | None) -> None:
        """Read the paragraphs and tables of a container.

        At the top level each of them becomes a line. Inside a table cell the text is
        collected into `cell`, because the line is the whole row.
        """
        for block in _through_wrappers(container, _BLOCKS):
            if block.tag == _TABLE:
                self._read_table(block, cell)
                continue
            text = self._paragraph_text(block)
            if not text:
                continue
            if cell is None:
                self.entries.append(_Entry(text, in_table=False))
            else:
                cell.append(text)

    def _read_table(self, table: Element, outer_cell: list[str] | None) -> None:
        """A row is one line: its cells from left to right, with a bar between them."""
        for row in _through_wrappers(table, frozenset({_ROW})):
            cells = []
            for cell_element in _through_wrappers(row, frozenset({_CELL})):
                cell: list[str] = []
                self._read_blocks(cell_element, cell=cell)
                if cell:
                    cells.append(" ".join(cell))
            if not cells:
                continue
            text = " | ".join(cells)
            if outer_cell is None:
                self.entries.append(_Entry(text, in_table=True))
            else:
                # A table inside a cell of another table stays inside that cell.
                outer_cell.append(text)

    def _paragraph_text(self, paragraph: Element) -> str:
        """The paragraph as Word shows it: its number, if it has one, and its text."""
        visible: list[str] = []
        hidden: list[str] = []
        self._read_inline(paragraph, visible, hidden)
        if hidden_text := squeeze("".join(hidden)):
            self.hidden_texts.append(hidden_text)

        # Non-breaking spaces, tabs and line breaks all become one plain space.
        text = squeeze("".join(visible))
        # The label is asked for even when the paragraph is empty: Word counts it.
        label = self._labels.next_label(paragraph)
        if label is None or (label == BULLET and not text):
            return text
        return f"{label} {text}".rstrip()

    def _read_inline(self, parent: Element, visible: list[str], hidden: list[str]) -> None:
        for child in parent:
            tag = child.tag
            if tag == _RUN:
                self._read_run(child, visible, hidden)
            elif tag in _INLINE_WRAPPERS:
                if tag in _INSERTIONS:
                    self.tracked_changes += 1
                self._read_inline(child, visible, hidden)
            elif tag == _CONTENT_CONTROL:
                content = child.find(_CONTENT_CONTROL_CONTENT)
                if content is not None:
                    self._read_inline(content, visible, hidden)
            elif tag in _DELETIONS:
                # Deleted text is not read. The document is read as if every tracked
                # change had been accepted.
                self.tracked_changes += 1

    def _read_run(self, run: Element, visible: list[str], hidden: list[str]) -> None:
        properties = run.find(f"{W}rPr")
        # Word's own "hidden" attribute. A reader of the document never sees this text,
        # so the LLM must not see it either.
        is_hidden = properties is not None and is_on(properties.find(f"{W}vanish"))
        target = hidden if is_hidden else visible
        for child in run:
            tag = child.tag
            if tag == f"{W}t":
                target.append(child.text or "")
            elif tag in _SPACES:
                target.append(" ")
            elif tag == f"{W}br":
                # A line break inside a paragraph. A page break is no text at all.
                if child.get(f"{W}type") in (None, "textWrapping"):
                    target.append(" ")
            elif tag == f"{W}noBreakHyphen":
                target.append("-")
            elif tag in _NOTE_REFERENCES:
                self.notes += 1
            elif tag in _DRAWINGS and any(inner.tag == _TEXT_BOX for inner in child.iter()):
                self.text_boxes += 1


def _through_wrappers(parent: Element, wanted: frozenset[str]) -> Iterator[Element]:
    """The children with one of the wanted tags, looking through elements that only wrap.

    Content controls matter most: in one of the sample contracts 117 of the 140
    paragraphs sit inside them.
    """
    for child in parent:
        if child.tag in wanted:
            yield child
        elif child.tag == _CONTENT_CONTROL:
            content = child.find(_CONTENT_CONTROL_CONTENT)
            if content is not None:
                yield from _through_wrappers(content, wanted)
        elif child.tag in _WRAPPERS:
            yield from _through_wrappers(child, wanted)


# --- What the caller is told ---------------------------------------------------------------


def _warnings(body: _BodyReader, labels: NumberLabels) -> list[ExtractionWarning]:
    """Say what was left out and what was added, so that nothing happens silently."""
    warnings = []
    if body.hidden_texts:
        samples = "; ".join(
            f'"{shorten(text)}"' for text in body.hidden_texts[:_MAX_HIDDEN_SAMPLES]
        )
        remaining = len(body.hidden_texts) - _MAX_HIDDEN_SAMPLES
        more = f"; and {remaining} more" if remaining > 0 else ""
        warnings.append(
            ExtractionWarning(
                code="hidden_text_removed",
                message="Hidden text was left out: "
                f"{_count(len(body.hidden_texts), 'paragraph')} with text marked as hidden "
                f"in Word ({samples}{more}).",
            )
        )
    if labels.numbers_computed:
        warnings.append(
            ExtractionWarning(
                code="numbers_computed",
                message="Paragraph numbers that are not written in the text were computed "
                "from the document's numbering settings, the way Word shows them: "
                f"{labels.numbers_computed} in all.",
            )
        )
    if labels.unsupported_formats:
        formats = ", ".join(sorted(labels.unsupported_formats))
        warnings.append(
            ExtractionWarning(
                code="numbering_format_unsupported",
                message=f"Some paragraphs are numbered in a format that is not supported "
                f"({formats}). Their numbers are shown as plain numbers.",
            )
        )
    if body.tracked_changes:
        warnings.append(
            ExtractionWarning(
                code="tracked_changes_found",
                message=f"The document has {_count(body.tracked_changes, 'tracked change')} "
                "that nobody accepted or rejected. It was read as if all of them had been "
                "accepted.",
            )
        )
    if body.text_boxes:
        warnings.append(
            ExtractionWarning(
                code="text_boxes_not_read",
                message=f"The document has {_count(body.text_boxes, 'text box', 'text boxes')}. "
                "Their text was not read.",
            )
        )
    if body.notes:
        notes = _count(body.notes, "footnote or endnote", "footnotes or endnotes")
        warnings.append(
            ExtractionWarning(
                code="footnotes_not_read",
                message=f"The document has {notes}. Their text was not read.",
            )
        )
    return warnings


def _count(number: int, noun: str, plural: str | None = None) -> str:
    return f"1 {noun}" if number == 1 else f"{number} {plural or noun + 's'}"
