"""The numbers Word shows in front of numbered paragraphs, worked out from its definitions."""

from xml.etree import ElementTree
from xml.etree.ElementTree import Element

import pytest

from app.extraction.parsers.docx_numbering import NumberLabels
from tests.helpers import WORD_NAMESPACES, docx_paragraph


def element(name: str, content: str) -> Element:
    return ElementTree.fromstring(f"<w:{name} {WORD_NAMESPACES}>{content}</w:{name}>")


def level(
    index: int,
    number_format: str = "decimal",
    pattern: str | None = None,
    *,
    start: int | None = None,
    style: str | None = None,
    restart: int | None = None,
    legal: bool = False,
) -> str:
    """One level of a list definition. The pattern defaults to "1." for level one."""
    pattern = f"%{index + 1}." if pattern is None else pattern
    return (
        f'<w:lvl w:ilvl="{index}">'
        + (f'<w:start w:val="{start}"/>' if start is not None else "")
        + f'<w:numFmt w:val="{number_format}"/>'
        + (f'<w:lvlRestart w:val="{restart}"/>' if restart is not None else "")
        + (f'<w:pStyle w:val="{style}"/>' if style is not None else "")
        + ("<w:isLgl/>" if legal else "")
        + f'<w:lvlText w:val="{pattern}"/>'
        + "</w:lvl>"
    )


def definition(definition_id: int, *levels: str) -> str:
    return f'<w:abstractNum w:abstractNumId="{definition_id}">{"".join(levels)}</w:abstractNum>'


def numbered_list(list_id: int, definition_id: int, overrides: str = "") -> str:
    return (
        f'<w:num w:numId="{list_id}"><w:abstractNumId w:val="{definition_id}"/>{overrides}</w:num>'
    )


def style(
    style_id: str,
    *,
    list_id: int | None = None,
    level: int | None = None,
    based_on: str | None = None,
    default: bool = False,
) -> str:
    setting = f'<w:ilvl w:val="{level}"/>' if level is not None else ""
    setting += f'<w:numId w:val="{list_id}"/>' if list_id is not None else ""
    return (
        f'<w:style w:type="paragraph" w:styleId="{style_id}"'
        + (' w:default="1"' if default else "")
        + ">"
        + (f'<w:basedOn w:val="{based_on}"/>' if based_on else "")
        + (f"<w:pPr><w:numPr>{setting}</w:numPr></w:pPr>" if setting else "")
        + "</w:style>"
    )


def paragraph(
    *, style: str | None = None, list_id: int | None = None, level: int | None = None
) -> Element:
    return element("body", docx_paragraph("Text.", style=style, list_id=list_id, level=level))[0]


# The list of a typical contract: 1. / 1.1 / (a) / (i) / (A), and the styles that use it.
LEGAL_LEVELS = (
    level(0, "decimal", "%1.", style="Clause"),
    level(1, "decimal", "%1.%2", style="Subclause"),
    level(2, "lowerLetter", "(%3)", style="Item"),
    level(3, "lowerRoman", "(%4)"),
    level(4, "upperLetter", "(%5)"),
)
LEGAL_LIST = definition(0, *LEGAL_LEVELS) + numbered_list(1, 0)
LEGAL_STYLES = (
    # Names the list only. The list itself says that this style is its first level.
    style("Clause", list_id=1)
    + style("Subclause", list_id=1, level=1)
    + style("Item", list_id=1, level=2)
    + style("Normal", default=True)
)


def legal_labels() -> NumberLabels:
    return NumberLabels(element("numbering", LEGAL_LIST), element("styles", LEGAL_STYLES))


def labels_of(labels: NumberLabels, *paragraphs: Element) -> list[str | None]:
    return [labels.next_label(each) for each in paragraphs]


def levels_in_a_row(labels: NumberLabels, *levels: int, list_id: int = 1) -> list[str | None]:
    return labels_of(labels, *(paragraph(list_id=list_id, level=each) for each in levels))


def one_level_list(number_format: str, pattern: str = "%1.", *, start: int = 1) -> NumberLabels:
    numbering = definition(0, level(0, number_format, pattern, start=start)) + numbered_list(1, 0)
    return NumberLabels(element("numbering", numbering), None)


# --- Counting ----------------------------------------------------------------------------------


def test_each_level_counts_and_the_deeper_levels_start_again() -> None:
    assert levels_in_a_row(legal_labels(), 0, 1, 1, 2, 2, 1, 2, 0, 1, 2) == [
        "1.",
        "1.1",
        "1.2",
        "(a)",
        "(b)",
        "1.3",
        "(a)",
        "2.",
        "2.1",
        "(a)",
    ]


def test_all_five_levels_of_a_legal_list() -> None:
    assert levels_in_a_row(legal_labels(), 0, 1, 2, 3, 3, 4, 4) == [
        "1.",
        "1.1",
        "(a)",
        "(i)",
        "(ii)",
        "(A)",
        "(B)",
    ]


def test_higher_level_that_never_appeared_is_shown_at_its_start() -> None:
    # A sub-clause before any clause. Word shows 1.1, not 0.1.
    assert levels_in_a_row(legal_labels(), 1, 1) == ["1.1", "1.2"]


def test_list_can_start_at_another_number() -> None:
    assert levels_in_a_row(one_level_list("decimal", start=5), 0, 0) == ["5.", "6."]


def test_how_many_numbers_were_computed_is_counted() -> None:
    labels = legal_labels()

    levels_in_a_row(labels, 0, 1, 1)
    labels.next_label(paragraph())

    assert labels.numbers_computed == 3


# --- Where a paragraph gets its numbering from ---------------------------------------------------


def test_number_comes_from_the_style_of_the_paragraph() -> None:
    # As in the sample contract: no paragraph says that it is numbered, its style does.
    labels = legal_labels()

    assert labels_of(
        labels,
        paragraph(style="Clause"),
        paragraph(style="Subclause"),
        paragraph(style="Item"),
        paragraph(style="Item"),
        paragraph(style="Subclause"),
        paragraph(style="Clause"),
    ) == ["1.", "1.1", "(a)", "(b)", "1.2", "2."]


def test_paragraphs_own_level_wins_over_the_level_of_its_style() -> None:
    # "Data Privacy" in the sample contract: a sub-clause style, moved up to a clause.
    labels = legal_labels()

    assert labels_of(
        labels,
        paragraph(style="Clause"),
        paragraph(style="Subclause"),
        paragraph(style="Subclause", list_id=1, level=0),
        paragraph(style="Subclause"),
    ) == ["1.", "1.1", "2.", "2.1"]


def test_paragraph_that_switches_numbering_off_has_no_number_and_is_not_counted() -> None:
    labels = legal_labels()

    assert labels_of(
        labels,
        paragraph(style="Subclause"),
        # List 0 is how Word writes "no numbering here".
        paragraph(style="Subclause", list_id=0),
        paragraph(style="Subclause"),
    ) == ["1.1", None, "1.2"]


def test_style_takes_its_numbering_from_the_style_it_is_based_on() -> None:
    styles = LEGAL_STYLES + style("ClauseBold", based_on="Clause") + style("Deep", based_on="Item")
    labels = NumberLabels(element("numbering", LEGAL_LIST), element("styles", styles))

    assert labels_of(
        labels,
        paragraph(style="ClauseBold"),
        paragraph(style="Subclause"),
        paragraph(style="Deep"),
    ) == ["1.", "1.1", "(a)"]


def test_styles_that_are_based_on_each_other_do_not_hang_the_parser() -> None:
    styles = style("A", based_on="B") + style("B", based_on="A")
    labels = NumberLabels(element("numbering", LEGAL_LIST), element("styles", styles))

    assert labels.next_label(paragraph(style="A")) is None


def test_default_style_can_carry_the_numbering() -> None:
    styles = style("Body", list_id=1, default=True)
    labels = NumberLabels(element("numbering", LEGAL_LIST), element("styles", styles))

    # The paragraphs name no style at all.
    assert labels_of(labels, paragraph(), paragraph()) == ["1.", "2."]


@pytest.mark.parametrize(
    "unnumbered",
    [
        pytest.param(paragraph(), id="no style and no list"),
        pytest.param(paragraph(style="Normal"), id="a style without numbering"),
        pytest.param(paragraph(list_id=42), id="a list that is not defined"),
        pytest.param(paragraph(list_id=1, level=7), id="a level the list does not define"),
    ],
)
def test_paragraph_without_usable_numbering_has_no_label(unnumbered: Element) -> None:
    assert legal_labels().next_label(unnumbered) is None


def test_document_without_any_numbering_definitions() -> None:
    # A file written by another program can have neither part.
    labels = NumberLabels(None, None)

    assert labels.next_label(paragraph(style="Clause", list_id=1)) is None
    assert labels.numbers_computed == 0


# --- How a number is written ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("number_format", "start", "expected"),
    [
        ("decimal", 9, ["9.", "10.", "11."]),
        ("decimalZero", 9, ["09.", "10.", "11."]),
        ("lowerLetter", 1, ["a.", "b.", "c."]),
        ("upperLetter", 1, ["A.", "B.", "C."]),
        # After z the letter repeats.
        ("lowerLetter", 26, ["z.", "aa.", "bb."]),
        ("lowerRoman", 3, ["iii.", "iv.", "v."]),
        ("upperRoman", 8, ["VIII.", "IX.", "X."]),
        ("lowerRoman", 39, ["xxxix.", "xl.", "xli."]),
    ],
)
def test_number_formats(number_format: str, start: int, expected: list[str]) -> None:
    labels = one_level_list(number_format, start=start)

    assert levels_in_a_row(labels, 0, 0, 0) == expected


def test_each_counter_is_shown_in_the_format_of_its_own_level() -> None:
    numbering = definition(
        0, level(0, "upperRoman", "Article %1"), level(1, "decimal", "%1.%2")
    ) + numbered_list(1, 0)
    labels = NumberLabels(element("numbering", numbering), None)

    assert levels_in_a_row(labels, 0, 1, 1, 0, 1) == [
        "Article I",
        "I.1",
        "I.2",
        "Article II",
        "II.1",
    ]


def test_legal_numbering_shows_every_counter_as_a_plain_number() -> None:
    numbering = definition(
        0, level(0, "upperRoman", "Article %1"), level(1, "decimal", "%1.%2", legal=True)
    ) + numbered_list(1, 0)
    labels = NumberLabels(element("numbering", numbering), None)

    assert levels_in_a_row(labels, 0, 0, 1) == ["Article I", "Article II", "2.1"]


def test_bullet_is_shown_as_a_bullet_and_is_not_a_number() -> None:
    # The pattern of a bullet is a glyph from a symbol font, which means nothing as text.
    labels = one_level_list("bullet", "")

    assert levels_in_a_row(labels, 0, 0) == ["•", "•"]
    assert labels.numbers_computed == 0


def test_level_without_a_number_keeps_its_fixed_text_or_has_no_label() -> None:
    assert levels_in_a_row(one_level_list("none", "Note:"), 0) == ["Note:"]
    assert levels_in_a_row(one_level_list("none", ""), 0) == [None]


def test_unknown_format_is_shown_as_a_plain_number_and_remembered() -> None:
    # Word can number in words: One, Two, Three.
    labels = one_level_list("cardinalText")

    assert levels_in_a_row(labels, 0, 0) == ["1.", "2."]
    assert labels.unsupported_formats == {"cardinalText"}


# --- Lists that restart, share a definition or change it ---------------------------------------


def test_level_can_be_told_never_to_start_again() -> None:
    numbering = definition(0, level(0), level(1, "decimal", "%1.%2", restart=0)) + numbered_list(
        1, 0
    )
    labels = NumberLabels(element("numbering", numbering), None)

    assert levels_in_a_row(labels, 0, 1, 1, 0, 1) == ["1.", "1.1", "1.2", "2.", "2.3"]


def test_level_can_start_again_only_after_a_chosen_level() -> None:
    # The third level starts again after the first level, not after the second.
    numbering = definition(
        0,
        level(0),
        level(1, "decimal", "%1.%2"),
        level(2, "lowerLetter", "(%3)", restart=1),
    ) + numbered_list(1, 0)
    labels = NumberLabels(element("numbering", numbering), None)

    assert levels_in_a_row(labels, 0, 1, 2, 2, 1, 2, 0, 1, 2) == [
        "1.",
        "1.1",
        "(a)",
        "(b)",
        "1.2",
        "(c)",
        "2.",
        "2.1",
        "(a)",
    ]


def test_two_lists_with_one_definition_count_on_together() -> None:
    numbering = definition(0, level(0)) + numbered_list(1, 0) + numbered_list(2, 0)
    labels = NumberLabels(element("numbering", numbering), None)

    assert labels_of(labels, paragraph(list_id=1, level=0), paragraph(list_id=2, level=0)) == [
        "1.",
        "2.",
    ]


def test_list_with_a_start_of_its_own_begins_again() -> None:
    # What Word writes for "Restart at 1" in the middle of a document.
    restart = '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride>'
    numbering = definition(0, level(0)) + numbered_list(1, 0) + numbered_list(2, 0, restart)
    labels = NumberLabels(element("numbering", numbering), None)

    assert labels_of(
        labels,
        paragraph(list_id=1, level=0),
        paragraph(list_id=1, level=0),
        paragraph(list_id=2, level=0),
        paragraph(list_id=2, level=0),
    ) == ["1.", "2.", "1.", "2."]


def test_list_can_replace_the_definition_of_a_level() -> None:
    replacement = f'<w:lvlOverride w:ilvl="0">{level(0, "lowerLetter", "(%1)")}</w:lvlOverride>'
    numbering = definition(0, level(0)) + numbered_list(2, 0, replacement)
    labels = NumberLabels(element("numbering", numbering), None)

    assert levels_in_a_row(labels, 0, 0, list_id=2) == ["(a)", "(b)"]


def test_definition_that_points_to_a_list_style_uses_the_levels_of_that_style() -> None:
    numbering = (
        '<w:abstractNum w:abstractNumId="0"><w:numStyleLink w:val="LegalList"/></w:abstractNum>'
        '<w:abstractNum w:abstractNumId="1"><w:styleLink w:val="LegalList"/>'
        + level(0, "upperLetter", "%1)")
        + "</w:abstractNum>"
        + numbered_list(1, 0)
    )
    labels = NumberLabels(element("numbering", numbering), None)

    assert levels_in_a_row(labels, 0, 0) == ["A)", "B)"]


def test_values_that_are_not_numbers_are_ignored() -> None:
    numbering = (
        '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="first"><w:lvlText w:val="%1."/>'
        '</w:lvl><w:lvl w:ilvl="0"><w:start w:val="one"/><w:lvlText w:val="%1."/></w:lvl>'
        "</w:abstractNum>" + numbered_list(1, 0)
    )
    labels = NumberLabels(element("numbering", numbering), None)

    assert levels_in_a_row(labels, 0, 0) == ["1.", "2."]
