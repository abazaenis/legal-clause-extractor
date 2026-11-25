"""White text counts as hidden only when nothing coloured is behind it."""

import pytest

from app.extraction.parsers.hidden_text import is_white


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
