"""The pipeline: parse the file, ask the LLM, repair its answer, cut and check the clauses."""

import threading
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.db.repository import ExtractionRepository
from app.db.session import create_session_factory
from app.errors import LLMInvalidResponseError, LLMUnavailableError
from app.extraction.file_types import FileType
from app.extraction.labels import ClauseType, ContractType
from app.extraction.models import (
    ContractMetadata,
    ExtractedClause,
    ExtractionWarning,
    Line,
    LLMUsage,
    PageNoise,
    ParsedDocument,
    Party,
    SourceFile,
)
from app.extraction.parsers.factory import ParserFactory
from app.extraction.prompt import PROMPT_VERSION, system_prompt
from app.extraction.service import extract_and_store, extract_document
from tests.fakes import FakeLLMProvider

SOURCE = SourceFile(filename="contract.pdf", file_type=FileType.PDF, data=b"%PDF-", sha256="x")
DOCUMENT = ParsedDocument(
    lines=(
        Line(number=1, text="Page 1 of 1", page=1, is_noise=True),
        Line(number=2, text="SERVICES AGREEMENT", page=1),
        Line(number=3, text="between Acme Ltd (the “Supplier”) and the Customer.", page=1),
        Line(number=4, text="1. Term", page=1),
        Line(number=5, text="This Agreement starts on March 1, 2021.", page=1),
    ),
    page_count=1,
)
ANSWER = {
    "metadata": {
        "title": "SERVICES AGREEMENT",
        "contract_type": "service_agreement",
        "effective_date": "March 1, 2021",
        "parties": [{"name": "Acme Ltd", "role": "Supplier"}],
    },
    "clauses": [
        {"line": 2, "type": "preamble", "number": None, "title": None},
        {"line": 4, "type": "term_and_termination", "number": "1", "title": "Term"},
    ],
}


class RecordingParser:
    """A parser that returns a fixed document and remembers how it was called."""

    def __init__(self, document: ParsedDocument = DOCUMENT) -> None:
        self.document = document
        self.sources: list[SourceFile] = []
        self.thread_id: int | None = None

    def parse(self, source: SourceFile) -> ParsedDocument:
        self.sources.append(source)
        self.thread_id = threading.get_ident()
        return self.document


def parsers(parser: RecordingParser | None = None) -> ParserFactory:
    return ParserFactory({FileType.PDF: parser or RecordingParser()})


# --- The pipeline, without storage --------------------------------------------------------------


async def test_file_is_parsed_and_sent_to_the_llm_as_numbered_lines() -> None:
    parser = RecordingParser()
    llm = FakeLLMProvider(ANSWER)

    await extract_document(SOURCE, parsers(parser), llm)

    assert parser.sources == [SOURCE]
    assert llm.calls == [
        {
            "system": system_prompt(),
            "user": (
                "<document>\n"
                "[page 1]\n"
                "2| SERVICES AGREEMENT\n"
                "3| between Acme Ltd (the “Supplier”) and the Customer.\n"
                "4| 1. Term\n"
                "5| This Agreement starts on March 1, 2021.\n"
                "</document>"
            ),
        }
    ]


async def test_result_holds_the_clauses_with_their_text_and_the_checked_metadata() -> None:
    result = await extract_document(SOURCE, parsers(), FakeLLMProvider(ANSWER))

    assert result.clauses == (
        ExtractedClause(
            position=0,
            type=ClauseType.PREAMBLE,
            number=None,
            title=None,
            text="SERVICES AGREEMENT\nbetween Acme Ltd (the “Supplier”) and the Customer.",
            start_line=2,
            end_line=3,
            start_page=1,
            end_page=1,
        ),
        ExtractedClause(
            position=1,
            type=ClauseType.TERM_AND_TERMINATION,
            number="1",
            title="Term",
            text="1. Term\nThis Agreement starts on March 1, 2021.",
            start_line=4,
            end_line=5,
            start_page=1,
            end_page=1,
        ),
    )
    assert result.metadata == ContractMetadata(
        title="SERVICES AGREEMENT",
        contract_type=ContractType.SERVICE_AGREEMENT,
        effective_date=date(2021, 3, 1),
        parties=(Party(name="Acme Ltd", role="Supplier"),),
    )
    assert result.page_count == 1
    assert result.page_noise == (PageNoise(text="Page 1 of 1", occurrences=1),)
    assert result.warnings == ()
    assert result.llm == LLMUsage(
        provider="fake",
        model="fake-model",
        prompt_version=PROMPT_VERSION,
        input_tokens=1000,
        output_tokens=50,
        duration_ms=7,
    )


async def test_document_without_pages_goes_through_the_same_pipeline() -> None:
    parser = RecordingParser(ParsedDocument(lines=(Line(number=1, text="1. Term"),)))
    llm = FakeLLMProvider()

    result = await extract_document(SOURCE, parsers(parser), llm)

    assert llm.calls[0]["user"] == "<document>\n1| 1. Term\n</document>"
    assert result.page_count is None
    assert [(clause.text, clause.start_page) for clause in result.clauses] == [("1. Term", None)]


async def test_warnings_come_in_the_order_of_the_pipeline() -> None:
    hidden_text = ExtractionWarning(code="hidden_text_removed", message="Removed hidden text.")
    parser = RecordingParser(
        ParsedDocument(lines=DOCUMENT.lines, page_count=1, warnings=(hidden_text,))
    )
    answer = {
        "metadata": {"title": "MASTER SERVICES AGREEMENT"},
        "clauses": [
            {"line": 2, "type": "preamble"},
            {"line": 4, "type": "duration", "number": "7", "title": "Term"},
        ],
    }

    result = await extract_document(SOURCE, parsers(parser), FakeLLMProvider(answer))

    assert [warning.code for warning in result.warnings] == [
        # What the parser left out of the file.
        "hidden_text_removed",
        # What was repaired in the model's answer.
        "unknown_clause_type",
        # What the model returned but the document does not say.
        "clause_number_not_verified",
        "title_not_verified",
    ]
    assert (result.clauses[1].type, result.clauses[1].number) == (ClauseType.OTHER, None)
    assert result.clauses[1].title == "Term"
    assert result.metadata.title is None


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        pytest.param("Sure! Here are the clauses.", "not valid JSON", id="not JSON"),
        pytest.param('{"clauses": []}', "lists no clause starts", id="empty"),
        pytest.param(
            {"clauses": [{"line": 2, "type": "preamble"}, {"line": 98}, {"line": 99}]},
            "2 of its 3 clause starts needed correcting",
            id="too much to fix",
        ),
    ],
)
async def test_answer_that_cannot_be_used_is_an_invalid_response(
    answer: dict[str, object] | str, reason: str
) -> None:
    llm = FakeLLMProvider(answer)

    with pytest.raises(LLMInvalidResponseError, match=reason) as raised:
        await extract_document(SOURCE, parsers(), llm)

    assert raised.value.message.startswith("The LLM's answer could not be used")
    # The LLM is not asked a second time.
    assert len(llm.calls) == 1


async def test_failure_of_the_llm_is_passed_on() -> None:
    llm = FakeLLMProvider(LLMUnavailableError("The provider is unavailable."))

    with pytest.raises(LLMUnavailableError):
        await extract_document(SOURCE, parsers(), llm)


async def test_parsing_runs_in_a_worker_thread() -> None:
    """Parsing is CPU-bound, so it must not run on the thread of the event loop."""
    parser = RecordingParser()

    await extract_document(SOURCE, parsers(parser), FakeLLMProvider(ANSWER))

    assert parser.thread_id is not None
    assert parser.thread_id != threading.get_ident()


# --- The pipeline with storage: one result per file ------------------------------------------


@pytest.fixture
def extractions(engine: AsyncEngine) -> ExtractionRepository:
    return ExtractionRepository(create_session_factory(engine))


async def test_new_file_is_extracted_and_stored(extractions: ExtractionRepository) -> None:
    stored = await extract_and_store(
        SOURCE,
        parsers=parsers(),
        llm=FakeLLMProvider(ANSWER),
        extractions=extractions,
    )

    assert stored.created
    found = await extractions.find_by_sha256(SOURCE.sha256)
    assert found is not None
    assert found.id == stored.extraction.id
    assert [clause.number for clause in found.clauses] == [None, "1"]


async def test_file_that_was_extracted_before_costs_no_llm_call(
    extractions: ExtractionRepository,
) -> None:
    parser = RecordingParser()
    llm = FakeLLMProvider(ANSWER)
    first = await extract_and_store(
        SOURCE, parsers=parsers(parser), llm=llm, extractions=extractions
    )

    again = await extract_and_store(
        SOURCE, parsers=parsers(parser), llm=llm, extractions=extractions
    )

    assert not again.created
    assert again.extraction.id == first.extraction.id
    # Neither parsed nor sent to the LLM a second time.
    assert len(parser.sources) == 1
    assert len(llm.calls) == 1


async def test_failed_extraction_stores_nothing(extractions: ExtractionRepository) -> None:
    llm = FakeLLMProvider("not JSON", ANSWER)

    with pytest.raises(LLMInvalidResponseError):
        await extract_and_store(SOURCE, parsers=parsers(), llm=llm, extractions=extractions)

    assert await extractions.find_by_sha256(SOURCE.sha256) is None
    # So the next upload of the same file is extracted, not answered from storage.
    stored = await extract_and_store(SOURCE, parsers=parsers(), llm=llm, extractions=extractions)
    assert stored.created


# --- What the pipeline logs --------------------------------------------------------------------


async def test_pipeline_logs_counts_and_codes_and_nothing_of_the_document(
    extractions: ExtractionRepository,
) -> None:
    answer = {
        "metadata": {"title": "SERVICES AGREEMENT"},
        "clauses": [
            {"line": 2, "type": "preamble"},
            {"line": 4, "type": "validity", "number": "1", "title": "Term"},
        ],
    }

    with capture_logs() as logs:
        stored = await extract_and_store(
            SOURCE,
            parsers=parsers(),
            llm=FakeLLMProvider(answer),
            extractions=extractions,
        )
        await extract_and_store(
            SOURCE,
            parsers=parsers(),
            llm=FakeLLMProvider(answer),
            extractions=extractions,
        )

    parsed, finished, saved, served = logs
    document_id = str(stored.extraction.id)
    assert parsed == {
        "event": "document_parsed",
        "log_level": "info",
        "file_type": "pdf",
        "size_bytes": 5,
        "pages": 1,
        "lines": 5,
        "noise_lines": 1,
        "duration_ms": parsed["duration_ms"],
    }
    assert finished == {
        "event": "extraction_finished",
        "log_level": "info",
        "clauses": 2,
        # The code of each warning and how often it occurred, without its message.
        "warnings": {"unknown_clause_type": 1},
        "prompt_version": PROMPT_VERSION,
    }
    assert saved == {
        "event": "extraction_stored",
        "log_level": "info",
        "document_id": document_id,
        "created": True,
    }
    # The same file again is answered from storage.
    assert served == {
        "event": "extraction_served_from_storage",
        "log_level": "info",
        "document_id": document_id,
    }
    # Not the file name, not the text, not the model's labels.
    logged = repr(logs)
    for secret in ("contract.pdf", "SERVICES AGREEMENT", "Acme", "validity", "Term"):
        assert secret not in logged, secret
