"""Small helpers for comparing the model's strings with the text of the document."""

import re

_MAX_QUOTED_LENGTH = 60

# Characters the model tends to swap for a plainer one when it copies text: curly
# quotes become straight ones, a long dash becomes a hyphen. Each group is treated
# as one character when a value is looked up in the document.
_INTERCHANGEABLE = (
    # Double quotes: straight, curly left and right, low.
    '"“”„',
    # Single quotes and apostrophes: straight, curly left and right.
    "'‘’",  # noqa: RUF001
    # Hyphen-minus, hyphen, non-breaking hyphen, en dash and em dash.
    "-‐‑–—",  # noqa: RUF001
)
_SAME_AS = {character: f"[{re.escape(group)}]" for group in _INTERCHANGEABLE for character in group}


def pattern_for(value: str) -> str:
    """A regular expression that finds `value` as a document may write it.

    The amount of whitespace does not matter, so a value that wraps onto the next
    line is found. Neither does the style of quotes and dashes. Everything else,
    capitals included, must be the same.
    """
    return r"\s+".join(
        "".join(_SAME_AS.get(character) or re.escape(character) for character in word)
        for word in value.split()
    )


def find_written(text: str, value: str) -> str | None:
    """Return `value` in the document's own characters, or None if it is not written there.

    What comes back is cut out of `text`. So a value that passes this check is a
    copy of the source, even where the model changed a quote or a dash.
    """
    pattern = pattern_for(value)
    if not pattern:
        return None
    match = re.search(pattern, text)
    return squeeze(match.group(0)) if match else None


def squeeze(text: str) -> str:
    """Turn every run of whitespace, line breaks included, into one space."""
    return " ".join(text.split())


def contains(text: str, part: str, *, ignore_case: bool = False) -> bool:
    """Whether `part` is written in `text`. The amount of whitespace never matters,
    so a value that wraps onto the next line is still found."""
    text, part = squeeze(text), squeeze(part)
    if ignore_case:
        text, part = text.casefold(), part.casefold()
    return part in text


def shorten(text: str | None) -> str:
    """Cut a string down to a length that is safe to quote in a warning."""
    text = text or ""
    if len(text) <= _MAX_QUOTED_LENGTH:
        return text
    return text[: _MAX_QUOTED_LENGTH - 1] + "…"
