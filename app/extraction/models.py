"""Plain data types passed between the steps of the extraction pipeline."""

from dataclasses import dataclass, field

from app.extraction.file_types import FileType


@dataclass(frozen=True, slots=True)
class SourceFile:
    """An uploaded file that passed the upload checks and is ready to be parsed."""

    filename: str
    file_type: FileType
    # Kept out of the repr so contract content never ends up in a log line or traceback.
    data: bytes = field(repr=False)
    # Identifies the file: the same bytes always give the same hash.
    sha256: str

    @property
    def size_bytes(self) -> int:
        return len(self.data)
