"""Errors the application raises on purpose.

Each one carries an HTTP status and a stable, machine-readable code. The API layer
turns them into the single JSON shape that every error response uses.
"""


class AppError(Exception):
    """Base class: an error the caller can understand and act on."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class FileTooLargeError(AppError):
    status_code = 413
    code = "file_too_large"

    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"The file is larger than the limit of {_readable_size(max_bytes)}.")


class UnsupportedFileTypeError(AppError):
    status_code = 415
    code = "unsupported_file_type"


class EmptyFileError(AppError):
    status_code = 422
    code = "empty_file"


class EncryptedFileError(AppError):
    status_code = 422
    code = "file_encrypted"


class UnreadableFileError(AppError):
    """The file has the right type but is damaged, or is not really that type."""

    status_code = 422
    code = "file_unreadable"


class TooManyPagesError(AppError):
    status_code = 422
    code = "too_many_pages"

    def __init__(self, page_count: int, max_pages: int) -> None:
        super().__init__(f"The file has {page_count} pages. The limit is {max_pages}.")


class NoTextFoundError(AppError):
    status_code = 422
    code = "no_text_found"


class NotImplementedYetError(AppError):
    """A step of the pipeline that has not been built yet."""

    status_code = 501
    code = "not_implemented"


def _readable_size(size_bytes: int) -> str:
    """Format a size for a person: 20971520 becomes "20 MB".

    The value is rounded to one decimal and a trailing ".0" is dropped. Megabytes are
    used whenever the size is at least 1 MB; a smaller size is shown in KB or bytes,
    so that a small limit is never reported as "0 MB".
    """
    for unit, bytes_per_unit in (("MB", 1024 * 1024), ("KB", 1024)):
        if size_bytes >= bytes_per_unit:
            value = f"{size_bytes / bytes_per_unit:.1f}".removesuffix(".0")
            return f"{value} {unit}"
    return f"{size_bytes} bytes"
