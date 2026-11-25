"""Error messages are written for a person to read."""

import pytest

from app.errors import FileTooLargeError, TooManyPagesError, UnpackedFileTooLargeError

MB = 1024 * 1024


@pytest.mark.parametrize(
    ("max_bytes", "readable"),
    [
        pytest.param(20 * MB, "20 MB", id="the default limit"),
        pytest.param(int(1.5 * MB), "1.5 MB", id="half megabytes keep one decimal"),
        pytest.param(10 * MB + 1, "10 MB", id="rounded to one decimal"),
        pytest.param(1 * MB, "1 MB", id="exactly one megabyte"),
        pytest.param(400_000, "390.6 KB", id="below one megabyte"),
        pytest.param(1024, "1 KB", id="exactly one kilobyte"),
        pytest.param(500, "500 bytes", id="below one kilobyte"),
    ],
)
def test_file_too_large_message_states_the_limit_readably(max_bytes: int, readable: str) -> None:
    error = FileTooLargeError(max_bytes)

    assert error.message == f"The file is larger than the limit of {readable}."


def test_file_that_is_too_large_once_unpacked_is_the_same_kind_of_error() -> None:
    error = UnpackedFileTooLargeError(50 * MB)

    # The same status and code as any file that is too large. Only the message differs.
    assert (error.status_code, error.code) == (413, "file_too_large")
    assert error.message == "The file is larger than 50 MB once it is unpacked."


def test_page_count_is_stated_as_counted_or_as_estimated() -> None:
    assert TooManyPagesError(320, 300).message == "The file has 320 pages. The limit is 300."
    # A Word document has no pages, so its length is an estimate.
    assert (
        TooManyPagesError(320, 300, estimated=True).message
        == "The document is about 320 pages long. The limit is 300."
    )
