"""The HTTP endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile, status

from app.api.dependencies import (
    LLMProviderDependency,
    ParserFactoryDependency,
    SettingsDependency,
)
from app.api.schemas import ErrorResponse, ExtractionDraftResponse
from app.api.upload import read_upload
from app.extraction.service import extract_document

router = APIRouter(prefix="/api", tags=["extractions"])

_UPLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    status.HTTP_413_CONTENT_TOO_LARGE: {
        "model": ErrorResponse,
        "description": "The file is larger than the configured limit.",
    },
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {
        "model": ErrorResponse,
        "description": "The file is not a PDF. The content decides, not the file name.",
    },
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "model": ErrorResponse,
        "description": "The `file` field is missing, or the file cannot be used: it is "
        "empty, password-protected, damaged, too long, or has no readable text.",
    },
    status.HTTP_502_BAD_GATEWAY: {
        "model": ErrorResponse,
        "description": "The LLM provider failed or refused the request, or its answer could "
        "not be used: it was empty, was not valid JSON, or needed more than 10% of its "
        "clause starts corrected.",
    },
    status.HTTP_504_GATEWAY_TIMEOUT: {
        "model": ErrorResponse,
        "description": "The LLM provider did not answer in time.",
    },
}


@router.post(
    "/extract",
    summary="Extract the clauses of a contract",
    responses=_UPLOAD_ERRORS,
)
async def extract(
    file: Annotated[UploadFile, File(description="The contract, as a PDF file.")],
    settings: SettingsDependency,
    parsers: ParserFactoryDependency,
    llm: LLMProviderDependency,
) -> ExtractionDraftResponse:
    """Upload one contract as multipart form data, in a field named `file`.

    The file is checked before anything else happens: its size, that it is not empty,
    and that its content is a PDF. It is then parsed into numbered lines, the LLM is
    asked where the clauses start, and its answer is corrected against the document.

    For now the response says where each part starts. The clause text and the stored
    result follow in a later change, and with them the `201` status.
    """
    source = await read_upload(file, max_bytes=settings.max_upload_bytes)
    draft = await extract_document(source, parsers, llm)
    return ExtractionDraftResponse.model_validate(draft)
