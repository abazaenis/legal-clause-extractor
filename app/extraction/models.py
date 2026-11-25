"""Plain data types passed between the steps of the extraction pipeline."""

from dataclasses import dataclass, field

from app.extraction.file_types import FileType


@dataclass(frozen=True, slots=True)
class SourceFile:
    """An uploaded file that passed the upload checks and is ready to be parsed."""

    filename: str
    file_type: FileType
    # Kept out of the repr so contract content never ends up in a log line or traceback.
    data: bytes = field(repr=False)
    # Identifies the file: the same bytes always give the same hash.
    sha256: str

    @property
    def size_bytes(self) -> int:
        return len(self.data)


@dataclass(frozen=True, slots=True)
class Line:
    """One line of a document, in reading order.

    A line is the unit a format is read in: a visual line of a PDF page, or a
    paragraph of a Word document. Line numbers are assigned once by the parser and
    never change, so they can be used to point back into the source.
    """

    # One-based and unique within the document.
    number: int
    text: str
    # One-based page the line is on. None for formats that have no pages.
    page: int | None = None
    # True for running headers, footers and page numbers. Such a line stays in the
    # document, but is left out of the prompt and of the clause text.
    is_noise: bool = False


@dataclass(frozen=True, slots=True)
class ExtractionWarning:
    """Something the caller should know about a result that is otherwise valid."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """What every parser produces, whatever the file format.

    The rest of the pipeline only ever sees this type, so it never needs to know
    whether the file was a PDF or something else.
    """

    lines: tuple[Line, ...]
    # None for formats that have no pages.
    page_count: int | None = None
    warnings: tuple[ExtractionWarning, ...] = ()

    @property
    def content_lines(self) -> tuple[Line, ...]:
        """The lines that carry the document's own text, without page noise."""
        return tuple(line for line in self.lines if not line.is_noise)
