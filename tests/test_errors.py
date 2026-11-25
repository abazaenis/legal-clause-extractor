"""Error messages are written for a person to read."""

import pytest

from app.errors import FileTooLargeError

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
