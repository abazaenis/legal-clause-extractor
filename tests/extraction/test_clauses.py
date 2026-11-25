"""Cutting the text of each clause out of the document."""

import pytest

from app.extraction.clauses import cut_clauses, summarise_page_noise
from app.extraction.labels import ClauseType
from app.extraction.models import ClauseStart, ExtractedClause, Line, PageNoise, ParsedDocument


def document(*texts: str, noise: tuple[int, ...] = (), lines_per_page: int = 100) -> ParsedDocument:
    """A document of these lines. `noise` holds the numbers of the page-noise lines."""
    lines = tuple(
        Line(
            number=number,
            text=text,
            page=(number - 1) // lines_per_page + 1,
            is_noise=number in noise,
        )
        for number, text in enumerate(texts, start=1)
    )
    return ParsedDocument(lines=lines, page_count=lines[-1].page)


def starts(*lines: int) -> list[ClauseStart]:
    return [ClauseStart(line=line, type=ClauseType.OTHER) for line in lines]


CONTRACT = document(
    "MUTUAL NDA",
    "between A and B",
    "1. Definitions.",
    "Confidential Information means everything.",
    "2. Term.",
    "One year.",
)


def test_each_clause_runs_to_the_line_before_the_next_start() -> None:
    clauses = cut_clauses(starts(1, 3, 5), CONTRACT)

    assert [(clause.start_line, clause.end_line) for clause in clauses] == [(1, 2), (3, 4), (5, 6)]
    assert [clause.text for clause in clauses] == [
        "MUTUAL NDA\nbetween A and B",
        "1. Definitions.\nConfidential Information means everything.",
        "2. Term.\nOne year.",
    ]


def test_every_line_of_the_document_ends_up_in_exactly_one_clause() -> None:
    clauses = cut_clauses(starts(1, 2, 5), CONTRACT)

    rejoined = "\n".join(clause.text for clause in clauses)
    assert rejoined == "\n".join(line.text for line in CONTRACT.lines)


def test_one_start_gives_one_clause_with_the_whole_document() -> None:
    (clause,) = cut_clauses(starts(1), CONTRACT)

    assert (clause.start_line, clause.end_line) == (1, 6)
    assert clause.text.count("\n") == 5


def test_labels_of_the_start_are_carried_over_with_the_position() -> None:
    preamble = ClauseStart(line=1, type=ClauseType.PREAMBLE)
    term = ClauseStart(line=5, type=ClauseType.TERM_AND_TERMINATION, number="2", title="Term")

    clauses = cut_clauses([preamble, term], CONTRACT)

    assert clauses[1] == ExtractedClause(
        position=1,
        type=ClauseType.TERM_AND_TERMINATION,
        number="2",
        title="Term",
        text="2. Term.\nOne year.",
        start_line=5,
        end_line=6,
        start_page=1,
        end_page=1,
    )
    assert clauses[0].position == 0


def test_page_noise_is_left_out_and_its_line_numbers_stay_unused() -> None:
    doc = document(
        "ACME CONFIDENTIAL",
        "1. Definitions.",
        "Page 1",
        "ACME CONFIDENTIAL",
        "continued on the next page.",
        "2. Term.",
        "Page 2",
        noise=(1, 3, 4, 7),
    )

    clauses = cut_clauses(starts(2, 6), doc)

    assert [clause.text for clause in clauses] == [
        "1. Definitions.\ncontinued on the next page.",
        "2. Term.",
    ]
    # The first clause has two lines, and they are lines 2 and 5 of the document.
    assert [(clause.start_line, clause.end_line) for clause in clauses] == [(2, 5), (6, 6)]


def test_clause_carries_the_pages_it_spans() -> None:
    doc = document("1. First.", "More.", "Still more.", "2. Second.", lines_per_page=2)

    clauses = cut_clauses(starts(1, 4), doc)

    assert [(clause.start_page, clause.end_page) for clause in clauses] == [(1, 2), (2, 2)]


def test_document_without_pages_gives_clauses_without_pages() -> None:
    # What a Word document will look like: paragraphs, no pages.
    doc = ParsedDocument(
        lines=(Line(number=1, text="1. First."), Line(number=2, text="2. Second."))
    )

    clauses = cut_clauses(starts(1, 2), doc)

    assert [(clause.start_page, clause.end_page) for clause in clauses] == [(None, None)] * 2


def test_clause_text_stays_out_of_the_repr() -> None:
    # A repr ends up in logs and tracebacks, and clause text is contract content.
    (clause,) = cut_clauses(starts(1), CONTRACT)

    assert "MUTUAL NDA" not in repr(clause)


@pytest.mark.parametrize(
    ("lines", "problem"),
    [
        pytest.param((), "first clause must start on the first line", id="no starts"),
        pytest.param((3, 5), "first clause must start on the first line", id="first lines lost"),
        pytest.param((1, 5, 3), "must be in document order", id="out of order"),
        pytest.param((1, 3, 3), "must be in document order", id="two on one line"),
        pytest.param((1, 9), "line 9, which has no text", id="line that does not exist"),
    ],
)
def test_starts_that_would_lose_text_are_refused(lines: tuple[int, ...], problem: str) -> None:
    # The repair step never produces these. Going on would silently drop text.
    with pytest.raises(ValueError, match=problem):
        cut_clauses(starts(*lines), CONTRACT)


def test_start_on_page_noise_is_refused() -> None:
    doc = document("ACME CONFIDENTIAL", "1. Definitions.", noise=(1,))

    with pytest.raises(ValueError, match="line 1, which has no text"):
        cut_clauses(starts(1, 2), doc)


# --- What was left out as page noise -----------------------------------------------------------


def test_page_noise_is_summarised_line_by_line() -> None:
    doc = document(
        "ACME CONFIDENTIAL - v6.21",
        "1. Definitions.",
        "Page 1 of 2",
        "ACME CONFIDENTIAL - v6.21",
        "2. Term.",
        "Page 2 of 2",
        noise=(1, 3, 4, 6),
    )

    assert summarise_page_noise(doc) == (
        # The same on every page: shown as it is written, numbers included.
        PageNoise(text="ACME CONFIDENTIAL - v6.21", occurrences=2),
        # Different on every page, but only in the numbers.
        PageNoise(text="Page # of #", occurrences=2),
    )


def test_bare_page_numbers_are_counted_together() -> None:
    doc = document("First.", "2", "Second.", "3", "Third.", "14", noise=(2, 4, 6))

    assert summarise_page_noise(doc) == (PageNoise(text="#", occurrences=3),)


def test_document_without_page_noise_has_nothing_to_report() -> None:
    assert summarise_page_noise(CONTRACT) == ()
