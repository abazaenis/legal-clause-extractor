"""Request and response models of the HTTP API."""

import uuid
from datetime import date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Extraction
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


class FileOut(BaseModel):
    """Facts about the uploaded file. The file itself is not stored."""

    filename: str = Field(examples=["nda.pdf"])
    content_type: str = Field(examples=["application/pdf"])
    size_bytes: int
    page_count: int | None = Field(description="Null for formats that have no pages.")
    sha256: str = Field(
        description="Identifies the file. The same bytes always give the same document id."
    )


class PartyOut(BaseModel):
    name: str | None = Field(description="Null when the contract leaves the name blank.")
    role: str | None = Field(
        description="The short name the contract defines for the party.", examples=["Customer"]
    )


class ContractMetadataOut(BaseModel):
    """Facts about the contract. Each value is written in the document, or it is null."""

    title: str | None = Field(examples=["MUTUAL NON-DISCLOSURE AGREEMENT"])
    contract_type: ContractType | None = Field(description="A best-effort label.")
    effective_date: date | None = Field(
        description="Read by code from the date as the contract writes it."
    )
    parties: list[PartyOut]


class ClauseOut(BaseModel):
    """One part of the contract. Its text is copied from the document, never generated."""

    model_config = ConfigDict(from_attributes=True)

    position: int = Field(description="Zero-based order of the clause within the document.")
    type: ClauseType = Field(description="A best-effort label of what the part is about.")
    number: str | None = Field(
        description="As written at the start of the clause.", examples=["5.1"]
    )
    title: str | None = Field(examples=["Term and Termination"])
    text: str = Field(description="The lines of the clause, joined by line breaks.")
    start_line: int = Field(description="The first line of the clause in the document.")
    end_line: int = Field(description="The last line of the clause, included.")
    start_page: int | None = Field(description="Null for formats that have no pages.")
    end_page: int | None = Field(description="Null for formats that have no pages.")


class WarningOut(BaseModel):
    code: str = Field(examples=["clause_start_moved_to_title"])
    message: str


class PageNoiseOut(BaseModel):
    text: str = Field(
        description="The line as written. Where lines differ only in their numbers, the "
        "numbers are shown as `#`.",
        examples=["Page # of #"],
    )
    occurrences: int


class LLMUsageOut(BaseModel):
    provider: str
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    duration_ms: int


class ExtractionResponse(BaseModel):
    """A contract broken into its clauses, as it is stored."""

    document_id: uuid.UUID = Field(
        description="Identifies the document. Uploading the same file again gives the same id."
    )
    created_at: datetime = Field(description="When the stored result was produced, in UTC.")
    file: FileOut
    metadata: ContractMetadataOut
    clauses: list[ClauseOut] = Field(
        description="In document order. Together they hold every line of the document "
        "except the page noise."
    )
    warnings: list[WarningOut] = Field(
        description="Everything that was left out of the document, corrected in the LLM's "
        "answer, or could not be verified against the document."
    )
    page_noise: list[PageNoiseOut] = Field(
        description="Page headers, footers and page numbers that were left out of the clause text."
    )
    llm: LLMUsageOut

    @classmethod
    def from_row(cls, row: Extraction) -> Self:
        """Build the response from a stored extraction. The row is flat; the response groups it."""
        return cls(
            document_id=row.id,
            created_at=row.created_at,
            file=FileOut(
                filename=row.filename,
                content_type=row.content_type,
                size_bytes=row.size_bytes,
                page_count=row.page_count,
                sha256=row.sha256,
            ),
            metadata=ContractMetadataOut.model_validate(
                {
                    "title": row.title,
                    "contract_type": row.contract_type,
                    "effective_date": row.effective_date,
                    "parties": row.parties,
                }
            ),
            clauses=[ClauseOut.model_validate(clause) for clause in row.clauses],
            warnings=[WarningOut.model_validate(warning) for warning in row.warnings],
            page_noise=[PageNoiseOut.model_validate(noise) for noise in row.page_noise],
            llm=LLMUsageOut(
                provider=row.llm_provider,
                model=row.llm_model,
                prompt_version=row.prompt_version,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                duration_ms=row.llm_duration_ms,
            ),
        )
