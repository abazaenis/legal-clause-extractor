"""The HTTP endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile, status

from app.api.dependencies import SettingsDependency
from app.api.schemas import ErrorResponse
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
        "description": "The `file` field is missing, or the file is empty.",
    },
    status.HTTP_501_NOT_IMPLEMENTED: {
        "model": ErrorResponse,
        "description": "The file is valid, but clause extraction is not built yet.",
    },
}


@router.post(
    "/extract",
    status_code=status.HTTP_201_CREATED,
    summary="Extract the clauses of a contract",
    responses=_UPLOAD_ERRORS,
)
async def extract(
    file: Annotated[UploadFile, File(description="The contract, as a PDF file.")],
    settings: SettingsDependency,
) -> None:
    """Upload one contract as multipart form data, in a field named `file`.

    The file is checked before anything else happens: its size, that it is not empty,
    and that its content is a PDF.
    """
    source = await read_upload(file, max_bytes=settings.max_upload_bytes)
    await extract_document(source)
