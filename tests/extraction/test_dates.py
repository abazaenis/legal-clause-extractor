"""Reading a date from text as a contract writes it."""

from datetime import date

import pytest

from app.extraction.dates import find_date


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("March 1, 2021", date(2021, 3, 1)),
        ("April 04, 2024", date(2024, 4, 4)),
        ("MARCH 1, 2021", date(2021, 3, 1)),
        ("Dec 31 2020", date(2020, 12, 31)),
        ("Sept. 3rd, 2019", date(2019, 9, 3)),
        ("1 March 2021", date(2021, 3, 1)),
        ("11th August, 2023", date(2023, 8, 11)),
        ("1st day of March, 2021", date(2021, 3, 1)),
        ("2021-03-01", date(2021, 3, 1)),
    ],
)
def test_date_is_read_in_the_ways_contracts_write_it(text: str, expected: date) -> None:
    assert find_date(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # One of the sample NDAs gives its signature date with a time.
        "April 04, 2024 16:02 ET",
        "made as of this 4th day of April, 2024 (the “Effective Date”)",
        "Effective Date: 2024-04-04.",
    ],
)
def test_text_around_the_date_is_ignored(text: str) -> None:
    assert find_date(text) == date(2024, 4, 4)


def test_first_date_in_the_text_is_the_one_returned() -> None:
    assert find_date("signed 5 May 2021, renewed 2023-01-01") == date(2021, 5, 5)
    assert find_date("2023-01-01, first signed May 5, 2021") == date(2023, 1, 1)


@pytest.mark.parametrize(
    "text",
    [
        # 4 March in one country, 3 April in another. A wrong date is worse than none.
        pytest.param("03/04/2021", id="numbers with slashes"),
        pytest.param("3.4.2021", id="numbers with dots"),
        pytest.param("March 2021", id="no day"),
        pytest.param("the first day of March, 2021", id="day in words"),
        pytest.param("February 30, 2021", id="not a real date"),
        pytest.param("2021-13-45", id="not a real ISO date"),
        pytest.param("Section 12, 2021", id="a word that is not a month"),
        pytest.param("to be agreed", id="no date at all"),
        pytest.param("", id="empty"),
    ],
)
def test_text_that_is_not_clearly_a_date_gives_none(text: str) -> None:
    assert find_date(text) is None
