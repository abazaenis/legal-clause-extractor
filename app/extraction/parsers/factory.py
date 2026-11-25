"""Picks the parser for a file type."""

from collections.abc import Mapping

from app.errors import UnsupportedFileTypeError
from app.extraction.file_types import FileType
from app.extraction.parsers.base import DocumentParser
from app.extraction.parsers.docx import DocxParser
from app.extraction.parsers.pdf import PdfParser


class ParserFactory:
    """Hands out the parser registered for a file type.

    Supporting a new format takes one parser class and one entry in
    `build_parser_factory`. Nothing else in the pipeline changes, because every
    parser returns the same `ParsedDocument`.
    """

    def __init__(self, parsers: Mapping[FileType, DocumentParser]) -> None:
        self._parsers = dict(parsers)

    def for_file_type(self, file_type: FileType) -> DocumentParser:
        try:
            return self._parsers[file_type]
        except KeyError:
            raise UnsupportedFileTypeError(
                f"No parser is available for {file_type.value} files."
            ) from None


def build_parser_factory(*, max_pages: int) -> ParserFactory:
    """The factory the application uses, with every supported format registered."""
    return ParserFactory(
        {
            FileType.PDF: PdfParser(max_pages=max_pages),
            FileType.DOCX: DocxParser(max_pages=max_pages),
        }
    )
