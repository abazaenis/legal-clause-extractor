"""Repairs the LLM's answer against the source document.

The model is not asked again when its answer has problems. Code fixes what it can,
and every fix is reported as a warning. The answer is refused only when so much of
it needed fixing that the rest cannot be trusted.

One thing is done without a warning: the closing dot of a clause number is trimmed,
because "5.1." and "5.1" are the same number and the model writes it either way.

All of this is deterministic: the same answer and the same document always give the
same result.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import pairwise

from app.errors import LLMInvalidResponseError
from app.extraction.answer import ExtractionAnswer, RawClauseStart
from app.extraction.labels import ClauseType, ContractType
from app.extraction.models import (
    ClauseStart,
    ContractFacts,
    ExtractionWarning,
    Line,
    ParsedDocument,
    Party,
)
from app.extraction.text import contains, shorten, squeeze

# An answer is refused when more than this share of its clause starts needed fixing.
MAX_FIXED_SHARE = 0.10

# A line that ends with one of these words cannot be the end of a sentence, so the
# sentence runs on into the next line. "and" and "or" are left out on purpose: a list
# item often ends with "; and", and the item after it is a real start.
_WORDS_THAT_CANNOT_END_A_SENTENCE = frozenset(
    {
        # References: "... as set forth in Article" / "2.3.1.2.3. During ..."
        "article", "articles", "section", "sections", "clause", "clauses",
        "paragraph", "paragraphs", "subsection", "subsections",
        "exhibit", "appendix", "schedule", "annex",
        # Articles and prepositions.
        "a", "an", "the", "of", "in", "to", "under", "with", "by", "per", "pursuant",
    }
)  # fmt: skip
_LAST_WORD = re.compile(r"([A-Za-z]+)\s*$")

# What the heading of an attachment looks like: the word, an identifier, and then a
# colon or nothing. "Exhibit A: Access Security Requirements" and "APPENDIX B" do.
# "Appendix C of this Agreement in accordance with ..." does not: it is a sentence.
_ATTACHMENT_HEADING = re.compile(
    r"\s*(?:exhibit|appendix|schedule|annex)\s+[^\s:]+\s*(?::|$)", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class RepairedAnswer:
    """The model's answer after repair: usable as it is."""

    # In document order. The first one is on the first line of the document.
    starts: tuple[ClauseStart, ...]
    facts: ContractFacts
    # One for every fix that was made.
    warnings: tuple[ExtractionWarning, ...]


class _Repairs:
    """Collects the warnings, and counts how many clause starts needed fixing."""

    def __init__(self) -> None:
        self.warnings: list[ExtractionWarning] = []
        self.fixed_starts = 0

    def fix(self, code: str, message: str, *, starts: int = 1) -> None:
        """Record a fix to one or more clause starts."""
        self.fixed_starts += starts
        self.warnings.append(ExtractionWarning(code=code, message=message))

    def note(self, code: str, message: str) -> None:
        """Record a fix that did not touch a clause start."""
        self.warnings.append(ExtractionWarning(code=code, message=message))


def repair_answer(answer: ExtractionAnswer, document: ParsedDocument) -> RepairedAnswer:
    """Fix the model's answer where it disagrees with the document.

    Raises `LLMInvalidResponseError` when more than `MAX_FIXED_SHARE` of the clause
    starts needed fixing. One fix is always allowed, so that a short contract is not
    refused over a single correction.
    """
    repairs = _Repairs()
    total_starts = len(answer.clauses) + answer.unreadable_clauses

    if answer.unreadable_clauses:
        repairs.fix(
            "clause_start_unreadable",
            f"Left out {_count(answer.unreadable_clauses, 'clause start')} that could not be "
            "read in the LLM's answer.",
            starts=answer.unreadable_clauses,
        )

    starts = _starts_on_real_lines(answer.clauses, document, repairs)
    starts = _in_document_order(starts, repairs)
    starts = _one_start_per_line(starts, repairs)
    starts = _moved_up_to_their_titles(starts, document, repairs)
    starts = _moved_up_to_attachment_headings(starts, document, repairs)
    starts = _without_starts_in_mid_sentence(starts, document, repairs)
    if starts:
        starts = _from_the_first_line(starts, document, repairs)

    if not starts or repairs.fixed_starts > max(1, total_starts * MAX_FIXED_SHARE):
        raise LLMInvalidResponseError(
            "The LLM's answer could not be used: "
            f"{repairs.fixed_starts} of its {total_starts} clause starts needed correcting."
        )

    return RepairedAnswer(
        starts=tuple(starts),
        facts=_facts(answer, repairs),
        warnings=tuple(repairs.warnings),
    )


# --- The fixes, in the order they are applied ----------------------------------------------


def _starts_on_real_lines(
    raw_starts: list[RawClauseStart], document: ParsedDocument, repairs: _Repairs
) -> list[ClauseStart]:
    """Drop starts that point at nothing, and give every other start a known type."""
    lines = {line.number: line for line in document.lines}
    starts = []
    for raw in raw_starts:
        line = lines.get(raw.line)
        if line is None:
            repairs.fix(
                "clause_start_on_missing_line",
                f"The clause start on line {raw.line} was dropped: the document has no such line.",
            )
        elif line.is_noise:
            repairs.fix(
                "clause_start_on_page_noise",
                f"The clause start on line {raw.line} was dropped: that line is a page header, "
                "a footer or a page number.",
            )
        else:
            starts.append(
                ClauseStart(
                    line=raw.line,
                    type=_known_clause_type(raw, repairs),
                    number=_without_closing_dot(raw.number),
                    title=raw.title,
                )
            )
    return starts


def _known_clause_type(raw: RawClauseStart, repairs: _Repairs) -> ClauseType:
    label = _as_label(raw.type)
    if label in ClauseType:
        return ClauseType(label)
    given = f'the unknown type "{shorten(raw.type)}"' if raw.type else "no type"
    # Reported, but not counted against the answer: a label is not a boundary, and the
    # text of the clause is the same whatever it is called.
    repairs.note(
        "unknown_clause_type",
        f'The clause start on line {raw.line} had {given} and was labelled "other".',
    )
    return ClauseType.OTHER


def _in_document_order(starts: list[ClauseStart], repairs: _Repairs) -> list[ClauseStart]:
    out_of_place = sum(1 for before, after in pairwise(starts) if after.line < before.line)
    if not out_of_place:
        return starts
    repairs.fix(
        "clause_starts_reordered",
        "The clause starts were not in document order and were sorted "
        f"({out_of_place} out of place).",
        starts=out_of_place,
    )
    return sorted(starts, key=lambda start: start.line)


def _one_start_per_line(starts: list[ClauseStart], repairs: _Repairs) -> list[ClauseStart]:
    kept: list[ClauseStart] = []
    for start in starts:
        if kept and kept[-1].line == start.line:
            repairs.fix(
                "clause_start_repeated",
                f"A second clause start on line {start.line} was dropped: "
                "the line already starts a part.",
            )
        else:
            kept.append(start)
    return kept


def _moved_up_to_their_titles(
    starts: list[ClauseStart], document: ParsedDocument, repairs: _Repairs
) -> list[ClauseStart]:
    """Move a start up one line when its title is written there and not on its own line.

    The model sometimes points at the first line of the text under a heading. The
    heading belongs to the part, so the start is moved onto it. The line above must
    hold the title exactly as the model gave it. A start is only left alone more
    easily: a title on its own line in other capitals still counts as being there.
    """
    content = document.content_lines
    position = {line.number: index for index, line in enumerate(content)}
    taken = {start.line for start in starts}

    moved = []
    for start in starts:
        index = position[start.line]
        above = content[index - 1] if index > 0 else None
        if (
            start.title
            and above is not None
            and above.number not in taken
            and contains(above.text, start.title)
            and not contains(content[index].text, start.title, ignore_case=True)
        ):
            repairs.fix(
                "clause_start_moved_to_title",
                f"The clause start on line {start.line} was moved up to line {above.number}, "
                f'where its title "{shorten(start.title)}" is written.',
            )
            taken.discard(start.line)
            taken.add(above.number)
            start = replace(start, line=above.number)
        moved.append(start)
    return moved


def _moved_up_to_attachment_headings(
    starts: list[ClauseStart], document: ParsedDocument, repairs: _Repairs
) -> list[ClauseStart]:
    """Move a start up one line when the line above is the heading of an attachment.

    "Exhibit A: Access Security Requirements", and below it the first paragraph. The
    model sometimes points at the paragraph, and not always with a title, so the rule
    above cannot catch every case.

    The line above is taken for such a heading only when two things hold. It is
    Exhibit, Appendix, Schedule or Annex, then an identifier, then a colon or the end
    of the line. And the line before it ends a sentence, so the heading does not
    continue one. A reference that wraps onto a new line fails both tests:
    "... set forth in" and then "Appendix C of this Agreement in accordance with ...".

    A start that is on such a heading itself stays where it is. The line above it can
    be the last row of a list of exhibits, and that row is not its heading.
    """
    content = document.content_lines
    position = {line.number: index for index, line in enumerate(content)}
    taken = {start.line for start in starts}

    moved = []
    for start in starts:
        index = position[start.line]
        above = content[index - 1] if index > 0 else None
        if (
            above is not None
            and above.number not in taken
            and _is_attachment_heading(content, index - 1)
            and not _ATTACHMENT_HEADING.match(content[index].text)
        ):
            repairs.fix(
                "clause_start_moved_to_heading",
                f"The clause start on line {start.line} was moved up to line {above.number}, "
                f'which is the heading "{shorten(above.text)}".',
            )
            taken.discard(start.line)
            taken.add(above.number)
            start = replace(start, line=above.number)
        moved.append(start)
    return moved


def _without_starts_in_mid_sentence(
    starts: list[ClauseStart], document: ParsedDocument, repairs: _Repairs
) -> list[ClauseStart]:
    """Drop a start that is really a reference to another clause, wrapped onto a new line.

    A sentence such as "... as set forth in Article / 2.3.1.2.3. During the ..." puts a
    clause number at the start of a line. The model can take that for the clause
    itself. It is recognised by two things together: the number is also the number of
    another start, and the line above ends with a word that cannot end a sentence.
    """
    content = document.content_lines
    position = {line.number: index for index, line in enumerate(content)}
    start_lines = {start.line for start in starts}
    uses = Counter(_number_key(start.number) for start in starts if start.number)

    kept = []
    for start in starts:
        index = position[start.line]
        above = content[index - 1] if index > 0 else None
        key = _number_key(start.number) if start.number else None
        if (
            key is not None
            and uses[key] > 1
            and above is not None
            and above.number not in start_lines
            and _ends_in_mid_sentence(above)
        ):
            repairs.fix(
                "clause_start_in_mid_sentence",
                f"The clause start on line {start.line} was dropped: its number "
                f'"{shorten(start.number)}" is also the number of another clause, and line '
                f"{above.number} above it ends in the middle of a sentence.",
            )
            # One start with this number always survives.
            uses[key] -= 1
        else:
            kept.append(start)
    return kept


def _from_the_first_line(
    starts: list[ClauseStart], document: ParsedDocument, repairs: _Repairs
) -> list[ClauseStart]:
    """Make sure the lines before the first start belong to a part, too."""
    first_line = document.content_lines[0].number
    if starts[0].line == first_line:
        return starts
    repairs.fix(
        "preamble_added",
        f"A preamble part was added from line {first_line}: the first clause start was on "
        f"line {starts[0].line}, so the lines before it belonged to no part.",
    )
    return [ClauseStart(line=first_line, type=ClauseType.PREAMBLE), *starts]


def _facts(answer: ExtractionAnswer, repairs: _Repairs) -> ContractFacts:
    metadata = answer.metadata
    if answer.metadata_unreadable:
        repairs.note(
            "metadata_unreadable",
            "The contract metadata in the LLM's answer could not be read and was left empty.",
        )

    contract_type: ContractType | None = None
    if metadata.contract_type:
        label = _as_label(metadata.contract_type)
        if label in ContractType:
            contract_type = ContractType(label)
        else:
            contract_type = ContractType.OTHER
            repairs.note(
                "unknown_contract_type",
                f'The unknown contract type "{shorten(metadata.contract_type)}" was '
                'replaced by "other".',
            )

    return ContractFacts(
        title=metadata.title,
        contract_type=contract_type,
        effective_date=metadata.effective_date,
        parties=tuple(Party(name=party.name, role=party.role) for party in metadata.parties),
    )


# --- Small helpers -------------------------------------------------------------------------


def _as_label(text: str | None) -> str:
    """ "Dispute Resolution" and "dispute-resolution" both mean "dispute_resolution"."""
    return re.sub(r"[\s-]+", "_", (text or "").strip().lower())


def _without_closing_dot(number: str | None) -> str | None:
    """ "5.1." and "5.1" are the same number; the model writes it either way."""
    if number is None:
        return None
    return number.rstrip(". ") or None


def _number_key(number: str) -> str:
    return squeeze(number.rstrip(". ").casefold())


def _is_attachment_heading(content: Sequence[Line], index: int) -> bool:
    """Whether the line at `index` looks like the heading of an attachment and stands
    like one: the line before it, if there is one, ends a sentence."""
    if not _ATTACHMENT_HEADING.match(content[index].text):
        return False
    return index == 0 or not _ends_in_mid_sentence(content[index - 1])


def _ends_in_mid_sentence(line: Line) -> bool:
    last_word = _LAST_WORD.search(line.text)
    return last_word is not None and last_word.group(1).lower() in _WORDS_THAT_CANNOT_END_A_SENTENCE


def _count(number: int, noun: str) -> str:
    return f"1 {noun}" if number == 1 else f"{number} {noun}s"
