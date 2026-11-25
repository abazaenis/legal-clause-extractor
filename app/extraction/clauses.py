"""Cuts the text of every clause out of the source document.

The model only says on which line each part starts. The text of a part is the
document's own lines, from its start to the line before the next start, so no word
of a clause is ever written by the model.
"""

import re
from collections.abc import Sequence
from itertools import pairwise

from app.extraction.models import ClauseStart, ExtractedClause, PageNoise, ParsedDocument

_DIGITS = re.compile(r"\d+")


def cut_clauses(
    starts: Sequence[ClauseStart], document: ParsedDocument
) -> tuple[ExtractedClause, ...]:
    """Return one clause per start, each with its text copied from the document.

    A part runs until the next start, so the parts cannot overlap or leave a gap:
    every line of the document ends up in exactly one of them. Lines flagged as page
    noise are not part of the text. Their numbers stay unused, which is why a clause
    can run from line 20 to line 31 and have ten lines.

    The starts must be on lines of the document, in document order, with the first
    one on the first line. The repair step guarantees that. A start that breaks the
    rule raises an error here, because going on would silently lose text.
    """
    content = document.content_lines
    index_of = {line.number: index for index, line in enumerate(content)}
    try:
        boundaries = [index_of[start.line] for start in starts]
    except KeyError as error:
        raise ValueError(f"A clause starts on line {error.args[0]}, which has no text.") from None
    if not boundaries or boundaries[0] != 0:
        raise ValueError("The first clause must start on the first line of the document.")
    if any(later <= earlier for earlier, later in pairwise(boundaries)):
        raise ValueError("The clause starts must be in document order, one per line.")
    boundaries.append(len(content))

    clauses = []
    for position, start in enumerate(starts):
        lines = content[boundaries[position] : boundaries[position + 1]]
        clauses.append(
            ExtractedClause(
                position=position,
                type=start.type,
                number=start.number,
                title=start.title,
                text="\n".join(line.text for line in lines),
                start_line=lines[0].number,
                end_line=lines[-1].number,
                start_page=lines[0].page,
                end_page=lines[-1].page,
            )
        )
    return tuple(clauses)


def summarise_page_noise(document: ParsedDocument) -> tuple[PageNoise, ...]:
    """Say what was left out of the clause text as page headers, footers and page numbers.

    Lines that differ only in their numbers are counted together, with `#` in place
    of the numbers: "Page 2 of 3" and "Page 3 of 3" become "Page # of #", twice. A
    line that is the same on every page is shown as it is written.
    """
    groups: dict[str, list[str]] = {}
    for line in document.lines:
        if line.is_noise:
            groups.setdefault(_DIGITS.sub("#", line.text), []).append(line.text)
    return tuple(
        PageNoise(text=texts[0] if len(set(texts)) == 1 else pattern, occurrences=len(texts))
        for pattern, texts in groups.items()
    )
