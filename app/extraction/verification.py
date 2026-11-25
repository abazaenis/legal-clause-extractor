"""Checks every string the model returned against the text of the document.

The model gives a number and a title for each clause, and a few facts about the
contract. It is told to copy them as written. Here each of them is looked up in the
place it claims to come from. A value that is not written there is left empty and
reported in a warning. It is never passed on.

A value that is found is replaced by the document's own characters. The model may
have changed the whitespace or the style of a quote, and what is stored is always
a copy of the source.
"""

import re
from collections.abc import Sequence
from dataclasses import replace

from app.extraction.dates import find_date
from app.extraction.models import (
    ContractFacts,
    ContractMetadata,
    ExtractedClause,
    ExtractionWarning,
    ParsedDocument,
    Party,
)
from app.extraction.text import find_written, pattern_for, shorten, squeeze

# A heading can wrap onto a second line, and a title can stand on the line below its
# label: "APPENDIX A" and then "NETWORK UPGRADE FACILITIES". In 19 answers to the
# sample contracts, every one of 1,419 titles was within the first two lines.
TITLE_LINES = 3

# A number is written at the start of the line, or right after its first word when
# the model leaves out the label: "X-1" for "Exhibit X-1:", "5" for "Section 5.".
_LABEL = r"(?:\S+ )?"
# What may not follow a number: more of a word or number ("1" in "11." and in "1st"),
# or a deeper level of numbering ("1" in "1.2").
_NUMBER_ENDS = r"(?!\w)(?!\.\d)"


def verify_clauses(
    clauses: Sequence[ExtractedClause],
) -> tuple[tuple[ExtractedClause, ...], tuple[ExtractionWarning, ...]]:
    """Keep the number and the title of a clause only where its own text shows them.

    The number must be written at the start of the clause's first line. The title
    must be written within its first `TITLE_LINES` lines.
    """
    warnings = []
    checked = []
    for clause in clauses:
        lines = clause.text.split("\n")
        number, title = clause.number, clause.title

        if number is not None:
            number = _number_as_written(lines[0], number)
            if number is None:
                warnings.append(
                    ExtractionWarning(
                        "clause_number_not_verified",
                        f'The number "{shorten(clause.number)}" given for the clause on line '
                        f"{clause.start_line} is not written at the start of that line, and "
                        "was left empty.",
                    )
                )
        if title is not None:
            title = find_written("\n".join(lines[:TITLE_LINES]), title)
            if title is None:
                warnings.append(
                    ExtractionWarning(
                        "clause_title_not_verified",
                        f'The title "{shorten(clause.title)}" given for the clause on line '
                        f"{clause.start_line} is not written in its first {TITLE_LINES} lines, "
                        "and was left empty.",
                    )
                )
        checked.append(replace(clause, number=number, title=title))
    return tuple(checked), tuple(warnings)


def verify_facts(
    facts: ContractFacts, document: ParsedDocument
) -> tuple[ContractMetadata, tuple[ExtractionWarning, ...]]:
    """Keep the facts about the contract that are written in the document.

    The effective date must be written in the document like every other value. It is
    then read as a date by code.
    """
    text = "\n".join(line.text for line in document.content_lines)
    warnings: list[ExtractionWarning] = []

    def as_written(value: str | None, code: str, what: str) -> str | None:
        if value is None:
            return None
        found = find_written(text, value)
        if found is None:
            warnings.append(
                ExtractionWarning(
                    code,
                    f'The {what} "{shorten(value)}" is not written in the document, and was '
                    "left empty.",
                )
            )
        return found

    title = as_written(facts.title, "title_not_verified", "contract title")

    parties = []
    for party in facts.parties:
        name = as_written(party.name, "party_not_verified", "party name")
        role = as_written(party.role, "party_not_verified", "party role")
        # A party with neither a name nor a role says nothing.
        if name is not None or role is not None:
            parties.append(Party(name=name, role=role))

    date_text = as_written(facts.effective_date, "effective_date_not_verified", "effective date")
    effective_date = find_date(date_text) if date_text is not None else None
    if date_text is not None and effective_date is None:
        warnings.append(
            ExtractionWarning(
                "effective_date_not_parsed",
                f'The effective date "{shorten(date_text)}" could not be read as a date, '
                "and was left empty.",
            )
        )

    metadata = ContractMetadata(
        title=title,
        contract_type=facts.contract_type,
        effective_date=effective_date,
        parties=tuple(parties),
    )
    return metadata, tuple(warnings)


def _number_as_written(first_line: str, number: str) -> str | None:
    pattern = pattern_for(number)
    if not pattern:
        return None
    match = re.match(f"{_LABEL}({pattern}){_NUMBER_ENDS}", squeeze(first_line))
    return squeeze(match.group(1)) if match else None
