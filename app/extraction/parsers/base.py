"""The interface every document parser implements."""

from typing import Protocol

from app.extraction.models import ParsedDocument, SourceFile


class DocumentParser(Protocol):
    """Turns one file format into the shared `ParsedDocument`.

    There is one parser per format. Everything format-specific stays inside it:
    how text is read, what counts as a line, and how page noise is recognised.
    """

    def parse(self, source: SourceFile) -> ParsedDocument:
        """Read the file and return its lines in reading order.

        Parsing is CPU-bound and synchronous; callers run it in a worker thread.
        Raises an `AppError` if the file is encrypted, damaged, too long, or has no text.
        """
        ...
