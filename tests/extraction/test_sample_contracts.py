"""Checks against the real sample contracts.

The samples are assignment material and are not part of the repository, so these
tests are skipped wherever the `Contracts` folder is missing. Where it is present,
they confirm that the parser and the noise rules hold up on real documents.
"""

import hashlib
import re
from pathlib import Path

import pytest

from app.extraction.file_types import FileType
from app.extraction.models import ParsedDocument, SourceFile
from app.extraction.parsers.pdf import PdfParser

CONTRACTS = Path(__file__).resolve().parents[2] / "Contracts"

pytestmark = pytest.mark.skipif(
    not CONTRACTS.is_dir(), reason="The sample contracts are not part of the repository."
)

PAGE_LABEL = re.compile(r"\d{1,3}|[ivx]+|[A-Z]-\d+|page \d+( of \d+)?", re.IGNORECASE)


@pytest.fixture(scope="module")
def parsed() -> dict[str, ParsedDocument]:
    """Every sample PDF, parsed once for the whole module."""
    parser = PdfParser(max_pages=300)
    documents = {}
    for path in sorted(CONTRACTS.glob("*.pdf")):
        data = path.read_bytes()
        source = SourceFile(
            filename=path.name,
            file_type=FileType.PDF,
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
        )
        documents[path.name] = parser.parse(source)
    return documents


def sample(parsed: dict[str, ParsedDocument], name_part: str) -> ParsedDocument:
    return next(document for name, document in parsed.items() if name_part in name)


def noise_texts(document: ParsedDocument) -> list[str]:
    return [line.text for line in document.lines if line.is_noise]


def test_no_page_number_is_left_in_the_content_of_any_sample(
    parsed: dict[str, ParsedDocument],
) -> None:
    for name, document in parsed.items():
        leftovers = [
            line.text for line in document.content_lines if PAGE_LABEL.fullmatch(line.text)
        ]
        assert leftovers == [], name


def test_best_case_nda(parsed: dict[str, ParsedDocument]) -> None:
    document = sample(parsed, "best case")

    assert document.page_count == 3
    assert document.warnings == ()
    assert noise_texts(document) == ["Page 2 of 3", "Page 3 of 3"]
    # Each of the nine clauses still starts a line with its number.
    content = [line.text for line in document.content_lines]
    for number in range(1, 10):
        assert any(text.startswith(f"{number}. ") for text in content), number


def test_nda_with_a_hidden_stamp(parsed: dict[str, ParsedDocument]) -> None:
    """This NDA carries "Updated 2016" in white text, invisible on the page."""
    document = sample(parsed, "Leumi")

    assert not any("Updated 2016" in line.text for line in document.lines)
    assert [warning.code for warning in document.warnings] == ["hidden_text_removed"]
    assert 'page 1: "Updated 2016"' in document.warnings[0].message


def test_service_agreement(parsed: dict[str, ParsedDocument]) -> None:
    document = sample(parsed, "ACUTRAQ")
    footer = "ACUTRAQ CONFIDENTIAL AND PROPRIETARY-6.21 version"

    assert document.page_count == 23
    # The first heading has its number in a larger font. It must not split in two.
    assert "1. All Users Must Have a Permissible Purpose" in [
        line.text for line in document.content_lines
    ]
    noise = noise_texts(document)
    assert noise.count(footer) == 23
    # The page number above the footer, printed on every page but the first.
    assert [text for text in noise if text != footer] == [str(page) for page in range(2, 24)]
    assert footer not in [line.text for line in document.content_lines]


def test_long_agreement(parsed: dict[str, ParsedDocument]) -> None:
    document = sample(parsed, "NYISO")

    assert document.page_count == 176
    noise = noise_texts(document)
    assert sum(text.startswith("NYISO Agreements -->") for text in noise) == 176
    assert sum(text.startswith("Effective Date:") for text in noise) == 176
    # The title on the first page repeats the running header and must be kept.
    first_page = [line for line in document.lines if line.page == 1]
    assert [(line.text, line.is_noise) for line in first_page[1:3]] == [
        ("SERVICE AGREEMENT NO. 2604", True),
        ("SERVICE AGREEMENT NO. 2604", False),
    ]
    assert [warning.code for warning in document.warnings] == ["pages_without_text"]
