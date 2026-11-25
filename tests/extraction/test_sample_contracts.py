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
from app.extraction.clauses import cut_clauses, summarise_page_noise
from app.extraction.file_types import detect_file_type
from app.extraction.labels import ClauseType
from app.extraction.models import ClauseStart, PageNoise, ParsedDocument, SourceFile
from app.extraction.parsers.factory import build_parser_factory
from app.extraction.repair import repair_answer

CONTRACTS = Path(__file__).resolve().parents[2] / "Contracts"

pytestmark = pytest.mark.skipif(
    not CONTRACTS.is_dir(), reason="The sample contracts are not part of the repository."
)

PAGE_LABEL = re.compile(r"\d{1,3}|[ivx]+|[A-Z]-\d+|page \d+( of \d+)?", re.IGNORECASE)


@pytest.fixture(scope="module")
def parsed() -> dict[str, ParsedDocument]:
    """Every sample contract, PDF and Word, parsed once for the whole module.

    Each file goes the way an upload goes: its type is read from its content, and the
    factory picks the parser.
    """
    parsers = build_parser_factory(max_pages=300)
    documents = {}
    for path in sorted([*CONTRACTS.glob("*.pdf"), *CONTRACTS.glob("*.docx")]):
        data = path.read_bytes()
        file_type = detect_file_type(data)
        assert file_type is not None, path.name
        source = SourceFile(
            filename=path.name,
            file_type=file_type,
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
        )
        documents[path.name] = parsers.for_file_type(file_type).parse(source)
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


# --- The two Word samples ----------------------------------------------------------------------


def test_word_contract_gets_the_numbers_that_word_shows(
    parsed: dict[str, ParsedDocument],
) -> None:
    """The master agreement has no clause number in its text. Word generates all of
    them from the paragraph styles, and so does the parser.
    """
    document = sample(parsed, "Long Form MSA")
    text = {line.number: line.text for line in document.lines}

    assert (len(document.lines), document.page_count) == (113, None)
    assert noise_texts(document) == []
    assert [(warning.code, warning.message) for warning in document.warnings] == [
        (
            "numbers_computed",
            "Paragraph numbers that are not written in the text were computed from the "
            "document's numbering settings, the way Word shows them: 90 in all.",
        )
    ]
    # Nearly all of these paragraphs sit inside content controls.
    assert text[12] == "1. Services."
    assert text[13].startswith("1.1 Service Provider shall provide to ACME the services")
    # A placeholder in a numbered style, with numbering switched off: it takes no number.
    assert text[27].startswith("3.3 ACME may, in good faith, dispute any invoiced amount")
    assert text[28] == "${T:Travel Expenses}"
    assert text[29].startswith("3.4 During the initial term")
    # A sub-clause style whose paragraph says it is a clause. Read by style alone it
    # would be 5.2, and every number after it would be one too low.
    assert text[42] == "6. Data Privacy"
    assert text[43].startswith("6.1 Service Provider will only collect")
    # Letters start again under each sub-clause.
    assert text[61].startswith("(f) Has a receiver, trustee, custodian")
    assert text[62].startswith("9.4 Upon expiration or termination")
    assert text[63].startswith("(a) Deliver to ACME all documents")
    assert text[108].startswith("27. Force Majeure.")


def test_table_of_the_word_contract_is_read_row_by_row(
    parsed: dict[str, ParsedDocument],
) -> None:
    """The notice addresses of clause 16, the only table in the samples."""
    text = {line.number: line.text for line in sample(parsed, "Long Form MSA").lines}

    assert text[92].startswith("16. Notices.")
    assert text[93] == "Notice to ACME: | Random address"
    assert text[95] == "Notice to Service Provider: | [SERVICE PROVIDER ADDRESS]"
    assert text[97].startswith("17. Waiver.")


def test_header_and_footer_of_the_word_contract_are_not_in_its_lines(
    parsed: dict[str, ParsedDocument],
) -> None:
    lines = [line.text for line in sample(parsed, "Long Form MSA").lines]

    # The footer of every page reads "Professional Services Master Agreement ACME
    # Confidential Information Page 17". It is stored apart from the body and never read.
    assert not any(re.search(r"\bPage \d", line) for line in lines)
    assert not any(line.endswith("ACME Confidential Information") for line in lines)


def test_converted_word_contract_keeps_its_typed_numbers_and_loses_its_page_numbers(
    parsed: dict[str, ParsedDocument],
) -> None:
    """This file was converted from a web page. Its clause numbers are typed, half of
    its paragraphs are empty, and its page numbers are paragraphs like any other.
    """
    document = sample(parsed, "Law_Insider")
    text = {line.number: line.text for line in document.lines}

    assert (len(document.lines), document.page_count) == (102, None)
    # Nothing was computed, so there is nothing to report.
    assert document.warnings == ()
    # The numbers are in the text, written with non-breaking spaces in the file.
    assert text[7].startswith("1. Appointment. Client appoints RSE Transfer Agent LLC")
    assert text[40].startswith("13. Confidentiality. (a) In connection with")
    # The page numbers 2 to 7. The first one sits in the middle of a sentence.
    assert noise_texts(document) == ["2", "3", "4", "5", "6", "7"]
    assert text[21].endswith("the Transfer Agent may consult with legal counsel")
    assert (text[22], document.lines[21].is_noise) == ("2", True)
    assert text[23].startswith("whose advice shall be full and complete authorization")
    assert summarise_page_noise(document) == (PageNoise(text="#", occurrences=6),)


# --- Cutting the clause text ------------------------------------------------------------------


def test_cutting_a_sample_into_clauses_loses_no_line(parsed: dict[str, ParsedDocument]) -> None:
    """Wherever the starts are, the clauses together hold every line that is not page noise."""
    for name, document in parsed.items():
        content = document.content_lines
        # A start on every tenth line: arbitrary, like a model that is wrong everywhere.
        starts = [ClauseStart(line=line.number, type=ClauseType.OTHER) for line in content[::10]]

        clauses = cut_clauses(starts, document)

        rejoined = "\n".join(clause.text for clause in clauses)
        assert rejoined == "\n".join(line.text for line in content), name
        assert clauses[-1].end_line == content[-1].number, name


def test_page_noise_of_the_samples_is_summarised(parsed: dict[str, ParsedDocument]) -> None:
    assert summarise_page_noise(sample(parsed, "best case")) == (
        PageNoise(text="Page # of #", occurrences=2),
    )
    assert summarise_page_noise(sample(parsed, "ACUTRAQ")) == (
        # The footer is the same on every page, so it is shown as it is written.
        PageNoise(text="ACUTRAQ CONFIDENTIAL AND PROPRIETARY-6.21 version", occurrences=23),
        # The page numbers 2 to 23.
        PageNoise(text="#", occurrences=22),
    )
    long_agreement = summarise_page_noise(sample(parsed, "NYISO"))
    assert long_agreement[2] == PageNoise(
        text="Effective Date: #/#/# - Docket #: ER#-#-# - Page #", occurrences=176
    )
    # 528 lines were left out, and the summary of them has 14 entries.
    assert sum(noise.occurrences for noise in long_agreement) == 528
    assert len(long_agreement) == 14


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


def test_start_under_an_exhibit_heading_is_moved_up_even_without_a_title(
    parsed: dict[str, ParsedDocument],
) -> None:
    """The same mistake as above, as another run made it: a start on line 363 with no
    number and no title. Only the heading on line 362 shows where the exhibit begins.
    """
    document = sample(parsed, "ACUTRAQ")

    lines, warnings = repaired_lines_and_warnings(
        document,
        RawClauseStart(line=359, type="table_of_contents", title="Table of Exhibits"),
        RawClauseStart(line=363, type="exhibit"),
    )

    assert lines == [359, 362]
    assert warnings == [
        (
            "clause_start_moved_to_heading",
            "The clause start on line 363 was moved up to line 362, which is the heading "
            '"Exhibit A: Access Security Requirements".',
        )
    ]


def test_sentence_that_begins_with_appendix_is_not_taken_for_a_heading(
    parsed: dict[str, ParsedDocument],
) -> None:
    """Line 654 of the long agreement is the end of clause 4.1: "... set forth in" and
    then "Appendix C of this Agreement in accordance with ...". Clause 4.2 starts on
    line 655, right below it, and must stay there.
    """
    document = sample(parsed, "NYISO")

    lines, warnings = repaired_lines_and_warnings(
        document,
        RawClauseStart(line=651, type="services", number="4.1"),
        RawClauseStart(line=655, type="services", number="4.2"),
    )

    assert lines == [651, 655]
    assert warnings == []
