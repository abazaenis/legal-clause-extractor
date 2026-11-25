"""Page headers, footers and page numbers are flagged; contract text is not."""

from app.extraction.parsers.noise import flag_page_noise

HEADER = "ACME CONFIDENTIAL"
WORDS = (
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
)


def body(page_number: int, lines: int = 6) -> list[str]:
    """Text lines that are different on every page and contain no numbers."""
    page_word = WORDS[page_number % len(WORDS)]
    return [
        f"The {page_word} party shall perform the {WORDS[index]} duty." for index in range(lines)
    ]


def flagged(pages: list[list[str]]) -> list[list[str]]:
    """The flagged lines of each page, which reads better in an assertion than booleans."""
    flags = flag_page_noise(pages)
    return [
        [line for line, is_noise in zip(page, page_flags, strict=True) if is_noise]
        for page, page_flags in zip(pages, flags, strict=True)
    ]


def count_flagged(pages: list[list[str]]) -> int:
    return sum(len(lines) for lines in flagged(pages))


def test_result_has_the_same_shape_as_the_input() -> None:
    pages = [body(1), [], body(3, lines=2)]

    flags = flag_page_noise(pages)

    assert [len(page_flags) for page_flags in flags] == [6, 0, 2]


# --- Rule 1: lines repeated at the same edge of the pages -----------------------------


def test_line_repeated_at_the_top_of_the_pages_is_a_header() -> None:
    pages = [[HEADER, *body(number)] for number in range(1, 5)]

    assert flagged(pages) == [[HEADER]] * 4


def test_footer_is_recognised_although_its_page_label_changes() -> None:
    # As in the long sample: everything but the page label is the same on every page.
    footer = "Effective Date: 3/1/2021 - Docket #: ER21-1413-000 - Page {}"
    pages = [[*body(number), footer.format(number)] for number in range(1, 5)]

    assert flagged(pages) == [[footer.format(number)] for number in range(1, 5)]


def test_footer_missing_from_some_pages_is_still_recognised() -> None:
    # As in the sample NDAs: the footer is printed on two of three pages.
    pages = [body(1), [*body(2), "Page 2 of 3"], [*body(3), "Page 3 of 3"]]

    assert flagged(pages) == [[], ["Page 2 of 3"], ["Page 3 of 3"]]


def test_line_repeated_in_the_middle_of_the_pages_is_kept() -> None:
    repeated = "The Receiving Party shall keep it confidential."
    pages = [[*body(number), repeated, *body(number)] for number in range(1, 5)]

    assert count_flagged(pages) == 0


def test_line_at_the_edge_of_fewer_than_half_the_pages_is_kept() -> None:
    pages = [[HEADER, *body(1)], *(body(number) for number in range(2, 6))]

    assert count_flagged(pages) == 0


def test_single_page_has_nothing_to_compare_with() -> None:
    assert count_flagged([[HEADER, *body(1), "1"]]) == 0


def test_only_one_copy_of_a_header_is_flagged_per_page() -> None:
    # As in the long sample: the title on page 1 repeats the running header.
    title = "SERVICE AGREEMENT NO. 2604"
    pages = [[title, title, *body(1)], *([title, *body(number)] for number in range(2, 5))]

    flags = flag_page_noise(pages)

    assert flags[0][:3] == [True, False, False]


def test_numbered_headings_at_the_top_of_every_page_are_kept() -> None:
    # Each article starts on a new page. Only the number differs, but it is not a page label.
    pages = [[f"ARTICLE {number}", *body(number)] for number in range(1, 6)]

    assert count_flagged(pages) == 0


def test_table_rows_at_the_page_edges_are_kept() -> None:
    # Rows of a payment schedule look alike from page to page, apart from their numbers.
    pages = [
        [
            f"Milestone {number} - due {number}/1/2026 - $ {number},000",
            *body(number),
            f"Subtotal after milestone {number}: $ {number * 3},500",
        ]
        for number in range(1, 6)
    ]

    assert count_flagged(pages) == 0


# --- Rule 2: page labels at the outermost line -----------------------------------------


def test_page_labels_at_the_outer_edge_are_flagged() -> None:
    pages = [
        [*body(1), "ii"],
        [*body(2), "B-7"],
        [*body(3), "- 3 -"],
        ["Page 4", *body(4)],
    ]

    assert flagged(pages) == [["ii"], ["B-7"], ["- 3 -"], ["Page 4"]]


def test_page_label_text_away_from_the_edge_is_kept() -> None:
    pages = [[*body(1), "ii", *body(1)], [*body(2), "B-7", *body(2)]]

    assert count_flagged(pages) == 0


def test_sentence_that_ends_with_a_page_reference_is_kept() -> None:
    pages = [[*body(1), "The fees are set out in the schedule on page 12"], body(2)]

    assert count_flagged(pages) == 0


# --- Rule 3: bare numbers that count upwards --------------------------------------------


def test_bare_numbers_that_count_upwards_are_page_numbers() -> None:
    # As in the long sample: the number is printed on every other page only, too few
    # pages for it to count as a repeated footer.
    pages = [body(number) for number in range(1, 11)]
    for page_index, page_number in ((1, "1"), (3, "2"), (5, "3")):
        pages[page_index].append(page_number)

    flags = flag_page_noise(pages)

    assert [flags[index][-1] for index in (1, 3, 5)] == [True, True, True]
    assert count_flagged(pages) == 3


def test_bare_numbers_that_do_not_count_upwards_are_kept() -> None:
    # Figures at the bottom of a page, for example the last cell of a table.
    pages = [body(number) for number in range(1, 11)]
    for page_index, figure in ((1, "500"), (3, "12"), (5, "7")):
        pages[page_index].append(figure)

    assert count_flagged(pages) == 0


def test_two_numbers_in_a_row_are_not_enough() -> None:
    pages = [body(number) for number in range(1, 11)]
    pages[1].append("1")
    pages[3].append("2")

    assert count_flagged(pages) == 0


def test_clause_numbers_are_never_page_numbers() -> None:
    # A clause number has a dot after it, so it is not a bare number.
    pages = [
        [f"{number}.", f"The {WORDS[number]} obligations", *body(number)] for number in range(1, 6)
    ]

    assert count_flagged(pages) == 0


# --- The rules together -----------------------------------------------------------------


def test_footer_and_page_number_above_it_are_both_flagged() -> None:
    # As in the sample service agreement.
    footer = "ACME CONFIDENTIAL AND PROPRIETARY-6.21 version"
    pages = [[*body(number), str(number), footer] for number in range(1, 5)]

    assert flagged(pages) == [[str(number), footer] for number in range(1, 5)]
