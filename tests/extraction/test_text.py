"""Comparing a string from the model with the text of the document."""

import pytest

from app.extraction.text import contains, find_written, shorten, squeeze

DOCUMENT = (
    "MUTUAL NON-DISCLOSURE AGREEMENT\n"
    "between Static Control Components (Europe)\n"
    "Limited (“SCC EU”) and the Customer’s parent – “Funder”."
)


@pytest.mark.parametrize(
    ("value", "as_written"),
    [
        pytest.param(
            "MUTUAL NON-DISCLOSURE AGREEMENT", "MUTUAL NON-DISCLOSURE AGREEMENT", id="as it is"
        ),
        pytest.param(
            "Static Control Components (Europe) Limited",
            "Static Control Components (Europe) Limited",
            id="across a line break",
        ),
        pytest.param("Static  Control\tComponents", "Static Control Components", id="other spaces"),
        pytest.param('("SCC EU")', "(“SCC EU”)", id="straight double quotes"),
        pytest.param("Customer's parent", "Customer’s parent", id="straight apostrophe"),
        pytest.param("parent - “Funder”", "parent – “Funder”", id="hyphen for a dash"),
        pytest.param("NON–DISCLOSURE", "NON-DISCLOSURE", id="dash for a hyphen"),
    ],
)
def test_value_is_found_and_returned_as_the_document_writes_it(value: str, as_written: str) -> None:
    assert find_written(DOCUMENT, value) == as_written


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("Mutual Non-Disclosure Agreement", id="other capitals"),
        pytest.param("MUTUAL NONDISCLOSURE AGREEMENT", id="a character missing"),
        pytest.param("Static Control Components Limited", id="a word missing"),
        pytest.param("Globex Corporation", id="not there at all"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
    ],
)
def test_value_that_is_not_written_is_not_found(value: str) -> None:
    assert find_written(DOCUMENT, value) is None


def test_characters_with_a_meaning_in_patterns_are_taken_as_they_are() -> None:
    assert find_written("Fees (a.k.a. charges) of $5.00 [net]", "$5.00 [net]") == "$5.00 [net]"
    assert find_written("Fees of $5x00", "$5.00") is None


def test_squeeze_turns_every_run_of_whitespace_into_one_space() -> None:
    assert squeeze("  5.1 \t Term\nand  more ") == "5.1 Term and more"


def test_contains_ignores_whitespace_and_only_on_request_capitals() -> None:
    assert contains("Exhibit A:  Access Security\nRequirements", "Access Security Requirements")
    assert not contains("EXHIBIT A", "Exhibit A")
    assert contains("EXHIBIT A", "Exhibit A", ignore_case=True)


def test_shorten_keeps_short_text_and_cuts_long_text() -> None:
    assert shorten("Remedies") == "Remedies"
    assert shorten(None) == ""
    assert shorten("T" * 200) == "T" * 59 + "…"
