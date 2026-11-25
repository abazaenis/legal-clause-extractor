"""The extraction pipeline, from a validated file to a stored result."""

import asyncio

from app.errors import NotImplementedYetError
from app.extraction.models import ParsedDocument, SourceFile
from app.extraction.parsers.factory import ParserFactory


async def extract_document(source: SourceFile, parsers: ParserFactory) -> None:
    """Run the extraction pipeline for one validated file.

    The upload checks and the parsing exist so far. The duplicate lookup, the LLM
    call and the storage of the result are added in later changes.
    """
    parser = parsers.for_file_type(source.file_type)
    # Parsing is CPU-bound. A worker thread keeps the event loop free for other requests.
    document = await asyncio.to_thread(parser.parse, source)

    raise NotImplementedYetError(
        f"{source.filename} was parsed ({_describe(document)}), "
        "but clause extraction is not implemented yet."
    )


def _describe(document: ParsedDocument) -> str:
    noise_lines = len(document.lines) - len(document.content_lines)
    pages = "" if document.page_count is None else f" on {document.page_count} pages"
    return f"{len(document.lines)} lines{pages}, {noise_lines} flagged as page noise"
