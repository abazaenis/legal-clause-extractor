"""Request and response models of the HTTP API."""

from pydantic import BaseModel, ConfigDict, Field

from app.extraction.labels import ClauseType, ContractType


class ErrorDetail(BaseModel):
    code: str = Field(
        description="Stable, machine-readable identifier of the error.",
        examples=["unsupported_file_type"],
    )
    message: str = Field(
        description="Explanation meant for a person.",
        examples=["Only PDF files are supported."],
    )


class ErrorResponse(BaseModel):
    """The body of every error response."""

    error: ErrorDetail


class _FromDomain(BaseModel):
    """Base for response models that are filled from the pipeline's own data types."""

    model_config = ConfigDict(from_attributes=True)


class PartyOut(_FromDomain):
    name: str | None = Field(description="Null when the contract leaves the name blank.")
    role: str | None = Field(
        description="The short name the contract defines for the party.", examples=["Customer"]
    )


class ContractMetadataOut(_FromDomain):
    title: str | None
    contract_type: ContractType | None
    effective_date: str | None = Field(
        description="As written in the contract.", examples=["March 1, 2021"]
    )
    parties: list[PartyOut]


class ClauseStartOut(_FromDomain):
    line: int = Field(description="The line of the document on which the part starts.")
    type: ClauseType = Field(description="A best-effort label of what the part is about.")
    number: str | None = Field(examples=["5.1"])
    title: str | None = Field(examples=["Term and Termination"])


class WarningOut(_FromDomain):
    code: str = Field(examples=["clause_start_moved_to_title"])
    message: str


class LLMUsageOut(_FromDomain):
    provider: str
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    duration_ms: int


class ExtractionDraftResponse(_FromDomain):
    """Where each part of the contract starts.

    This is an interim response. The text of each clause is not cut out of the
    document yet, and nothing is stored.
    """

    metadata: ContractMetadataOut = Field(validation_alias="facts")
    clauses: list[ClauseStartOut] = Field(
        validation_alias="starts",
        description="In document order. Each part runs until the next start.",
    )
    warnings: list[WarningOut] = Field(
        description="Everything that was left out of the document or corrected in the LLM's answer."
    )
    llm: LLMUsageOut
