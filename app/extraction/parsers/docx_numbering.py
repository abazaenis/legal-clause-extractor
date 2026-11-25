"""The numbers Word puts in front of numbered paragraphs.

In a Word contract the clause numbers are usually not part of the text. A paragraph
only says which list it belongs to and at which level, often through its style, and
Word works the number out when it shows the page. This module does the same, so that
a line of the parsed document reads the way the paragraph does in Word:
"1.1 The Supplier shall ...".

Everything here depends only on the document's own definitions (`word/numbering.xml`
and `word/styles.xml`) and on the order of its paragraphs. The same document always
gives the same numbers.

The rules follow the file format (ECMA-376, part 1, section 17.9) and what Word does
where the format leaves it open. Corners that are not covered are listed in the README.
"""

import re
from dataclasses import dataclass, field
from xml.etree.ElementTree import Element

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# A list in Word has nine levels.
_LEVELS = 9
_PLACEHOLDER = re.compile(r"%([1-9])")
# What a bullet is shown as. The real glyph depends on a font the parser does not have.
BULLET = "•"

_KNOWN_FORMATS = frozenset(
    {"decimal", "decimalZero", "lowerLetter", "upperLetter", "lowerRoman", "upperRoman"}
)
# Formats of levels that show no number of their own.
_NO_NUMBER = frozenset({"none", "bullet"})

_ROMAN = (
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
    (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class _Level:
    """How one level of a list is numbered."""

    # decimal, lowerLetter, upperRoman, bullet, none and so on.
    number_format: str = "decimal"
    # What is shown, with %1 to %9 standing for the counters of levels one to nine.
    pattern: str = ""
    start: int = 1
    # Which higher level makes this one start again. None is Word's default: any of them.
    restart_after: int | None = None
    # "Legal" numbering: every counter in the pattern is shown as a plain number.
    all_decimal: bool = False
    # The paragraph style that puts a paragraph on this level.
    style: str | None = None


@dataclass(frozen=True, slots=True)
class _List:
    """One list as a paragraph refers to it (`w:num`)."""

    definition: str
    level_overrides: dict[int, _Level] = field(default_factory=dict)
    start_overrides: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _ListReference:
    """What a paragraph or a style says about numbering. Either part can be missing."""

    list_id: str | None = None
    level: int | None = None


class NumberLabels:
    """Works out the label of each numbered paragraph, in document order.

    It keeps the counters, so `next_label` must be called once for every paragraph
    of the document, in the order the paragraphs appear.
    """

    def __init__(self, numbering: Element | None, styles: Element | None) -> None:
        self._definitions, self._linked_definitions = _read_definitions(numbering)
        self._lists = _read_lists(numbering)
        self._style_references, self._based_on, self._default_style = _read_styles(styles)
        self._counters: dict[str, list[int | None]] = {}
        self._lists_seen: set[str] = set()
        # How many numbers were computed. Bullets are not counted.
        self.numbers_computed = 0
        # Number formats the code does not know. They were shown as plain numbers.
        self.unsupported_formats: set[str] = set()

    def next_label(self, paragraph: Element) -> str | None:
        """Return what Word shows in front of this paragraph, or None if it has no number."""
        properties = paragraph.find(f"{W}pPr")
        style = _value(properties, "pStyle") or self._default_style
        list_id, level = self._list_and_level(properties, style)
        numbered_list = self._lists.get(list_id) if list_id else None
        if numbered_list is None:
            return None
        definition = self._linked_definitions.get(
            numbered_list.definition, numbered_list.definition
        )
        levels = self._definitions.get(definition, {}) | numbered_list.level_overrides
        if level is None:
            level = self._level_of_style(levels, style)
        if level not in levels:
            return None

        counters = self._counters.setdefault(definition, [None] * _LEVELS)
        if list_id is not None and list_id not in self._lists_seen:
            # A list that sets its own start value begins again when it first appears.
            self._lists_seen.add(list_id)
            for overridden in numbered_list.start_overrides:
                counters[overridden] = None

        def start_of(index: int) -> int:
            if index in numbered_list.start_overrides:
                return numbered_list.start_overrides[index]
            return levels[index].start if index in levels else 1

        current = counters[level]
        counters[level] = start_of(level) if current is None else current + 1
        for deeper in range(level + 1, _LEVELS):
            if _starts_again(levels.get(deeper), level):
                counters[deeper] = None

        this_level = levels[level]
        if this_level.number_format == "bullet":
            return BULLET

        def shown(match: re.Match[str]) -> str:
            index = int(match.group(1)) - 1
            counter = counters[index]
            # A higher level that never appeared is shown at its start value.
            value = start_of(index) if counter is None else counter
            number_format = levels[index].number_format if index in levels else "decimal"
            if this_level.all_decimal and number_format not in _NO_NUMBER:
                number_format = "decimal"
            return self._formatted(value, number_format)

        label = " ".join(_PLACEHOLDER.sub(shown, this_level.pattern).split())
        if not label:
            return None
        self.numbers_computed += 1
        return label

    def _list_and_level(
        self, properties: Element | None, style: str | None
    ) -> tuple[str | None, int | None]:
        """The paragraph's own setting wins. What it leaves out comes from its style."""
        direct = _reference(properties)
        inherited = self._reference_of_style(style)
        list_id = direct.list_id if direct.list_id is not None else inherited.list_id
        level = direct.level if direct.level is not None else inherited.level
        # List 0 is how a paragraph, or a style, switches numbering off.
        return (None if list_id == "0" else list_id), level

    def _reference_of_style(self, style: str | None) -> _ListReference:
        """The nearest numbering setting on the style or on the styles it is based on."""
        level: int | None = None
        seen: set[str] = set()
        while style is not None and style not in seen:
            seen.add(style)
            reference = self._style_references.get(style, _ListReference())
            if level is None:
                level = reference.level
            if reference.list_id is not None:
                return _ListReference(list_id=reference.list_id, level=level)
            style = self._based_on.get(style)
        return _ListReference()

    def _level_of_style(self, levels: dict[int, _Level], style: str | None) -> int:
        """A style can name a list without a level. The list then says which level is its own."""
        seen: set[str] = set()
        while style is not None and style not in seen:
            seen.add(style)
            for index, level in levels.items():
                if level.style == style:
                    return index
            style = self._based_on.get(style)
        return 0

    def _formatted(self, value: int, number_format: str) -> str:
        if number_format in _NO_NUMBER:
            return ""
        if number_format == "decimalZero":
            return f"{value:02d}"
        if value >= 1:
            if number_format in ("lowerLetter", "upperLetter"):
                letters = _letters(value)
                return letters if number_format == "lowerLetter" else letters.upper()
            if number_format in ("lowerRoman", "upperRoman"):
                roman = _roman(value)
                return roman if number_format == "lowerRoman" else roman.upper()
        if number_format not in _KNOWN_FORMATS:
            # Words, ordinals, other scripts. A plain number is the honest fallback,
            # and the caller reports that it was used.
            self.unsupported_formats.add(number_format)
        return str(value)


# --- Reading the definitions ---------------------------------------------------------------


def _read_definitions(
    numbering: Element | None,
) -> tuple[dict[str, dict[int, _Level]], dict[str, str]]:
    """The levels of every list definition, and which definitions stand in for others.

    A definition can point to a list style instead of holding levels itself. It is
    then replaced by the definition that the style belongs to.
    """
    definitions: dict[str, dict[int, _Level]] = {}
    points_to_style: dict[str, str] = {}
    owner_of_style: dict[str, str] = {}
    for definition in _children(numbering, "abstractNum"):
        definition_id = definition.get(f"{W}abstractNumId")
        if definition_id is None:
            continue
        definitions[definition_id] = _read_levels(definition)
        if (linked := _value(definition, "numStyleLink")) is not None:
            points_to_style[definition_id] = linked
        if (owned := _value(definition, "styleLink")) is not None:
            owner_of_style[owned] = definition_id
    linked_definitions = {
        definition_id: owner_of_style[style]
        for definition_id, style in points_to_style.items()
        if style in owner_of_style
    }
    return definitions, linked_definitions


def _read_lists(numbering: Element | None) -> dict[str, _List]:
    lists: dict[str, _List] = {}
    for numbered_list in _children(numbering, "num"):
        list_id = numbered_list.get(f"{W}numId")
        definition = _value(numbered_list, "abstractNumId")
        if list_id is None or definition is None:
            continue
        level_overrides: dict[int, _Level] = {}
        start_overrides: dict[int, int] = {}
        for override in numbered_list.findall(f"{W}lvlOverride"):
            index = _level_index(override.get(f"{W}ilvl"))
            if index is None:
                continue
            if (level := override.find(f"{W}lvl")) is not None:
                level_overrides[index] = _read_level(level)
            if (start := _number(_value(override, "startOverride"))) is not None:
                start_overrides[index] = start
        lists[list_id] = _List(definition, level_overrides, start_overrides)
    return lists


def _read_levels(definition: Element) -> dict[int, _Level]:
    levels: dict[int, _Level] = {}
    for level in definition.findall(f"{W}lvl"):
        index = _level_index(level.get(f"{W}ilvl"))
        if index is not None:
            levels[index] = _read_level(level)
    return levels


def _read_level(level: Element) -> _Level:
    start = _number(_value(level, "start"))
    return _Level(
        number_format=_value(level, "numFmt") or "decimal",
        pattern=_value(level, "lvlText") or "",
        start=1 if start is None else start,
        restart_after=_number(_value(level, "lvlRestart")),
        all_decimal=is_on(level.find(f"{W}isLgl")),
        style=_value(level, "pStyle"),
    )


def _read_styles(
    styles: Element | None,
) -> tuple[dict[str, _ListReference], dict[str, str], str | None]:
    """The numbering each paragraph style carries, what it is based on, and the default style."""
    references: dict[str, _ListReference] = {}
    based_on: dict[str, str] = {}
    default_style: str | None = None
    for style in _children(styles, "style"):
        style_id = style.get(f"{W}styleId")
        if style_id is None or style.get(f"{W}type") != "paragraph":
            continue
        if _is_on_value(style.get(f"{W}default")):
            default_style = style_id
        references[style_id] = _reference(style.find(f"{W}pPr"))
        if (parent := _value(style, "basedOn")) is not None:
            based_on[style_id] = parent
    return references, based_on, default_style


def _reference(properties: Element | None) -> _ListReference:
    """Read `w:numPr`, the numbering setting of a paragraph or of a style."""
    setting = properties.find(f"{W}numPr") if properties is not None else None
    if setting is None:
        return _ListReference()
    return _ListReference(
        list_id=_value(setting, "numId"), level=_level_index(_value(setting, "ilvl"))
    )


# --- Small helpers -------------------------------------------------------------------------


def _starts_again(deeper: _Level | None, used_level: int) -> bool:
    """Whether a deeper level goes back to its start when `used_level` gets a new number."""
    if deeper is None or deeper.restart_after is None:
        return True
    if deeper.restart_after == 0:
        return False
    # The value counts levels from one: 1 means "only after the first level".
    return used_level <= deeper.restart_after - 1


def _letters(value: int) -> str:
    """1 is a, 26 is z, and then the letter repeats: 27 is aa, 28 is bb."""
    return chr(ord("a") + (value - 1) % 26) * ((value - 1) // 26 + 1)


def _roman(value: int) -> str:
    numeral = []
    for amount, symbol in _ROMAN:
        while value >= amount:
            numeral.append(symbol)
            value -= amount
    return "".join(numeral)


def _children(parent: Element | None, name: str) -> list[Element]:
    return [] if parent is None else parent.findall(f"{W}{name}")


def _value(parent: Element | None, name: str) -> str | None:
    """The `w:val` attribute of a child element, which is where Word keeps single values."""
    child = parent.find(f"{W}{name}") if parent is not None else None
    return None if child is None else child.get(f"{W}val")


def _number(text: str | None) -> int | None:
    try:
        return None if text is None else int(text)
    except ValueError:
        return None


def _level_index(text: str | None) -> int | None:
    index = _number(text)
    return index if index is not None and 0 <= index < _LEVELS else None


def is_on(element: Element | None) -> bool:
    """An on/off property is on when it is present, unless it says otherwise."""
    return element is not None and _is_on_value(element.get(f"{W}val", "true"))


def _is_on_value(value: str | None) -> bool:
    return value is not None and value.lower() not in ("0", "false", "off")
