"""Checks against the real sample contracts.

The samples are assignment material and are not part of the repository, so these
tests are skipped wherever the `Contracts` folder is missing. Where it is present,
they confirm that the parser, the noise rules and the repair of the LLM's answer hold
up on real documents. No test calls an LLM: the answers are written out here.
"""

import hashlib
import re
from pathlib import Path

import pytest

from app.extraction.answer import ExtractionAnswer, RawClauseStart, RawMetadata
from app.extraction.file_types import FileType
from app.extraction.models import ParsedDocument, SourceFile
from app.extraction.parsers.pdf import PdfParser
from app.extraction.repair import repair_answer

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


# --- Repairs of the LLM's answer, on mistakes the LLM made with these documents ------------


def repaired_lines_and_warnings(
    document: ParsedDocument, *starts: RawClauseStart
) -> tuple[list[int], list[tuple[str, str]]]:
    """Repair an answer that holds the first line of the document and these starts."""
    first = RawClauseStart(line=document.content_lines[0].number, type="preamble")
    answer = ExtractionAnswer(metadata=RawMetadata(), clauses=[first, *starts])

    repaired = repair_answer(answer, document)

    return (
        [start.line for start in repaired.starts[1:]],
        [(warning.code, warning.message) for warning in repaired.warnings],
    )


def test_reference_wrapped_onto_a_new_line_is_not_taken_for_a_clause(
    parsed: dict[str, ParsedDocument],
) -> None:
    """Line 535 of the long agreement begins with a clause number, but it is the end of
    a sentence: "... as set forth in Article / 2.3.1.2.3. During the suspension period".
    The clause with that number starts on line 559.
    """
    document = sample(parsed, "NYISO")

    lines, warnings = repaired_lines_and_warnings(
        document,
        RawClauseStart(line=535, type="term_and_termination", number="2.3.1.2.3"),
        RawClauseStart(line=559, type="term_and_termination", number="2.3.1.2.3"),
    )

    assert lines == [559]
    assert warnings == [
        (
            "clause_start_in_mid_sentence",
            'The clause start on line 535 was dropped: its number "2.3.1.2.3" is also the '
            "number of another clause, and line 532 above it ends in the middle of a sentence.",
        )
    ]


def test_start_under_a_heading_is_moved_up_to_the_heading(
    parsed: dict[str, ParsedDocument],
) -> None:
    """Line 362 of the service agreement is "Exhibit A: Access Security Requirements".
    The LLM pointed at line 363, the first line of the text under that heading.
    """
    document = sample(parsed, "ACUTRAQ")

    lines, warnings = repaired_lines_and_warnings(
        document,
        RawClauseStart(line=359, type="table_of_contents", title="Table of Exhibits"),
        RawClauseStart(
            line=363, type="exhibit", number="Exhibit A", title="Access Security Requirements"
        ),
    )

    assert lines == [359, 362]
    assert warnings == [
        (
            "clause_start_moved_to_title",
            "The clause start on line 363 was moved up to line 362, where its title "
            '"Access Security Requirements" is written.',
        )
    ]
