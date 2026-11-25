"""The pipeline: parse the file, ask the LLM where the clauses start, repair its answer."""

import threading

import pytest

from app.errors import LLMInvalidResponseError, LLMUnavailableError
from app.extraction.file_types import FileType
from app.extraction.labels import ClauseType, ContractType
from app.extraction.models import (
    ClauseStart,
    ContractFacts,
    ExtractionWarning,
    Line,
    LLMUsage,
    ParsedDocument,
    Party,
    SourceFile,
)
from app.extraction.parsers.factory import ParserFactory
from app.extraction.prompt import PROMPT_VERSION, system_prompt
from app.extraction.service import extract_document
from tests.fakes import FakeLLMProvider

SOURCE = SourceFile(filename="contract.pdf", file_type=FileType.PDF, data=b"%PDF-", sha256="x")
DOCUMENT = ParsedDocument(
    lines=(
        Line(number=1, text="Header", page=1, is_noise=True),
        Line(number=2, text="SERVICES AGREEMENT", page=1),
        Line(number=3, text="1. Term", page=1),
        Line(number=4, text="This Agreement runs for one year.", page=1),
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
        {"line": 3, "type": "term_and_termination", "number": "1", "title": "Term"},
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


def parsers(parser: RecordingParser) -> ParserFactory:
    return ParserFactory({FileType.PDF: parser})


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
                "3| 1. Term\n"
                "4| This Agreement runs for one year.\n"
                "</document>"
            ),
        }
    ]


async def test_result_says_where_the_parts_start_and_what_the_call_cost() -> None:
    draft = await extract_document(SOURCE, parsers(RecordingParser()), FakeLLMProvider(ANSWER))

    assert draft.starts == (
        ClauseStart(line=2, type=ClauseType.PREAMBLE),
        ClauseStart(line=3, type=ClauseType.TERM_AND_TERMINATION, number="1", title="Term"),
    )
    assert draft.facts == ContractFacts(
        title="SERVICES AGREEMENT",
        contract_type=ContractType.SERVICE_AGREEMENT,
        effective_date="March 1, 2021",
        parties=(Party(name="Acme Ltd", role="Supplier"),),
    )
    assert draft.warnings == ()
    assert draft.llm == LLMUsage(
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

    draft = await extract_document(SOURCE, parsers(parser), llm)

    assert llm.calls[0]["user"] == "<document>\n1| 1. Term\n</document>"
    assert [start.line for start in draft.starts] == [1]


async def test_warnings_of_the_parser_and_of_the_repair_are_both_reported() -> None:
    hidden_text = ExtractionWarning(code="hidden_text_removed", message="Removed hidden text.")
    parser = RecordingParser(
        ParsedDocument(lines=DOCUMENT.lines, page_count=1, warnings=(hidden_text,))
    )
    answer = {"clauses": [{"line": 2, "type": "preamble"}, {"line": 3, "type": "duration"}]}

    draft = await extract_document(SOURCE, parsers(parser), FakeLLMProvider(answer))

    # In the order of the pipeline: what the parser left out, then what was fixed.
    assert [warning.code for warning in draft.warnings] == [
        "hidden_text_removed",
        "unknown_clause_type",
    ]
    assert draft.starts[1].type is ClauseType.OTHER


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
        await extract_document(SOURCE, parsers(RecordingParser()), llm)

    assert raised.value.message.startswith("The LLM's answer could not be used")
    # The LLM is not asked a second time.
    assert len(llm.calls) == 1


async def test_failure_of_the_llm_is_passed_on() -> None:
    llm = FakeLLMProvider(LLMUnavailableError("The provider is unavailable."))

    with pytest.raises(LLMUnavailableError):
        await extract_document(SOURCE, parsers(RecordingParser()), llm)


async def test_parsing_runs_in_a_worker_thread() -> None:
    """Parsing is CPU-bound, so it must not run on the thread of the event loop."""
    parser = RecordingParser()

    await extract_document(SOURCE, parsers(parser), FakeLLMProvider(ANSWER))

    assert parser.thread_id is not None
    assert parser.thread_id != threading.get_ident()
