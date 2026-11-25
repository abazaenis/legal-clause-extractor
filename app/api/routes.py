"""The HTTP endpoints."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Path, Query, Response, UploadFile, status

from app.api.dependencies import (
    ExtractionRepositoryDependency,
    LLMProviderDependency,
    ParserFactoryDependency,
    SettingsDependency,
)
from app.api.schemas import ErrorResponse, ExtractionListResponse, ExtractionResponse
from app.api.upload import read_upload
from app.errors import ExtractionNotFoundError
from app.extraction.service import extract_and_store

router = APIRouter(prefix="/api", tags=["extractions"])

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

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
        response.headers["Location"] = router.url_path_for(
            "get_extraction", document_id=str(stored.extraction.id)
        )
    else:
        response.status_code = status.HTTP_200_OK
    return ExtractionResponse.from_row(stored.extraction)


@router.get(
    "/extractions",
    summary="List the stored extractions",
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "`limit` or `offset` is not a number in the allowed range.",
        },
    },
)
async def list_extractions(
    extractions: ExtractionRepositoryDependency,
    limit: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="How many extractions to return.")
    ] = DEFAULT_PAGE_SIZE,
    offset: Annotated[
        int, Query(ge=0, description="How many extractions to skip, counted from the newest.")
    ] = 0,
) -> ExtractionListResponse:
    """Return one page of the stored extractions, newest first.

    Each item is a summary: the file, the contract metadata and the number of clauses.
    The clauses themselves come with `GET /api/extractions/{document_id}`. `total`
    says how many extractions there are in all, so a client knows when to stop.
    """
    page = await extractions.list_page(limit=limit, offset=offset)
    return ExtractionListResponse.from_page(page, limit=limit, offset=offset)


@router.get(
    "/extractions/{document_id}",
    summary="Get one stored extraction",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No extraction is stored for this document id.",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "The document id is not a UUID.",
        },
    },
)
async def get_extraction(
    document_id: Annotated[
        uuid.UUID, Path(description="The `document_id` that `POST /api/extract` returned.")
    ],
    extractions: ExtractionRepositoryDependency,
) -> ExtractionResponse:
    """Return the stored result of a contract, as `POST /api/extract` returned it."""
    extraction = await extractions.find_by_id(document_id)
    if extraction is None:
        raise ExtractionNotFoundError(document_id)
    return ExtractionResponse.from_row(extraction)
