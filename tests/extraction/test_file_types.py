"""A file is recognised by its content."""

import io
import zipfile

import pytest

from app.extraction.file_types import (
    FileType,
    detect_file_type,
    is_old_office_file,
    is_password_protected_office_file,
)
from tests.helpers import build_docx, docx_paragraph

# The first bytes of every file in the container of the old Office formats.
OLD_OFFICE = bytes.fromhex("d0cf11e0a1b11ae1")


def zip_with(*names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "<xml/>")
    return buffer.getvalue()


# --- PDF -----------------------------------------------------------------------------------------


def test_pdf_is_recognised_by_its_header() -> None:
    assert detect_file_type(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj") is FileType.PDF


def test_pdf_header_may_follow_a_byte_order_mark_or_blank_lines() -> None:
    assert detect_file_type(b"\xef\xbb\xbf\r\n\r\n%PDF-1.4\n") is FileType.PDF


# --- Word ----------------------------------------------------------------------------------------


def test_word_document_is_recognised_by_the_part_that_holds_its_text() -> None:
    assert detect_file_type(build_docx(docx_paragraph("1. Term."))) is FileType.DOCX


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(zip_with("xl/workbook.xml", "[Content_Types].xml"), id="spreadsheet"),
        pytest.param(zip_with("ppt/presentation.xml"), id="presentation"),
        pytest.param(zip_with("content.xml", "mimetype"), id="OpenDocument text"),
        pytest.param(zip_with("notes.txt"), id="plain archive"),
        pytest.param(zip_with(), id="empty archive"),
        pytest.param(b"PK\x03\x04 word/document.xml but no real archive", id="not really a zip"),
    ],
)
def test_other_zip_files_are_not_word_documents(data: bytes) -> None:
    # A .docx is a ZIP file, and so are many other things.
    assert detect_file_type(data) is None


# --- Everything else -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"\x89PNG\r\n\x1a\n", id="png image"),
        pytest.param(b"<!doctype html><html>", id="html"),
        pytest.param(b"Plain text that mentions PDF files.", id="text mentioning pdf"),
        pytest.param(OLD_OFFICE + b"\x00" * 100, id="old Word document"),
    ],
)
def test_other_content_is_not_supported(data: bytes) -> None:
    assert detect_file_type(data) is None


def test_old_office_file_is_told_apart_so_the_caller_can_be_told_what_to_do() -> None:
    assert is_old_office_file(OLD_OFFICE + b"\x00" * 100)
    assert not is_old_office_file(build_docx(docx_paragraph("1. Term.")))
    assert not is_old_office_file(b"%PDF-1.7")


def test_password_protected_word_document_is_recognised() -> None:
    # A .docx with a password is not a ZIP file any more. It is stored encrypted, in
    # the old container, in a stream with this name.
    protected = OLD_OFFICE + b"\x00" * 64 + "EncryptedPackage".encode("utf-16-le") + b"\x00" * 64

    assert is_password_protected_office_file(protected)
    assert not is_password_protected_office_file(OLD_OFFICE + b"\x00" * 100)
    # The name alone, outside that container, means nothing.
    assert not is_password_protected_office_file("EncryptedPackage".encode("utf-16-le"))
