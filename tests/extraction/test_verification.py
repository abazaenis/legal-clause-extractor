"""Every string the model returned must be written in the document, or it is left empty."""

from datetime import date

import pytest

from app.extraction.labels import ClauseType, ContractType
from app.extraction.models import (
    ContractFacts,
    ContractMetadata,
    ExtractedClause,
    Line,
    ParsedDocument,
    Party,
)
from app.extraction.verification import TITLE_LINES, verify_clauses, verify_facts


def clause(
    text: str, *, number: str | None = None, title: str | None = None, start_line: int = 7
) -> ExtractedClause:
    return ExtractedClause(
        position=0,
        type=ClauseType.OTHER,
        number=number,
        title=title,
        text=text,
        start_line=start_line,
        end_line=start_line + text.count("\n"),
        start_page=None,
        end_page=None,
    )


def checked(given: ExtractedClause) -> tuple[ExtractedClause, list[tuple[str, str]]]:
    (result,), warnings = verify_clauses([given])
    return result, [(warning.code, warning.message) for warning in warnings]


# --- The number of a clause -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first_line", "number"),
    [
        ("5.1 Term. This Agreement runs for one year.", "5.1"),
        ("1. Definition of Confidential Information.", "1"),
        ("ARTICLE 5", "ARTICLE 5"),
        ("ARTICLE 5. TERM", "ARTICLE 5"),
        ("(a) the Developer shall comply;", "(a)"),
        ("I. OBLIGATIONS OF ALL USERS", "I"),
        ("Exhibit A: Access Security Requirements", "Exhibit A"),
        # From the sample service agreement: no space after the dot.
        ("23.SEVERABILITY.", "23"),
        ("  5.1   Term", "5.1"),
        # The model sometimes leaves out the label in front of the number.
        ("Exhibit X-1: Notice to Users of Consumer Reports", "X-1"),
        ("Section 5. Term", "5"),
    ],
)
def test_number_written_at_the_start_of_the_clause_is_kept(first_line: str, number: str) -> None:
    result, warnings = checked(clause(first_line, number=number))

    assert result.number == number
    assert warnings == []


def test_value_comes_back_in_the_documents_own_characters() -> None:
    # The model wrote a hyphen and straight quotes. The document has neither.
    given = clause(
        "Exhibit X–1: OBLIGATIONS OF USERS OF “PRESCREENED” LISTS",
        number="X-1",
        title='OBLIGATIONS OF USERS OF "PRESCREENED" LISTS',
    )

    result, warnings = checked(given)

    assert warnings == []
    assert (result.number, result.title) == ("X–1", "OBLIGATIONS OF USERS OF “PRESCREENED” LISTS")


@pytest.mark.parametrize(
    ("first_line", "number"),
    [
        pytest.param("5.1 Term.", "5.2", id="another number"),
        pytest.param("as required by Section 5 of this Agreement.", "5", id="not at the start"),
        pytest.param("11. Security.", "1", id="part of a longer number"),
        pytest.param("1.2 Fees.", "1", id="a deeper level"),
        pytest.param("1st party shall pay.", "1", id="part of a word"),
        pytest.param("ACUTRAQ shall provide the Services.", "A", id="a letter of a word"),
        pytest.param("ARTICLE 5", "Article 5", id="written differently"),
    ],
)
def test_number_that_is_not_written_there_is_left_empty(first_line: str, number: str) -> None:
    result, warnings = checked(clause(first_line, number=number))

    assert result.number is None
    assert warnings == [
        (
            "clause_number_not_verified",
            f'The number "{number}" given for the clause on line 7 is not written at the '
            "start of that line, and was left empty.",
        )
    ]


def test_number_is_looked_for_on_the_first_line_only() -> None:
    result, _ = checked(clause("Payment terms follow.\n5.1 Fees are due monthly.", number="5.1"))

    assert result.number is None


# --- The title of a clause --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "title"),
    [
        pytest.param("3. Remedies. The Receiving Party agrees.", "Remedies", id="first line"),
        pytest.param(
            "Exhibit X-1: Notice to Users of Consumer Reports: Obligations of Users under the\n"
            "Fair Credit Reporting Act\nAll users must have a permissible purpose.",
            "Notice to Users of Consumer Reports: Obligations of Users under the "
            "Fair Credit Reporting Act",
            id="wrapped onto the second line",
        ),
        pytest.param(
            "APPENDIX A\nNETWORK UPGRADE FACILITIES\n1. Description.",
            "NETWORK UPGRADE FACILITIES",
            id="on the line below the label",
        ),
        pytest.param("ARTICLE 5\n(continued)\nTERM\nOne year.", "TERM", id="third line"),
    ],
)
def test_title_written_at_the_start_of_the_clause_is_kept(text: str, title: str) -> None:
    result, warnings = checked(clause(text, title=title))

    assert result.title == title
    assert warnings == []


@pytest.mark.parametrize(
    ("text", "title"),
    [
        pytest.param("7. Each party keeps what it owns.", "Ownership", id="made up"),
        pytest.param("3. REMEDIES. The Receiving Party agrees.", "Remedies", id="other capitals"),
        pytest.param("5. Term.\nOne.\nTwo.\nIt ends on Termination.", "Termination", id="too far"),
    ],
)
def test_title_that_is_not_written_there_is_left_empty(text: str, title: str) -> None:
    result, warnings = checked(clause(text, title=title))

    assert result.title is None
    assert warnings == [
        (
            "clause_title_not_verified",
            f'The title "{title}" given for the clause on line 7 is not written in its first '
            f"{TITLE_LINES} lines, and was left empty.",
        )
    ]


def test_everything_else_about_the_clause_is_left_alone() -> None:
    given = clause("5.1 Term.\nOne year.", number="6", title="Duration", start_line=40)

    result, warnings = checked(given)

    assert [code for code, _ in warnings] == [
        "clause_number_not_verified",
        "clause_title_not_verified",
    ]
    assert (result.text, result.start_line, result.end_line) == ("5.1 Term.\nOne year.", 40, 41)
    assert result.type is ClauseType.OTHER


def test_clause_without_a_number_or_a_title_needs_no_check() -> None:
    given = clause("IN WITNESS WHEREOF, the parties have signed.")

    assert verify_clauses([given]) == ((given,), ())


def test_long_value_is_shortened_in_the_warning() -> None:
    _, warnings = checked(clause("1. Term.", title="T" * 200))

    assert f'"{"T" * 59}…"' in warnings[0][1]


# --- The facts about the contract ----------------------------------------------------------------

DOCUMENT = ParsedDocument(
    lines=(
        Line(number=1, text="ACME CONFIDENTIAL - Draft of January 5, 2020", page=1, is_noise=True),
        Line(number=2, text="MUTUAL NON-DISCLOSURE AGREEMENT", page=1),
        Line(number=3, text="between Static Control Components (Europe)", page=1),
        Line(number=4, text="Limited (“SCC EU”) and Leumi UK Group Limited (“Funder”),", page=1),
        Line(number=5, text="effective as of August 11, 2023.", page=1),
    ),
    page_count=1,
)


def verified(facts: ContractFacts) -> tuple[ContractMetadata, list[tuple[str, str]]]:
    metadata, warnings = verify_facts(facts, DOCUMENT)
    return metadata, [(warning.code, warning.message) for warning in warnings]


def test_facts_written_in_the_document_become_the_metadata() -> None:
    metadata, warnings = verified(
        ContractFacts(
            title="MUTUAL NON-DISCLOSURE AGREEMENT",
            contract_type=ContractType.NDA,
            effective_date="August 11, 2023",
            parties=(
                # The name wraps onto the next line in the document.
                Party(name="Static Control Components (Europe) Limited", role="SCC EU"),
                Party(name="Leumi UK Group Limited", role="Funder"),
            ),
        )
    )

    assert warnings == []
    assert metadata == ContractMetadata(
        title="MUTUAL NON-DISCLOSURE AGREEMENT",
        contract_type=ContractType.NDA,
        # The date is read by code from the text as written.
        effective_date=date(2023, 8, 11),
        parties=(
            Party(name="Static Control Components (Europe) Limited", role="SCC EU"),
            Party(name="Leumi UK Group Limited", role="Funder"),
        ),
    )


def test_missing_facts_stay_missing_without_a_warning() -> None:
    assert verified(ContractFacts()) == (ContractMetadata(), [])


def test_facts_come_back_in_the_documents_own_characters() -> None:
    # Two spaces and straight quotes from the model. One space and curly quotes in the document.
    metadata, warnings = verified(
        ContractFacts(parties=(Party(name="Leumi UK  Group Limited", role='("Funder")'),))
    )

    assert warnings == []
    assert metadata.parties == (Party(name="Leumi UK Group Limited", role="(“Funder”)"),)


def test_title_that_is_not_in_the_document_is_left_empty() -> None:
    metadata, warnings = verified(ContractFacts(title="Mutual Confidentiality Agreement"))

    assert metadata.title is None
    assert warnings == [
        (
            "title_not_verified",
            'The contract title "Mutual Confidentiality Agreement" is not written in the '
            "document, and was left empty.",
        )
    ]


def test_party_keeps_what_is_written_and_loses_what_is_not() -> None:
    metadata, warnings = verified(
        ContractFacts(
            parties=(
                Party(name="Leumi Bank plc", role="Funder"),
                Party(name="Static Control Components (Europe) Limited", role="Supplier"),
            )
        )
    )

    assert metadata.parties == (
        Party(name=None, role="Funder"),
        Party(name="Static Control Components (Europe) Limited", role=None),
    )
    assert warnings == [
        (
            "party_not_verified",
            'The party name "Leumi Bank plc" is not written in the document, and was left empty.',
        ),
        (
            "party_not_verified",
            'The party role "Supplier" is not written in the document, and was left empty.',
        ),
    ]


def test_party_with_nothing_left_is_dropped() -> None:
    metadata, warnings = verified(ContractFacts(parties=(Party(name="Globex Corporation"),)))

    assert metadata.parties == ()
    assert [code for code, _ in warnings] == ["party_not_verified"]


def test_effective_date_that_is_not_in_the_document_is_left_empty() -> None:
    metadata, warnings = verified(ContractFacts(effective_date="September 1, 2023"))

    assert metadata.effective_date is None
    assert warnings == [
        (
            "effective_date_not_verified",
            'The effective date "September 1, 2023" is not written in the document, and was '
            "left empty.",
        )
    ]


def test_page_noise_is_not_part_of_the_document_for_this_check() -> None:
    # The date in the running header was left out of the prompt, so the model never saw it.
    metadata, warnings = verified(ContractFacts(effective_date="January 5, 2020"))

    assert metadata.effective_date is None
    assert [code for code, _ in warnings] == ["effective_date_not_verified"]


def test_effective_date_that_cannot_be_read_as_a_date_is_left_empty() -> None:
    document = ParsedDocument(lines=(Line(number=1, text="Effective Date: 03/04/2021"),))

    metadata, warnings = verify_facts(ContractFacts(effective_date="03/04/2021"), document)

    assert metadata.effective_date is None
    assert [(warning.code, warning.message) for warning in warnings] == [
        (
            "effective_date_not_parsed",
            'The effective date "03/04/2021" could not be read as a date, and was left empty.',
        )
    ]
