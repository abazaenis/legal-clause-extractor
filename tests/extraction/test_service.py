"""The pipeline hands a file to the parser registered for its type."""

import threading

import pytest

from app.errors import NotImplementedYetError
from app.extraction.file_types import FileType
from app.extraction.models import Line, ParsedDocument, SourceFile
from app.extraction.parsers.factory import ParserFactory
from app.extraction.service import extract_document

SOURCE = SourceFile(filename="contract.pdf", file_type=FileType.PDF, data=b"%PDF-", sha256="x")


class RecordingParser:
    """A parser that returns a fixed document and remembers how it was called."""

    def __init__(self, document: ParsedDocument) -> None:
        self.document = document
        self.sources: list[SourceFile] = []
        self.thread_id: int | None = None

    def parse(self, source: SourceFile) -> ParsedDocument:
        self.sources.append(source)
        self.thread_id = threading.get_ident()
        return self.document


async def test_file_is_parsed_by_the_parser_for_its_type() -> None:
    document = ParsedDocument(
        lines=(Line(number=1, text="Header", page=1, is_noise=True), Line(2, "1. Term", page=1)),
        page_count=1,
    )
    parser = RecordingParser(document)

    # Temporary: the pipeline stops after parsing, and reports what it parsed.
    with pytest.raises(NotImplementedYetError, match="2 lines on 1 pages, 1 flagged as page noise"):
        await extract_document(SOURCE, ParserFactory({FileType.PDF: parser}))

    assert parser.sources == [SOURCE]


async def test_document_without_pages_goes_through_the_same_pipeline() -> None:
    document = ParsedDocument(lines=(Line(number=1, text="1. Term"),))
    parser = RecordingParser(document)

    with pytest.raises(NotImplementedYetError, match=r"\(1 lines, 0 flagged as page noise\)"):
        await extract_document(SOURCE, ParserFactory({FileType.PDF: parser}))


async def test_parsing_runs_in_a_worker_thread() -> None:
    """Parsing is CPU-bound, so it must not run on the thread of the event loop."""
    parser = RecordingParser(ParsedDocument(lines=(Line(number=1, text="1. Term"),)))

    with pytest.raises(NotImplementedYetError):
        await extract_document(SOURCE, ParserFactory({FileType.PDF: parser}))

    assert parser.thread_id is not None
    assert parser.thread_id != threading.get_ident()
