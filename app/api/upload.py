"""Turns an uploaded file into a validated `SourceFile`."""

import hashlib
from pathlib import PurePosixPath

from fastapi import UploadFile

from app.errors import (
    AppError,
    EmptyFileError,
    EncryptedFileError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)
from app.extraction.file_types import (
    detect_file_type,
    is_old_office_file,
    is_password_protected_office_file,
)
from app.extraction.models import SourceFile

_MAX_FILENAME_LENGTH = 255
_DEFAULT_FILENAME = "upload"


async def read_upload(file: UploadFile, *, max_bytes: int) -> SourceFile:
    """Read an uploaded file and run every check that does not need a parser.

    The checks, in order: the size limit, an empty file, and the file type. Checks
    that need the document opened (encryption, page count, a text layer) belong to
    the parser.
    """
    # Reading one byte past the limit is enough to know the file is too large,
    # without ever holding more than that in memory.
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise FileTooLargeError(max_bytes)
    if not data:
        raise EmptyFileError("The uploaded file is empty.")

    file_type = detect_file_type(data)
    if file_type is None:
        raise _unsupported(data)

    return SourceFile(
        filename=clean_filename(file.filename),
        file_type=file_type,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _unsupported(data: bytes) -> AppError:
    """Say why a file is not accepted, as precisely as its content allows."""
    if is_password_protected_office_file(data):
        return EncryptedFileError(
            "The document is password-protected. Remove the password and upload it again."
        )
    if is_old_office_file(data):
        return UnsupportedFileTypeError(
            "Files in the old Office format, such as .doc, are not supported. "
            "Save the document as .docx or as a PDF and upload it again."
        )
    return UnsupportedFileTypeError("Only PDF and Word (.docx) files are supported.")


def clean_filename(raw: str | None) -> str:
    """Reduce the client's file name to a bare name that is safe to store and show.

    The name is never used as a path on this server. Stripping directories and
    capping the length still keeps odd input out of the database and the responses.
    """
    name = PurePosixPath((raw or "").replace("\\", "/")).name.strip()
    if name in {".", ".."}:
        name = ""
    return name[:_MAX_FILENAME_LENGTH] or _DEFAULT_FILENAME
