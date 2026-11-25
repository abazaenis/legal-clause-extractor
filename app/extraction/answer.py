"""Reads the LLM's answer.

The model is asked for one JSON object. Reading it is deliberately forgiving: a
single bad entry must not cost the whole extraction. Only an answer that is empty
or is not JSON at all is refused here. Everything else is read as far as it goes,
and what was skipped is counted, so the repair step can warn about it.

Whether the line numbers exist in the document, and whether the labels are known,
is not decided here. That is the repair step's job, against the source.
"""

import json
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, field_validator


def _blank_to_none(value: object) -> object:
    """A model sometimes writes "" where it means "nothing". Both become None."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


type OptionalText = Annotated[str | None, BeforeValidator(_blank_to_none)]


class _AnswerModel(BaseModel):
    # Extra fields are ignored: a model that adds a comment has not broken anything.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class RawParty(_AnswerModel):
    # Null when the contract leaves the name blank, as a template does:
    # the party is then only known by its role.
    name: OptionalText = None
    # The short name the contract defines for the party, such as "Customer".
    role: OptionalText = None


class RawMetadata(_AnswerModel):
    """Facts about the contract as a whole, as the model wrote them. All optional."""

    title: OptionalText = None
    contract_type: OptionalText = None
    # As written in the contract, for example "March 1, 2021". Code parses the date later.
    effective_date: OptionalText = None
    parties: list[RawParty] = Field(default_factory=list)

    @field_validator("parties")
    @classmethod
    def _drop_parties_without_name_or_role(cls, parties: list[RawParty]) -> list[RawParty]:
        # An entry with neither says nothing about the contract.
        return [party for party in parties if party.name or party.role]


class RawClauseStart(_AnswerModel):
    """Where one part of the contract begins, as the model wrote it."""

    line: int
    # Any text is accepted here. The repair step maps an unknown label to "other".
    type: OptionalText = None
    # As written at the start of the line: "5.1", "(a)", "ARTICLE 5".
    number: OptionalText = None
    title: OptionalText = None


class ExtractionAnswer(BaseModel):
    """The model's answer, read as far as it could be read."""

    metadata: RawMetadata
    clauses: list[RawClauseStart]
    # Entries of `clauses` that were not an object with a whole-number `line`.
    unreadable_clauses: int = 0
    # True when `metadata` was present but could not be read, and was left empty.
    metadata_unreadable: bool = False


class AnswerFormatError(Exception):
    """The model's answer is empty or is not JSON, so nothing can be done with it."""


def parse_answer(text: str) -> ExtractionAnswer:
    """Read the model's answer, or raise `AnswerFormatError` if there is nothing to read."""
    try:
        data = json.loads(text)
    except ValueError:
        raise AnswerFormatError("The answer is not valid JSON.") from None
    if not isinstance(data, dict):
        raise AnswerFormatError("The answer is not a JSON object.")

    entries = data.get("clauses")
    if not isinstance(entries, list) or not entries:
        raise AnswerFormatError("The answer lists no clause starts.")

    clauses: list[RawClauseStart] = []
    for entry in entries:
        try:
            clauses.append(RawClauseStart.model_validate(entry))
        except ValidationError:
            continue
    if not clauses:
        raise AnswerFormatError("The answer lists no usable clause starts.")

    metadata, metadata_unreadable = _read_metadata(data.get("metadata"))
    return ExtractionAnswer(
        metadata=metadata,
        clauses=clauses,
        unreadable_clauses=len(entries) - len(clauses),
        metadata_unreadable=metadata_unreadable,
    )


def _read_metadata(value: object) -> tuple[RawMetadata, bool]:
    """Return the metadata, and whether it had to be left empty because it was unreadable."""
    if value is None:
        return RawMetadata(), False
    try:
        return RawMetadata.model_validate(value), False
    except ValidationError:
        return RawMetadata(), True
