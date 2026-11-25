"""A file is recognised by its content."""

import pytest

from app.extraction.file_types import FileType, detect_file_type


def test_pdf_is_recognised_by_its_header() -> None:
    assert detect_file_type(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj") is FileType.PDF


def test_pdf_header_may_follow_a_byte_order_mark_or_blank_lines() -> None:
    assert detect_file_type(b"\xef\xbb\xbf\r\n\r\n%PDF-1.4\n") is FileType.PDF


def test_marker_after_the_first_1024_bytes_does_not_count() -> None:
    assert detect_file_type(b"x" * 1024 + b"%PDF-1.4\n") is None


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"PK\x03\x04[Content_Types].xml", id="word document (zip)"),
        pytest.param(b"\x89PNG\r\n\x1a\n", id="png image"),
        pytest.param(b"<!doctype html><html>", id="html"),
        pytest.param(b"Plain text that mentions PDF files.", id="text mentioning pdf"),
    ],
)
def test_other_content_is_not_supported(data: bytes) -> None:
    assert detect_file_type(data) is None


def test_file_type_knows_its_media_type() -> None:
    assert FileType.PDF.media_type == "application/pdf"
