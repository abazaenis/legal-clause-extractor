"""The LLM's answer is repaired in code, and every repair is reported."""

from typing import Any

import pytest

from app.errors import LLMInvalidResponseError
from app.extraction.answer import ExtractionAnswer, RawClauseStart, RawMetadata
from app.extraction.labels import ClauseType, ContractType
from app.extraction.models import ClauseStart, Line, ParsedDocument, Party
from app.extraction.repair import RepairedAnswer, repair_answer


def document(*texts: str, noise: tuple[int, ...] = ()) -> ParsedDocument:
    """A one-page document. `noise` holds the numbers of the lines flagged as page noise."""
    return ParsedDocument(
        lines=tuple(
            Line(number=number, text=text, page=1, is_noise=number in noise)
            for number, text in enumerate(texts, start=1)
        ),
        page_count=1,
    )


def start(line: int, type: str | None = "other", **fields: Any) -> RawClauseStart:
    return RawClauseStart(line=line, type=type, **fields)


def answer(*starts: RawClauseStart, **fields: Any) -> ExtractionAnswer:
    return ExtractionAnswer(metadata=RawMetadata(), clauses=list(starts)).model_copy(update=fields)


def repair(
    answer_: ExtractionAnswer, document_: ParsedDocument
) -> tuple[list[int], list[tuple[str, str]], RepairedAnswer]:
    """Repair, and return the start lines and the warnings in a form that is easy to assert."""
    repaired = repair_answer(answer_, document_)
    lines = [clause.line for clause in repaired.starts]
    warnings = [(warning.code, warning.message) for warning in repaired.warnings]
    return lines, warnings, repaired


TEN_LINES = document(*(f"{number}. Clause text." for number in range(1, 11)))


# --- An answer that needs nothing ------------------------------------------------------------


def test_clean_answer_comes_back_unchanged_and_without_warnings() -> None:
    _, warnings, repaired = repair(
        answer(start(1, "preamble"), start(4, "definitions", number="1", title="Definitions")),
        TEN_LINES,
    )

    assert warnings == []
    assert repaired.starts == (
        ClauseStart(line=1, type=ClauseType.PREAMBLE),
        ClauseStart(line=4, type=ClauseType.DEFINITIONS, number="1", title="Definitions"),
    )


def test_type_written_slightly_differently_is_still_recognised() -> None:
    _, warnings, repaired = repair(
        answer(start(1, "Dispute Resolution"), start(2, "GOVERNING-LAW")), TEN_LINES
    )

    assert warnings == []
    assert [clause.type for clause in repaired.starts] == [
        ClauseType.DISPUTE_RESOLUTION,
        ClauseType.GOVERNING_LAW,
    ]


def test_closing_dot_of_a_number_is_removed_quietly() -> None:
    _, warnings, repaired = repair(
        answer(start(1, number="1."), start(2, number="ARTICLE 5."), start(3, number="(a)")),
        TEN_LINES,
    )

    assert warnings == []
    assert [clause.number for clause in repaired.starts] == ["1", "ARTICLE 5", "(a)"]


# --- Fix 1: starts that point at nothing, unknown types, order, the first line ---------------


def test_start_on_a_line_the_document_does_not_have_is_dropped() -> None:
    lines, warnings, _ = repair(answer(start(1), start(5), start(99)), TEN_LINES)

    assert lines == [1, 5]
    assert warnings == [
        (
            "clause_start_on_missing_line",
            "The clause start on line 99 was dropped: the document has no such line.",
        )
    ]


def test_start_on_page_noise_is_dropped() -> None:
    doc = document("1. First.", "Page 1 of 3", "2. Second.", noise=(2,))

    lines, warnings, _ = repair(answer(start(1), start(2), start(3)), doc)

    assert lines == [1, 3]
    assert warnings == [
        (
            "clause_start_on_page_noise",
            "The clause start on line 2 was dropped: that line is a page header, a footer "
            "or a page number.",
        )
    ]


def test_unknown_type_becomes_other() -> None:
    _, warnings, repaired = repair(answer(start(1), start(3, "remedies")), TEN_LINES)

    assert repaired.starts[1].type is ClauseType.OTHER
    assert warnings == [
        (
            "unknown_clause_type",
            'The clause start on line 3 had the unknown type "remedies" and was labelled "other".',
        )
    ]


def test_missing_type_becomes_other() -> None:
    _, warnings, repaired = repair(answer(start(1), start(3, None)), TEN_LINES)

    assert repaired.starts[1].type is ClauseType.OTHER
    assert warnings == [
        ("unknown_clause_type", 'The clause start on line 3 had no type and was labelled "other".')
    ]


def test_starts_out_of_order_are_sorted() -> None:
    lines, warnings, _ = repair(answer(start(1), start(7), start(4), start(9)), TEN_LINES)

    assert lines == [1, 4, 7, 9]
    assert warnings == [
        (
            "clause_starts_reordered",
            "The clause starts were not in document order and were sorted (1 out of place).",
        )
    ]


def test_second_start_on_the_same_line_is_dropped() -> None:
    lines, warnings, repaired = repair(
        answer(start(1), start(4, "definitions"), start(4, "notices")), TEN_LINES
    )

    assert lines == [1, 4]
    assert repaired.starts[1].type is ClauseType.DEFINITIONS
    assert warnings == [
        (
            "clause_start_repeated",
            "A second clause start on line 4 was dropped: the line already starts a part.",
        )
    ]


def test_preamble_is_added_when_the_first_start_is_not_the_first_line() -> None:
    lines, warnings, repaired = repair(answer(start(3, "definitions"), start(6)), TEN_LINES)

    assert lines == [1, 3, 6]
    assert repaired.starts[0] == ClauseStart(line=1, type=ClauseType.PREAMBLE)
    assert warnings == [
        (
            "preamble_added",
            "A preamble part was added from line 1: the first clause start was on line 3, "
            "so the lines before it belonged to no part.",
        )
    ]


def test_first_line_means_the_first_line_that_is_not_page_noise() -> None:
    doc = document("ACME CONFIDENTIAL", "MUTUAL NDA", "1. Definitions.", noise=(1,))

    lines, warnings, _ = repair(answer(start(2, "preamble"), start(3)), doc)

    assert lines == [2, 3]
    assert warnings == []


# --- Fix 2: a reference wrapped onto a new line ----------------------------------------------

REFERENCE_ON_A_NEW_LINE = document(
    "2.3.1.2.2 During the suspension period, the Developer shall complete the",
    "assignment of this Agreement to a new developer as set forth in Article",
    "2.3.1.2.3. During the suspension period, the running of any notice of termination",
    "time period shall be suspended.",
    "2.3.1.2.3 If the NYISO selects a new developer, the Developer shall coordinate.",
)


def test_reference_wrapped_onto_a_new_line_is_not_a_clause() -> None:
    lines, warnings, _ = repair(
        answer(
            start(1, number="2.3.1.2.2"),
            start(3, number="2.3.1.2.3."),
            start(5, number="2.3.1.2.3"),
        ),
        REFERENCE_ON_A_NEW_LINE,
    )

    assert lines == [1, 5]
    assert warnings == [
        (
            "clause_start_in_mid_sentence",
            'The clause start on line 3 was dropped: its number "2.3.1.2.3" is also the number '
            "of another clause, and line 2 above it ends in the middle of a sentence.",
        )
    ]


def test_start_is_kept_when_no_other_start_has_its_number() -> None:
    lines, warnings, _ = repair(
        answer(start(1, number="2.3.1.2.2"), start(3, number="2.3.1.2.3")),
        REFERENCE_ON_A_NEW_LINE,
    )

    assert lines == [1, 3]
    assert warnings == []


@pytest.mark.parametrize(
    "line_above",
    [
        pytest.param("conditioned or delayed.", id="a finished sentence"),
        pytest.param("NETWORK UPGRADE FACILITIES", id="a heading"),
        pytest.param("Figure C-4", id="a caption"),
        pytest.param("(a) the first obligation; and", id="a list item ending in and"),
        pytest.param("terms of the account. Section 604(a)(3)(F)(ii)", id="a citation"),
    ],
)
def test_repeated_number_alone_does_not_remove_a_start(line_above: str) -> None:
    # Numbers repeat all the time: "1." in the contract and again in an appendix,
    # "(b)" under every section. Each of these lines came up in the sample contracts.
    doc = document("1. First part.", "It ends here.", line_above, "1. Second part with the number.")

    lines, warnings, _ = repair(answer(start(1, number="1"), start(4, number="1")), doc)

    assert lines == [1, 4]
    assert warnings == []


def test_start_is_kept_when_the_line_above_is_a_start_itself() -> None:
    doc = document("5. Requirements of the", "(a) Developer must comply.", "6. Other.", "(a) More.")

    lines, warnings, _ = repair(
        answer(start(1, number="5"), start(2, number="(a)"), start(3), start(4, number="(a)")), doc
    )

    assert lines == [1, 2, 3, 4]
    assert warnings == []


def test_one_start_with_the_number_always_survives() -> None:
    doc = document(
        "Opening.", "as set forth in Section", "7.1 First.", "as required by", "7.1 Second."
    )

    lines, warnings, _ = repair(
        answer(start(1), start(3, number="7.1"), start(5, number="7.1")), doc
    )

    assert lines == [1, 5]
    assert [code for code, _ in warnings] == ["clause_start_in_mid_sentence"]


# --- Fix 3: a title on the line above ---------------------------------------------------------

EXHIBIT_HEADING = document(
    "Table of Exhibits",
    "Exhibit A: Access Security Requirements",
    "We must work together to protect the privacy of consumers.",
    "The following measures are designed to reduce unauthorized access.",
)


def test_start_is_moved_up_to_the_line_with_its_title() -> None:
    lines, warnings, repaired = repair(
        answer(start(1), start(3, "exhibit", title="Exhibit A: Access Security Requirements")),
        EXHIBIT_HEADING,
    )

    assert lines == [1, 2]
    assert repaired.starts[1].title == "Exhibit A: Access Security Requirements"
    assert warnings == [
        (
            "clause_start_moved_to_title",
            "The clause start on line 3 was moved up to line 2, where its title "
            '"Exhibit A: Access Security Requirements" is written.',
        )
    ]


def test_title_can_be_a_part_of_the_line_above() -> None:
    # The model gives "Exhibit A" as the number and the rest as the title.
    lines, _, _ = repair(
        answer(
            start(1),
            start(3, "exhibit", number="Exhibit A", title="Access  Security Requirements"),
        ),
        EXHIBIT_HEADING,
    )

    assert lines == [1, 2]


def test_title_on_its_own_line_in_other_capitals_counts_as_being_there() -> None:
    # "Term" is not on line 3 letter for letter, but it is on line 2. Moving the start
    # would put the end of clause 4 into clause 5.
    doc = document(
        "4. Fees. The fees are fixed", "for the remainder of the Term.", "5. TERM. One year."
    )

    lines, warnings, _ = repair(
        answer(start(1, number="4"), start(3, number="5", title="Term")), doc
    )

    assert lines == [1, 3]
    assert warnings == []


def test_page_noise_between_the_title_and_the_start_is_skipped() -> None:
    doc = document(
        "Opening.", "Exhibit A: Security", "Page 11", "We must work together.", noise=(3,)
    )

    lines, _, _ = repair(answer(start(1), start(4, title="Exhibit A: Security")), doc)

    assert lines == [1, 2]


# A heading that does not begin like the heading of an attachment, so that only the
# rule about titles is at work in the cases below.
PLAIN_HEADING = document(
    "Opening words.",
    "Access Security Requirements",
    "We must work together to protect the privacy of consumers.",
    "The following measures are designed to reduce unauthorized access.",
)


@pytest.mark.parametrize(
    "starts",
    [
        pytest.param(
            (start(1), start(2, title="Access Security Requirements")),
            id="the title is on the start line already",
        ),
        pytest.param(
            (start(1), start(2), start(3, title="Access Security Requirements")),
            id="the line above is a start itself",
        ),
        pytest.param((start(1), start(3, title="Security Measures")), id="the title is nowhere"),
        pytest.param(
            (start(1), start(3, title="ACCESS SECURITY REQUIREMENTS")),
            id="the line above writes the title differently",
        ),
        pytest.param((start(1), start(3)), id="there is no title"),
    ],
)
def test_start_stays_where_it_is(starts: tuple[RawClauseStart, ...]) -> None:
    lines, warnings, _ = repair(answer(*starts), PLAIN_HEADING)

    assert lines == [clause.line for clause in starts]
    assert warnings == []


# --- Fix 4: the heading of an attachment on the line above ------------------------------------


def test_start_under_the_heading_of_an_attachment_is_moved_up_to_it() -> None:
    # No title this time, so the rule about titles has nothing to go by.
    lines, warnings, repaired = repair(answer(start(1), start(3, "exhibit")), EXHIBIT_HEADING)

    assert lines == [1, 2]
    assert repaired.starts[1].type is ClauseType.EXHIBIT
    assert warnings == [
        (
            "clause_start_moved_to_heading",
            "The clause start on line 3 was moved up to line 2, which is the heading "
            '"Exhibit A: Access Security Requirements".',
        )
    ]


@pytest.mark.parametrize(
    "heading",
    ["Exhibit A: Access Security Requirements", "APPENDIX B", "Schedule 1: Fees", "annex II"],
)
def test_each_kind_of_attachment_heading_is_recognised(heading: str) -> None:
    doc = document("Opening words.", heading, "The first paragraph below it.")

    lines, warnings, _ = repair(answer(start(1), start(3)), doc)

    assert lines == [1, 2]
    assert [code for code, _ in warnings] == ["clause_start_moved_to_heading"]


@pytest.mark.parametrize(
    "line_above",
    [
        pytest.param("Table of Exhibits", id="the word comes later in the line"),
        pytest.param("Scheduled maintenance is announced in advance.", id="a longer word"),
        pytest.param("Exhibits are attached to this Agreement.", id="the plural"),
        pytest.param("Access Security Requirements", id="another kind of heading"),
        pytest.param("Appendix C of this Agreement in accordance with its terms.", id="a sentence"),
        pytest.param("Schedule 1 - Fees", id="no colon after the identifier"),
        pytest.param("EXHIBIT", id="no identifier"),
    ],
)
def test_line_above_that_is_not_an_attachment_heading_moves_nothing(line_above: str) -> None:
    doc = document("Opening words.", line_above, "The first paragraph below it.")

    lines, warnings, _ = repair(answer(start(1), start(3)), doc)

    assert lines == [1, 3]
    assert warnings == []


def test_reference_that_wraps_onto_a_new_line_is_not_a_heading() -> None:
    # The shape of lines 653 to 655 of the long sample agreement. The second line
    # begins like a heading, but it carries on the sentence of the first.
    doc = document(
        "4.1 The systems shall interconnect at the Points of Interconnection set forth in",
        "Appendix C of this Agreement in accordance with the terms of this Agreement.",
        "4.2 No Transmission Delivery Service.",
    )

    lines, warnings, _ = repair(answer(start(1, number="4.1"), start(3, number="4.2")), doc)

    assert lines == [1, 3]
    assert warnings == []


def test_heading_must_follow_a_line_that_ends_a_sentence() -> None:
    # "Appendix B." has the form of a heading. The line before it shows that it is the
    # end of a sentence.
    doc = document(
        "4.1 The work shall follow the milestones set forth in",
        "Appendix B.",
        "4.2 Each party shall bear its own costs.",
    )

    lines, warnings, _ = repair(answer(start(1, number="4.1"), start(3, number="4.2")), doc)

    assert lines == [1, 3]
    assert warnings == []


def test_heading_on_the_first_line_of_the_document_has_no_line_before_it() -> None:
    doc = document("EXHIBIT A", "The first paragraph.", "The second paragraph.")

    lines, warnings, _ = repair(answer(start(2)), doc)

    assert lines == [1]
    assert [code for code, _ in warnings] == ["clause_start_moved_to_heading"]


def test_start_that_is_on_an_attachment_heading_itself_stays_there() -> None:
    # The line above the heading is the last row of a list of exhibits, not its heading.
    doc = document(
        "Table of Exhibits",
        "Exhibit A: Access Security Requirements",
        "Exhibit B: Notice to Users",
        "Exhibit A: Access Security Requirements",
        "We must work together to protect the privacy of consumers.",
    )

    lines, warnings, _ = repair(answer(start(1, "table_of_contents"), start(4, "exhibit")), doc)

    assert lines == [1, 4]
    assert warnings == []


def test_attachment_heading_that_is_a_start_already_is_left_alone() -> None:
    lines, warnings, _ = repair(answer(start(1), start(2, "exhibit"), start(3)), EXHIBIT_HEADING)

    assert lines == [1, 2, 3]
    assert warnings == []


def test_start_with_a_title_is_moved_once_and_reported_once() -> None:
    # Both rules apply here. The rule about titles comes first, and after it the start
    # is on the heading itself.
    lines, warnings, _ = repair(
        answer(start(1), start(3, "exhibit", title="Access Security Requirements")),
        EXHIBIT_HEADING,
    )

    assert lines == [1, 2]
    assert [code for code, _ in warnings] == ["clause_start_moved_to_title"]


def test_page_noise_between_the_heading_and_the_start_is_skipped() -> None:
    doc = document("Opening.", "APPENDIX A", "Page 11", "The first paragraph.", noise=(3,))

    lines, _, _ = repair(answer(start(1), start(4)), doc)

    assert lines == [1, 2]


# --- When the answer is refused ------------------------------------------------------------


def twenty_starts(*bad_lines: int) -> ExtractionAnswer:
    doc_lines = range(1, 21)
    return answer(*(start(n, "nonsense" if n in bad_lines else "other") for n in doc_lines))


TWENTY_LINES = document(*(f"{number}. Clause text." for number in range(1, 21)))


def test_a_tenth_of_the_starts_may_need_fixing() -> None:
    _, warnings, _ = repair(twenty_starts(3, 9), TWENTY_LINES)

    assert len(warnings) == 2


def test_more_than_a_tenth_is_too_much() -> None:
    with pytest.raises(LLMInvalidResponseError, match="3 of its 20 clause starts needed"):
        repair_answer(twenty_starts(3, 9, 15), TWENTY_LINES)


def test_one_fix_is_always_allowed_so_short_contracts_are_not_refused() -> None:
    lines, warnings, _ = repair(answer(start(1), start(2), start(99)), TEN_LINES)

    assert lines == [1, 2]
    assert len(warnings) == 1


def test_two_fixes_in_a_short_answer_are_too_many() -> None:
    with pytest.raises(LLMInvalidResponseError, match="2 of its 4 clause starts needed"):
        repair_answer(answer(start(1), start(2), start(98), start(99)), TEN_LINES)


def test_every_kind_of_fix_counts() -> None:
    # Unreadable, missing line, unknown type: three fixes in an answer of twenty-one.
    answer_ = twenty_starts(3).model_copy(update={"unreadable_clauses": 1})
    answer_.clauses.append(start(99))

    with pytest.raises(LLMInvalidResponseError, match="3 of its 22 clause starts needed"):
        repair_answer(answer_, TWENTY_LINES)


def test_unreadable_entries_are_reported() -> None:
    _, warnings, _ = repair(
        answer(*(start(n) for n in range(1, 21)), unreadable_clauses=2), TWENTY_LINES
    )

    assert warnings == [
        (
            "clause_start_unreadable",
            "Left out 2 clause starts that could not be read in the LLM's answer.",
        )
    ]


def test_answer_with_no_usable_start_is_refused() -> None:
    with pytest.raises(LLMInvalidResponseError, match="1 of its 1 clause starts needed"):
        repair_answer(answer(start(99)), TEN_LINES)


# --- Metadata --------------------------------------------------------------------------------


def test_metadata_is_carried_over() -> None:
    metadata = RawMetadata.model_validate(
        {
            "title": "MUTUAL NON-DISCLOSURE AGREEMENT",
            "contract_type": "NDA",
            "effective_date": "August 11, 2023",
            "parties": [{"name": "Leumi UK Group Limited", "role": "Funder"}, {"role": "Customer"}],
        }
    )

    _, warnings, repaired = repair(answer(start(1), metadata=metadata), TEN_LINES)

    assert warnings == []
    assert repaired.facts.title == "MUTUAL NON-DISCLOSURE AGREEMENT"
    assert repaired.facts.contract_type is ContractType.NDA
    assert repaired.facts.effective_date == "August 11, 2023"
    assert repaired.facts.parties == (
        Party(name="Leumi UK Group Limited", role="Funder"),
        Party(name=None, role="Customer"),
    )


def test_unknown_contract_type_becomes_other_without_counting_as_a_start_fix() -> None:
    metadata = RawMetadata(contract_type="interconnection agreement")

    # Two warnings in a three-start answer: only one of them is about a start.
    _, warnings, repaired = repair(
        answer(start(1), start(2), start(99), metadata=metadata), TEN_LINES
    )

    assert repaired.facts.contract_type is ContractType.OTHER
    assert [code for code, _ in warnings] == [
        "clause_start_on_missing_line",
        "unknown_contract_type",
    ]


def test_unreadable_metadata_is_reported() -> None:
    _, warnings, repaired = repair(answer(start(1), metadata_unreadable=True), TEN_LINES)

    assert repaired.facts.title is None
    assert warnings == [
        (
            "metadata_unreadable",
            "The contract metadata in the LLM's answer could not be read and was left empty.",
        )
    ]
