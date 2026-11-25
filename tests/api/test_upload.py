"""The client's file name is reduced to something safe to store and show."""

import pytest

from app.api.upload import clean_filename


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("nda.pdf", "nda.pdf", id="plain name"),
        pytest.param("C:\\Users\\me\\contracts\\nda.pdf", "nda.pdf", id="windows path"),
        pytest.param("/var/tmp/nda.pdf", "nda.pdf", id="unix path"),
        pytest.param("../../etc/passwd", "passwd", id="path traversal"),
        pytest.param("  spaced name.pdf  ", "spaced name.pdf", id="surrounding spaces"),
        pytest.param("", "upload", id="empty"),
        pytest.param(None, "upload", id="missing"),
        pytest.param("..", "upload", id="parent directory only"),
        pytest.param("contracts/", "contracts", id="trailing slash"),
    ],
)
def test_clean_filename(raw: str | None, expected: str) -> None:
    assert clean_filename(raw) == expected


def test_clean_filename_caps_the_length() -> None:
    assert len(clean_filename("a" * 300 + ".pdf")) == 255
