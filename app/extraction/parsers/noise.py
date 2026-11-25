"""Page noise: running headers, footers and page numbers.

`flag_page_noise` is for a PDF, which has pages. `flag_body_page_numbers` is for a
Word document, which has none but can still carry page numbers in its text. Both are
pure functions of the text, so they can be tested without a file.

The rules are deliberately conservative. A line wrongly left in costs little: a
stray footer ends up inside a clause. A line wrongly flagged would drop contract
text, so anything doubtful is left alone.
"""

import math
import re
from collections import Counter
from collections.abc import Sequence

type Pages = Sequence[Sequence[str]]
type Flags = list[list[bool]]

# How many lines at the top and at the bottom of a page are looked at.
_EDGE_LINES = 3
# A repeated line counts as a header or footer when it sits at the same edge of at
# least this share of the pages, and of at least this many pages.
_MIN_PAGE_SHARE = 0.5
_MIN_PAGES = 2
# Bare numbers count as page numbers when at least this many of them count upwards.
_MIN_NUMBER_RUN = 3

_BARE_NUMBER = re.compile(r"\d{1,4}")
# A page number typed into the body of a Word document has at most three digits. Four
# digits are more likely a year, and years count upwards too: 2026, 2027, 2028.
_BODY_PAGE_NUMBER = re.compile(r"\d{1,3}")
# "Page 3" or "Page 3 of 9" at the very start or the very end of a line.
_PAGE_LABEL_AT_AN_END = re.compile(
    r"^page\s+\d+(\s+of\s+\d+)?\b|\bpage\s+\d+(\s+of\s+\d+)?$", re.IGNORECASE
)

# Page labels that are recognisable on their own. A bare number is not one of them:
# on its own it could just as well be a figure in a table.
_PAGE_LABELS = (
    re.compile(r"page\s+\d+(\s+of\s+\d+)?", re.IGNORECASE),  # Page 2, Page 2 of 3
    re.compile(r"-\s*\d+\s*-"),  # - 2 -
    re.compile(r"(?=[ivx])x{0,3}(ix|iv|v?i{0,3})"),  # ii, iv, xii: front-matter pages
    re.compile(r"[A-Z]-\d{1,3}"),  # B-7: appendix pages
)


def flag_page_noise(pages: Pages) -> Flags:
    """Say, for every line of every page, whether it is page noise.

    `pages` holds the text lines of each page, top to bottom. The result has the same
    shape, with True for a line that is a running header, a footer or a page number.

    Three rules apply, in this order:

    1. A line that sits in the top or bottom few lines of at least half of the pages
       is a header or footer. The lines must be identical, except for a page label
       at either end: "Docket 12 - Page 3" and "Docket 12 - Page 4" are the same line.
       No other number is ignored, so table rows and numbered headings never match.
    2. The outermost remaining line of a page is a page number if it is written like
       one, for example "ii" or "B-7".
    3. Outermost lines that are bare numbers are page numbers if they count upwards
       from page to page: 1, 2, 3 and so on.
    """
    flags = [[False] * len(page) for page in pages]
    _flag_repeated_edge_lines(pages, flags)
    _flag_page_labels(pages, flags)
    _flag_counting_numbers(pages, flags)
    return flags


def flag_body_page_numbers(paragraphs: Sequence[str], in_body: Sequence[bool]) -> list[bool]:
    """Say, for every paragraph of a document without pages, whether it is a page number.

    A Word file that was converted from a PDF or from a web page can have its page
    numbers as ordinary paragraphs, in the middle of a clause or even of a sentence.

    A paragraph is flagged when it is a bare number of at most three digits, and it
    belongs to a run of at least three such paragraphs that count upwards by one, with
    text between each of them: 2, text, 3, text, 4. Numbers right next to each other
    are not page numbers, because a page has text on it. A run that starts at 1 is
    left alone, because it could just as well be a list someone numbered by hand, and
    a first page rarely carries its number. `in_body` is False for the rows of a
    table, where a bare number is a figure.
    """
    flags = [False] * len(paragraphs)
    run: list[int] = []  # indexes of the numbers counted so far
    previous: int | None = None

    def flag_run() -> None:
        if len(run) >= _MIN_NUMBER_RUN and int(paragraphs[run[0]]) != 1:
            for index in run:
                flags[index] = True

    for index, (text, eligible) in enumerate(zip(paragraphs, in_body, strict=True)):
        if not eligible or not _BODY_PAGE_NUMBER.fullmatch(text.strip()):
            continue
        value = int(text)
        # The next page number is one higher, and comes after the text of a page.
        if previous is not None and (value != previous + 1 or index == run[-1] + 1):
            flag_run()
            run = []
        run.append(index)
        previous = value
    flag_run()
    return flags


def _flag_repeated_edge_lines(pages: Pages, flags: Flags) -> None:
    pages_with_text = sum(1 for page in pages if page)
    required_pages = max(_MIN_PAGES, math.ceil(pages_with_text * _MIN_PAGE_SHARE))

    for edge in (_top_edge, _bottom_edge):
        pages_per_pattern: Counter[str] = Counter()
        for page in pages:
            pages_per_pattern.update({_pattern(page[index]) for index in edge(page)})
        repeated = {
            pattern
            for pattern, page_count in pages_per_pattern.items()
            if pattern and page_count >= required_pages
        }

        for page, page_flags in zip(pages, flags, strict=True):
            # A header or footer appears once per page. A second identical line at
            # the same edge, such as a title repeating the header, is kept.
            already_flagged: set[str] = set()
            for index in edge(page):
                pattern = _pattern(page[index])
                if pattern in repeated and pattern not in already_flagged:
                    page_flags[index] = True
                    already_flagged.add(pattern)


def _flag_page_labels(pages: Pages, flags: Flags) -> None:
    for page, page_flags in zip(pages, flags, strict=True):
        for outermost in (_first_unflagged, _last_unflagged):
            index = outermost(page_flags)
            if index is not None and _is_page_label(page[index]):
                page_flags[index] = True


def _flag_counting_numbers(pages: Pages, flags: Flags) -> None:
    for outermost in (_first_unflagged, _last_unflagged):
        run: list[tuple[int, int]] = []  # (page index, line index) of the numbers so far
        previous: int | None = None

        for page_index, (page, page_flags) in enumerate(zip(pages, flags, strict=True)):
            index = outermost(page_flags)
            if index is None or not _BARE_NUMBER.fullmatch(page[index].strip()):
                # A page without a number does not break the count: in some PDFs
                # the number is printed on every other page only.
                continue
            value = int(page[index])
            if previous is not None and value != previous + 1:
                _flag_run(run, flags)
                run = []
            run.append((page_index, index))
            previous = value

        _flag_run(run, flags)


def _flag_run(run: list[tuple[int, int]], flags: Flags) -> None:
    if len(run) >= _MIN_NUMBER_RUN:
        for page_index, index in run:
            flags[page_index][index] = True


def _top_edge(page: Sequence[str]) -> list[int]:
    """Indexes of the lines at the top of a page, from the top down."""
    return list(range(min(_EDGE_LINES, len(page))))


def _bottom_edge(page: Sequence[str]) -> list[int]:
    """Indexes of the lines at the bottom of a page, from the bottom up."""
    return list(range(len(page) - 1, max(len(page) - _EDGE_LINES, 0) - 1, -1))


def _first_unflagged(page_flags: Sequence[bool]) -> int | None:
    return next((index for index, flagged in enumerate(page_flags) if not flagged), None)


def _last_unflagged(page_flags: Sequence[bool]) -> int | None:
    reversed_index = _first_unflagged(page_flags[::-1])
    return None if reversed_index is None else len(page_flags) - 1 - reversed_index


def _pattern(line: str) -> str:
    """Reduce a line to what a header or footer keeps the same from page to page.

    Whitespace is collapsed, and a page label at the start or the end of the line
    becomes "Page #". Everything else, including every other number, must match.
    """
    return _PAGE_LABEL_AT_AN_END.sub("Page #", " ".join(line.split()))


def _is_page_label(line: str) -> bool:
    text = line.strip()
    return any(label.fullmatch(text) for label in _PAGE_LABELS)
