"""Reading the LLM's answer: forgiving about details, firm about an answer that is not one."""

import json
from typing import Any

import pytest

from app.extraction.answer import AnswerFormatError, parse_answer


def answer(**overrides: Any) -> str:
    """A well-formed answer as JSON text, with parts of it replaced."""
    base: dict[str, Any] = {
        "metadata": {
            "title": "MUTUAL NON-DISCLOSURE AGREEMENT",
            "contract_type": "nda",
            "effective_date": "August 11, 2023",
            "parties": [{"name": "Leumi UK Group Limited", "role": "Funder"}],
        },
        "clauses": [
            {"line": 1, "type": "preamble", "number": None, "title": None},
            {"line": 11, "type": "definitions", "number": "1", "title": "Definitions"},
        ],
    }
    return json.dumps(base | overrides)


def test_well_formed_answer_is_read() -> None:
    parsed = parse_answer(answer())

    assert parsed.metadata.title == "MUTUAL NON-DISCLOSURE AGREEMENT"
    assert parsed.metadata.contract_type == "nda"
    # The date stays as written in the contract. Code parses it later.
    assert parsed.metadata.effective_date == "August 11, 2023"
    assert [(party.name, party.role) for party in parsed.metadata.parties] == [
        ("Leumi UK Group Limited", "Funder")
    ]
    assert [(c.line, c.type, c.number, c.title) for c in parsed.clauses] == [
        (1, "preamble", None, None),
        (11, "definitions", "1", "Definitions"),
    ]
    assert parsed.unreadable_clauses == 0
    assert not parsed.metadata_unreadable


def test_metadata_can_be_missing_entirely() -> None:
    parsed = parse_answer(json.dumps({"clauses": [{"line": 1, "type": "other"}]}))

    assert parsed.metadata.title is None
    assert parsed.metadata.parties == []
    assert not parsed.metadata_unreadable


def test_blank_strings_count_as_missing_and_text_is_trimmed() -> None:
    parsed = parse_answer(
        answer(
            metadata={"title": "  ", "contract_type": "", "effective_date": "", "parties": []},
            clauses=[{"line": 1, "type": " other ", "number": " 5.1 ", "title": "   "}],
        )
    )

    assert (parsed.metadata.title, parsed.metadata.contract_type) == (None, None)
    assert parsed.metadata.effective_date is None
    clause = parsed.clauses[0]
    assert (clause.type, clause.number, clause.title) == ("other", "5.1", None)


def test_any_type_is_accepted_here_and_judged_later() -> None:
    # The repair step maps an unknown label to "other" and warns about it.
    parsed = parse_answer(answer(clauses=[{"line": 1, "type": "remedies"}, {"line": 2}]))

    assert [clause.type for clause in parsed.clauses] == ["remedies", None]


def test_party_can_have_a_role_without_a_name() -> None:
    # As in the sample service agreement, a template that leaves the customer's name blank.
    parsed = parse_answer(
        answer(
            metadata={
                "parties": [
                    {"name": "ACUTRAQ Background Screening Inc.", "role": "ACUTRAQ"},
                    {"name": None, "role": "Customer"},
                    {"name": None, "role": ""},
                ]
            }
        )
    )

    # The entry with neither a name nor a role says nothing and is dropped.
    assert [(party.name, party.role) for party in parsed.metadata.parties] == [
        ("ACUTRAQ Background Screening Inc.", "ACUTRAQ"),
        (None, "Customer"),
    ]


def test_fields_the_answer_was_not_asked_for_are_ignored() -> None:
    parsed = parse_answer(
        answer(notes="Not sure about clause 3.", clauses=[{"line": 1, "confidence": 0.9}])
    )

    assert len(parsed.clauses) == 1


def test_entry_that_is_not_a_clause_start_is_skipped_and_counted() -> None:
    parsed = parse_answer(
        answer(
            clauses=[
                {"line": 1, "type": "preamble"},
                {"type": "definitions"},
                {"line": "eleven", "type": "definitions"},
                "clause three",
                {"line": 20, "type": "notices"},
            ]
        )
    )

    assert [clause.line for clause in parsed.clauses] == [1, 20]
    assert parsed.unreadable_clauses == 3


def test_unreadable_metadata_is_left_empty_and_flagged() -> None:
    parsed = parse_answer(answer(metadata={"title": "NDA", "parties": "Leumi and SCC"}))

    assert parsed.metadata.title is None
    assert parsed.metadata_unreadable
    # The clause starts are what matters most, and they are still read.
    assert len(parsed.clauses) == 2


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        pytest.param("", "not valid JSON", id="empty"),
        pytest.param("Sure! Here are the clauses.", "not valid JSON", id="prose"),
        pytest.param('{"clauses": [{"line": 1', "not valid JSON", id="cut off"),
        pytest.param("[1, 2, 3]", "not a JSON object", id="a list"),
        pytest.param('{"metadata": {}}', "lists no clause starts", id="no clauses"),
        pytest.param('{"clauses": []}', "lists no clause starts", id="empty clauses"),
        pytest.param('{"clauses": "none"}', "lists no clause starts", id="clauses not a list"),
        pytest.param('{"clauses": [{"type": "other"}]}', "no usable clause starts", id="no line"),
    ],
)
def test_answer_with_nothing_to_read_is_refused(text: str, reason: str) -> None:
    with pytest.raises(AnswerFormatError, match=reason):
        parse_answer(text)


def test_refusal_never_quotes_the_answer() -> None:
    # The answer is derived from contract text, and the reason ends up in an error message.
    with pytest.raises(AnswerFormatError) as raised:
        parse_answer('{"clauses": "CONFIDENTIAL CLAUSE TEXT"}')

    assert "CONFIDENTIAL" not in str(raised.value)
