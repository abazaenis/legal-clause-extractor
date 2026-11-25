"""White text counts as hidden only when nothing coloured is behind it."""

from typing import Any

import pytest

from app.extraction.parsers.hidden_text import coloured_regions, is_hidden, is_white

WHITE = (1.0,)
BLACK = (0.0,)


def char(color: object, x0: float = 100, top: float = 100) -> dict[str, Any]:
    """A character 10 points wide and high, as pdfplumber describes one."""
    return {"non_stroking_color": color, "x0": x0, "x1": x0 + 10, "top": top, "bottom": top + 10}


def box(color: object, *, fill: bool = True) -> dict[str, Any]:
    """A shape that covers the default character."""
    return {
        "non_stroking_color": color,
        "fill": fill,
        "x0": 90,
        "x1": 300,
        "top": 90,
        "bottom": 120,
    }


@pytest.mark.parametrize(
    "color",
    [
        pytest.param((1.0,), id="gray"),
        pytest.param(1, id="gray as a bare number"),
        pytest.param((1.0, 1.0, 1.0), id="rgb"),
        pytest.param([1, 1, 1], id="rgb as a list"),
        pytest.param((0.0, 0.0, 0.0, 0.0), id="cmyk without ink"),
        pytest.param((0.99,), id="almost white"),
    ],
)
def test_white_is_recognised_in_every_colour_space(color: object) -> None:
    assert is_white(color)


@pytest.mark.parametrize(
    "color",
    [
        pytest.param((0.0,), id="black"),
        pytest.param((0.5,), id="mid gray"),
        pytest.param((1.0, 0.0, 0.0), id="red"),
        pytest.param((0.0, 0.0, 0.0, 1.0), id="cmyk black"),
        pytest.param(None, id="no colour"),
        pytest.param("Pattern1", id="a pattern"),
        pytest.param((), id="empty"),
        pytest.param((1.0, "x"), id="not all numbers"),
        pytest.param(True, id="a boolean is not a colour"),
    ],
)
def test_anything_else_is_not_white(color: object) -> None:
    assert not is_white(color)


def test_white_character_on_the_bare_page_is_hidden() -> None:
    assert is_hidden(char(WHITE), regions=[])


def test_black_character_is_never_hidden() -> None:
    assert not is_hidden(char(BLACK), regions=[])


def test_white_character_on_a_coloured_shape_is_visible() -> None:
    regions = coloured_regions([box((0.0, 0.0, 0.5))], images=[])

    assert not is_hidden(char(WHITE), regions)


def test_white_character_beside_a_coloured_shape_is_hidden() -> None:
    regions = coloured_regions([box((0.0, 0.0, 0.5))], images=[])

    assert is_hidden(char(WHITE, x0=400), regions)


def test_white_character_on_an_image_is_visible() -> None:
    image = {"x0": 90, "x1": 300, "top": 90, "bottom": 120}

    assert not is_hidden(char(WHITE), coloured_regions([], images=[image]))


@pytest.mark.parametrize(
    "shape",
    [
        pytest.param(box((1.0, 1.0, 1.0)), id="a white box"),
        pytest.param(box((0.0, 0.0, 0.5), fill=False), id="an outline without a fill"),
    ],
)
def test_shapes_that_leave_the_page_white_do_not_reveal_white_text(shape: dict[str, Any]) -> None:
    regions = coloured_regions([shape], images=[])

    assert regions == []
    assert is_hidden(char(WHITE), regions)
