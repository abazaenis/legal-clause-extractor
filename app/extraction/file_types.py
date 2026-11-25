"""Recognising a file by its content."""

from enum import StrEnum

_PDF_MARKER = b"%PDF-"
# The PDF specification allows the header anywhere in the first 1024 bytes, and real
# files do start with a byte-order mark or a blank line now and then.
_PDF_HEADER_WINDOW = 1024


class FileType(StrEnum):
    """The file types the service can read."""

    PDF = "pdf"

    @property
    def media_type(self) -> str:
        return _MEDIA_TYPES[self]


_MEDIA_TYPES = {FileType.PDF: "application/pdf"}


def detect_file_type(data: bytes) -> FileType | None:
    """Identify a file from its leading bytes, or return None if it is not supported.

    The file name and the content type sent by the client are never consulted: both
    are whatever the client chooses to claim.
    """
    if _PDF_MARKER in data[:_PDF_HEADER_WINDOW]:
        return FileType.PDF
    return None
