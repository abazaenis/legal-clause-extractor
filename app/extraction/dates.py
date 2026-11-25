"""Reads a date out of text as it is written in a contract.

The model copies the effective date from the contract as text. Turning that text
into a date is done here, by code, so the same text always gives the same date.
"""

import re
from datetime import date

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sept", "sep"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}

_MONTH_NAME = r"(?P<month>[A-Za-z]{3,9})\.?"
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?P<year>\d{4})"

# A date written with numbers only, such as 03/04/2021, is left out on purpose. It is
# 4 March in one country and 3 April in another, and a wrong date is worse than none.
_PATTERNS = (
    # 2021-03-01
    re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b"),
    # March 1, 2021 and Mar. 1st 2021
    re.compile(rf"\b{_MONTH_NAME}\s+{_DAY},?\s+{_YEAR}\b", re.IGNORECASE),
    # 1 March 2021 and 1st day of March, 2021
    re.compile(rf"\b{_DAY}\s+(?:day\s+of\s+)?{_MONTH_NAME},?\s+{_YEAR}\b", re.IGNORECASE),
)


def find_date(text: str) -> date | None:
    """Return the first date written in `text`, or None when there is none to read.

    Anything around the date is ignored, so "April 04, 2024 16:02 ET" gives 4 April 2024.
    """
    found: list[tuple[int, date]] = []
    for pattern in _PATTERNS:
        for match in pattern.finditer(text):
            value = _as_date(match)
            if value is not None:
                found.append((match.start(), value))
                break
    return min(found)[1] if found else None


def _as_date(match: re.Match[str]) -> date | None:
    month = match["month"]
    month_number = int(month) if month.isdigit() else _MONTHS.get(month.lower())
    if month_number is None:
        return None
    try:
        return date(int(match["year"]), month_number, int(match["day"]))
    except ValueError:
        # Written like a date, but not one: February 30.
        return None
