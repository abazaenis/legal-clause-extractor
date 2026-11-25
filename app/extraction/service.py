"""The extraction pipeline, from a validated file to a stored result."""

import asyncio

from app.db.repository import ExtractionRepository, StoredExtraction
from app.errors import LLMInvalidResponseError
from app.extraction.answer import AnswerFormatError, parse_answer
from app.extraction.clauses import cut_clauses, summarise_page_noise
from app.extraction.models import ExtractionResult, LLMUsage, SourceFile
from app.extraction.parsers.factory import ParserFactory
from app.extraction.prompt import PROMPT_VERSION, build_user_prompt, system_prompt
from app.extraction.repair import repair_answer
from app.extraction.verification import verify_clauses, verify_facts
from app.llm.base import LLMProvider


async def extract_and_store(
    source: SourceFile,
    *,
    overwrite: bool,
    parsers: ParserFactory,
    llm: LLMProvider,
    extractions: ExtractionRepository,
) -> StoredExtraction:
    """Return the stored extraction of a file, running the pipeline only when needed.

    A file is identified by the hash of its bytes, and has one stored result. A file
    that was extracted before costs no LLM call: its stored result is returned. With
    `overwrite`, the file is extracted again and the new result takes the place of
    the stored one, under the same id.
    """
    if not overwrite:
        stored = await extractions.find_by_sha256(source.sha256)
        if stored is not None:
            return StoredExtraction(extraction=stored, created=False)

    result = await extract_document(source, parsers, llm)
    return await extractions.save(source, result)


async def extract_document(
    source: SourceFile, parsers: ParserFactory, llm: LLMProvider
) -> ExtractionResult:
    """Run the extraction pipeline for one validated file. Nothing is stored here.

    1. The parser for the file type turns the file into numbered lines.
    2. The LLM says on which lines the parts of the contract start. It is asked once.
    3. Its answer is repaired against the document, and every fix is reported.
    4. The text of each clause is cut out of the document by its line numbers.
    5. Every string the model returned is checked against that text.

    Only step 2 uses the model. The rest is code, so the same answer for the same
    file always gives the same result.
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

    clauses, clause_warnings = verify_clauses(cut_clauses(repaired.starts, document))
    metadata, metadata_warnings = verify_facts(repaired.facts, document)

    return ExtractionResult(
        page_count=document.page_count,
        metadata=metadata,
        clauses=clauses,
        warnings=(
            *document.warnings,
            *repaired.warnings,
            *clause_warnings,
            *metadata_warnings,
        ),
        page_noise=summarise_page_noise(document),
        llm=LLMUsage(
            provider=response.provider,
            model=response.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            duration_ms=response.duration_ms,
        ),
    )
