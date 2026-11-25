"""The HTTP endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, File, Query, Response, UploadFile, status

from app.api.dependencies import (
    ExtractionRepositoryDependency,
    LLMProviderDependency,
    ParserFactoryDependency,
    SettingsDependency,
)
from app.api.schemas import ErrorResponse, ExtractionResponse
from app.api.upload import read_upload
from app.extraction.service import extract_and_store

router = APIRouter(prefix="/api", tags=["extractions"])

_EXTRACT_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_201_CREATED: {
        "headers": {
            "Location": {
                "description": "Where the stored result can be read again.",
                "schema": {"type": "string", "examples": ["/api/extractions/{document_id}"]},
            }
        },
    },
    status.HTTP_200_OK: {
        "model": ExtractionResponse,
        "description": "The file was extracted before. Its stored result is returned and no "
        "LLM call is made, or with `overwrite=true` the stored result was replaced.",
    },
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
    status_code=status.HTTP_201_CREATED,
    summary="Extract the clauses of a contract",
    response_description="The file was extracted, and the result was stored.",
    responses=_EXTRACT_RESPONSES,
)
async def extract(
    file: Annotated[UploadFile, File(description="The contract, as a PDF file.")],
    response: Response,
    settings: SettingsDependency,
    parsers: ParserFactoryDependency,
    llm: LLMProviderDependency,
    extractions: ExtractionRepositoryDependency,
    overwrite: Annotated[
        bool,
        Query(
            description="Extract the file again even if it has a stored result. The new "
            "result replaces the stored one and the document id stays the same."
        ),
    ] = False,
) -> ExtractionResponse:
    """Upload one contract as multipart form data, in a field named `file`.

    The file is checked before anything else happens: its size, that it is not empty,
    and that its content is a PDF. It is then parsed into numbered lines, an LLM says
    where the clauses start, and code cuts the text of every clause out of the document.

    A file is identified by its content. Uploading the same file again returns the
    stored result with `200`, without calling the LLM.
    """
    source = await read_upload(file, max_bytes=settings.max_upload_bytes)
    stored = await extract_and_store(
        source, overwrite=overwrite, parsers=parsers, llm=llm, extractions=extractions
    )
    if stored.created:
        response.headers["Location"] = f"{router.prefix}/extractions/{stored.extraction.id}"
    else:
        response.status_code = status.HTTP_200_OK
    return ExtractionResponse.from_row(stored.extraction)
