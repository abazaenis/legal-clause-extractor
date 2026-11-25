"""Text a reader cannot see: white text on a white background.

Used by the PDF parser. Hidden text is the obvious place to plant instructions for
the LLM, so it never reaches the prompt. It is not dropped silently either: the
parser reports what it left out.

The functions here work on the plain dictionaries pdfplumber uses for characters,
shapes and images, so they can be tested without a PDF file.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

# How close to pure white a colour has to be. Converters do not always write exactly 1.0.
_WHITE_TOLERANCE = 0.02

# An area of a page: left, top, right, bottom, measured from the top-left corner.
type Region = tuple[float, float, float, float]


def is_white(color: object) -> bool:
    """True for white, whether the colour is given as gray, RGB or CMYK.

    A colour that cannot be read as numbers (none at all, or a pattern) is not
    treated as white: when in doubt the text is kept.
    """
    components = _components(color)
    if components is None:
        return False
    if len(components) == 4:
        # CMYK describes ink, so white is the absence of all four.
        return all(component <= _WHITE_TOLERANCE for component in components)
    if len(components) in (1, 3):
        return all(component >= 1 - _WHITE_TOLERANCE for component in components)
    return False


def coloured_regions(
    shapes: Iterable[Mapping[str, Any]], images: Iterable[Mapping[str, Any]]
) -> list[Region]:
    """The areas of a page that are not plain white paper.

    These are the filled shapes that are not white, and the images. White text on
    top of one of them is visible, for example a white heading on a dark bar.
    """
    regions = [
        _region(shape)
        for shape in shapes
        if shape.get("fill") and not is_white(shape.get("non_stroking_color"))
    ]
    regions.extend(_region(image) for image in images)
    return regions


def is_hidden(char: Mapping[str, Any], regions: Sequence[Region]) -> bool:
    """True for a white character with nothing coloured behind it."""
    if not is_white(char.get("non_stroking_color")):
        return False
    x = (char["x0"] + char["x1"]) / 2
    y = (char["top"] + char["bottom"]) / 2
    return not any(
        left <= x <= right and top <= y <= bottom for left, top, right, bottom in regions
    )


def _components(color: object) -> tuple[float, ...] | None:
    """A colour as a tuple of numbers, or None if it is not made of numbers."""
    parts = color if isinstance(color, (list, tuple)) else (color,)
    numbers = [
        float(part)
        for part in parts
        if isinstance(part, (int, float)) and not isinstance(part, bool)
    ]
    if not numbers or len(numbers) != len(parts):
        return None
    return tuple(numbers)


def _region(obj: Mapping[str, Any]) -> Region:
    return (obj["x0"], obj["top"], obj["x1"], obj["bottom"])
