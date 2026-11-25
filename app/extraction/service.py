"""The extraction pipeline, from a validated file to a stored result."""

from app.errors import NotImplementedYetError
from app.extraction.models import SourceFile


async def extract_document(source: SourceFile) -> None:
    """Run the extraction pipeline for one validated file.

    Only the upload checks exist so far. The duplicate lookup, the parsing, the LLM
    call and the storage of the result are added in later changes.
    """
    raise NotImplementedYetError(
        f"{source.filename} passed the upload checks, but clause extraction is not implemented yet."
    )
