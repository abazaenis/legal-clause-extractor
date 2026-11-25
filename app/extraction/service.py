"""The extraction pipeline, from a validated file to a stored result."""

import asyncio

from app.errors import LLMInvalidResponseError
from app.extraction.answer import AnswerFormatError, parse_answer
from app.extraction.models import ExtractionDraft, LLMUsage, SourceFile
from app.extraction.parsers.factory import ParserFactory
from app.extraction.prompt import PROMPT_VERSION, build_user_prompt, system_prompt
from app.extraction.repair import repair_answer
from app.llm.base import LLMProvider


async def extract_document(
    source: SourceFile, parsers: ParserFactory, llm: LLMProvider
) -> ExtractionDraft:
    """Run the extraction pipeline for one validated file.

    The file is parsed into numbered lines, the LLM is asked where the clauses start,
    and its answer is repaired against the document. The model is asked once: what is
    wrong with its answer is fixed in code and reported as a warning.

    Cutting the clause text and storing the result are added in later changes.
    """
    parser = parsers.for_file_type(source.file_type)
    # Parsing is CPU-bound. A worker thread keeps the event loop free for other requests.
    document = await asyncio.to_thread(parser.parse, source)

    response = await llm.complete(system=system_prompt(), user=build_user_prompt(document))
    try:
        answer = parse_answer(response.text)
    except AnswerFormatError as error:
        raise LLMInvalidResponseError(f"The LLM's answer could not be used. {error}") from error
    repaired = repair_answer(answer, document)

    return ExtractionDraft(
        starts=repaired.starts,
        facts=repaired.facts,
        # The parser's warnings first: they are about the file, the others about the answer.
        warnings=(*document.warnings, *repaired.warnings),
        llm=LLMUsage(
            provider=response.provider,
            model=response.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            duration_ms=response.duration_ms,
        ),
    )
