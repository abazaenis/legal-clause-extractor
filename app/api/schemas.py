"""Request and response models of the HTTP API."""

from pydantic import BaseModel, Field


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
