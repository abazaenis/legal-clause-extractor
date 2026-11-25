"""The factory picks a parser by file type, and every parser returns the same shape."""

import pytest

from app.errors import UnsupportedFileTypeError
from app.extraction.file_types import FileType
from app.extraction.models import Line, ParsedDocument, SourceFile
from app.extraction.parsers.docx import DocxParser
from app.extraction.parsers.factory import ParserFactory, build_parser_factory
from app.extraction.parsers.pdf import PdfParser


class ParagraphParser:
    """A stand-in for a format without pages, such as a Word document.

    It does not inherit from anything: matching the interface is enough.
    """

    def parse(self, source: SourceFile) -> ParsedDocument:
        paragraphs = source.data.decode().split("\n")
        return ParsedDocument(
            lines=tuple(Line(number=index, text=text) for index, text in enumerate(paragraphs, 1))
        )


def test_application_factory_has_a_parser_for_every_supported_file_type() -> None:
    factory = build_parser_factory(max_pages=300)

    assert isinstance(factory.for_file_type(FileType.PDF), PdfParser)
    assert isinstance(factory.for_file_type(FileType.DOCX), DocxParser)
    # A file type that is recognised must be readable.
    for file_type in FileType:
        factory.for_file_type(file_type)


def test_file_type_without_a_parser_is_unsupported() -> None:
    factory = ParserFactory({})

    with pytest.raises(UnsupportedFileTypeError, match="No parser is available for pdf files"):
        factory.for_file_type(FileType.PDF)


def test_new_format_only_needs_a_parser_and_a_registration() -> None:
    factory = ParserFactory({FileType.PDF: ParagraphParser()})
    source = SourceFile(
        filename="contract.txt", file_type=FileType.PDF, data=b"1. Term\n2. Fees", sha256="x"
    )

    document = factory.for_file_type(source.file_type).parse(source)

    # The same shared output as the PDF parser, just without pages.
    assert document.page_count is None
    assert document.lines == (Line(number=1, text="1. Term"), Line(number=2, text="2. Fees"))
    assert all(line.page is None for line in document.lines)
