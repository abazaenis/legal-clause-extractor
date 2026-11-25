"""Recognising a file by its content."""

import io
import zipfile
from enum import StrEnum

_PDF_MARKER = b"%PDF-"
# The PDF specification allows the header anywhere in the first 1024 bytes, and real
# files do start with a byte-order mark or a blank line now and then.
_PDF_HEADER_WINDOW = 1024

# A .docx file is a ZIP file. So are spreadsheets, presentations and plain archives.
# What makes it a Word document is the part that holds the text.
_ZIP_MARKER = b"PK\x03\x04"
_WORD_MAIN_PART = "word/document.xml"

# The container of the old Office formats (.doc, .xls, .ppt). A .docx that is
# protected with a password is stored in the same container.
_OLD_OFFICE_MARKER = bytes.fromhex("d0cf11e0a1b11ae1")
# The name of the stream that holds an encrypted document, as the container stores it.
_ENCRYPTED_STREAM = "EncryptedPackage".encode("utf-16-le")


class FileType(StrEnum):
    """The file types the service can read."""

    PDF = "pdf"
    DOCX = "docx"

    @property
    def media_type(self) -> str:
        return _MEDIA_TYPES[self]


_MEDIA_TYPES = {
    FileType.PDF: "application/pdf",
    FileType.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def detect_file_type(data: bytes) -> FileType | None:
    """Identify a file from its content, or return None if it is not supported.

    The file name and the content type sent by the client are never consulted: both
    are whatever the client chooses to claim.
    """
    if _PDF_MARKER in data[:_PDF_HEADER_WINDOW]:
        return FileType.PDF
    if data.startswith(_ZIP_MARKER) and _holds_a_word_document(data):
        return FileType.DOCX
    return None


def is_old_office_file(data: bytes) -> bool:
    """Whether the file is in the container of the old Office formats, such as .doc."""
    return data.startswith(_OLD_OFFICE_MARKER)


def is_password_protected_office_file(data: bytes) -> bool:
    """Whether the file is an Office document that was encrypted with a password."""
    return is_old_office_file(data) and _ENCRYPTED_STREAM in data


def _holds_a_word_document(data: bytes) -> bool:
    # Only the directory of the ZIP file is read here. Nothing is unpacked.
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return _WORD_MAIN_PART in archive.namelist()
    except Exception:
        # Not a ZIP file after all, or a damaged one. Either way it is not supported.
        return False
