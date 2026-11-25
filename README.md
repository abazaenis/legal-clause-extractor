# Contract Clause Extractor

A FastAPI service that splits a contract, PDF or Word, into its clauses and stores the result. An LLM only says where each clause starts. Code cuts the text out of the document, so every clause is a verbatim copy of the source.

## How it works

```mermaid
flowchart TD
    client(["Client"]) -->|"POST /api/extract with one PDF or Word file"| checks["Check the upload:<br/>size, not empty, file type by content"]
    checks -->|"rejected"| reject["413, 415 or 422"]
    checks -->|"accepted"| known{"Same file<br/>extracted before?"}
    known -->|"yes"| stored["200 with the stored result,<br/>no LLM call"]
    known -->|"no"| parse["Parse into numbered lines,<br/>page headers, footers and page numbers flagged"]
    parse -->|"encrypted, damaged, too long or no text"| unreadable["422, or 413 for a Word file<br/>that unpacks to over 50 MB"]
    parse --> llm["One LLM call:<br/>clause start lines, labels and metadata"]
    llm -->|"provider error or unusable answer"| fail["502, or 504 on a timeout"]
    llm --> repair["Repair the answer in code:<br/>one warning per fix, no second call"]
    repair -->|"more than 10% of the clause starts needed fixing"| fail
    repair --> cut["Cut the text of each clause out of the document"]
    cut --> verify["Check every string the model returned against that text"]
    verify --> store[("SQLite: extractions and clauses,<br/>written in one transaction")]
    store --> created["201 with the full result"]
    reader(["Client"]) -->|"GET /api/extractions and GET /api/extractions/{document_id}"| store
```

Only one step calls the LLM. Everything else is deterministic code.

## Running it

Copy `.env.example` to `.env` and set `LLM_API_KEY`. Then:

```bash
docker build -t clause-extractor .
docker run --rm -p 8000:8000 --env-file .env -v clause-extractor-data:/data clause-extractor
```

The API is at `http://localhost:8000`, with interactive documentation at `/docs`.

- **The container runs the migrations, then the server.** It runs as a non-root user, from an image without tests or build tools.
- **The API key is passed at start**, so it is in no image layer.
- **The volume on `/data` keeps the database** when the container is removed.

Settings come from the environment or `.env`:

| Setting | Default | Meaning |
|---|---|---|
| `LLM_API_KEY` | none | Required. The server does not start without it |
| `LLM_PROVIDER` | `deepseek` | `deepseek`, or `openai_compatible` for any other API with the OpenAI wire format |
| `LLM_MODEL` | `deepseek-flash` | The model to ask |
| `LLM_BASE_URL` | the provider's own | Required for `openai_compatible` |
| `LLM_REASONING_EFFORT` | the provider's default | Passed to the provider as it is. `.env.example` sets `high` |
| `LLM_MAX_OUTPUT_TOKENS` | `128000` | An answer that hits the limit is unusable. Reasoning counts towards it |
| `LLM_TIMEOUT_SECONDS` | `300` | For one attempt |
| `LLM_MAX_RETRIES` | `2` | Retries after the first attempt, with backoff |
| `DATABASE_URL` | `sqlite+aiosqlite:///./extractions.db` | Any SQLAlchemy async URL |
| `MAX_UPLOAD_BYTES` | 20 MB | Largest upload accepted |
| `MAX_PAGES` | `300` | Longest document accepted. For Word, 3,000 characters count as a page |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

## Results on the sample contracts

All seven samples in one run, with `deepseek-flash`, reasoning effort `high` and prompt version 3:

| Sample | Size | Clauses | LLM time | Tokens in / out |
|---|---|---|---|---|
| Three NDAs | 3 pages each | 11 each | 13–17 s | 3.3k / 2.5k–3.5k |
| Service agreement | 23 pages | 59 | 120 s | 16k / 28k |
| Interconnection agreement | 176 pages | 287 | 123 s | 52k / 36k |
| Service agreement, Word | 102 lines | 56 | 69 s | 6k / 16k |
| Master agreement, Word | 113 lines | 99 | 111 s | 10k / 28k |

Every sample was also read by hand, and each of its clause starts listed:
- **All 520 clear-cut starts were found**, and every clause number matched the document.
- **Every clause text is a verbatim copy**, and the clauses add up to the document, line for line.
- **The 14 extra parts are judgement calls**: nine unnumbered headings inside a section or an appendix, such as "Indemnification." and "RECITALS", and five template placeholders (`${T:...}`) in the master agreement.

What else the runs showed:
- **Reasoning effort trades time for parts found.** On the service agreement, `none` took 6 s instead of 150 s. It found 53 parts instead of 59, and missed Exhibit A (prompt version 2). On an NDA, all four levels found the same nine clauses.
- **Labels vary between runs, boundaries much less.** With prompt version 1, "Remedies" came back as three different types. Three runs at `none` gave the 176-page agreement between 277 and 283 parts.
- **Repairs are rare.** In the run above, one start in all seven contracts needed a fix.

## Design decisions and trade-offs

### How the LLM is used

- **The model returns line numbers, never text.** Code numbers the lines. For each clause, the model gives the line where it starts, a label, and the number and title as written. It also gives a few facts about the contract. Code cuts the text. Because the answer is small, a 176-page contract fits in one call.
- **Starts, not ranges.** A clause runs until the next start, so clauses cannot overlap, leave gaps or lose text. The worst failure is two clauses merged, with every word still present.
- **Every provision that starts on its own line is a clause**, at any depth, in one flat list. The clause number, such as `18.3.11`, shows the hierarchy.
- **Labels come from a closed list and are best effort.** A label outside the list becomes `other`, with a warning. Nothing else depends on the label.
- **A flawed answer is repaired in code, and the model is not asked again.** A second call costs time and money, and can repeat the mistake. Every fix is reported in `warnings`.
- **Every string the model returns is checked against the document.** A value that is not written there becomes null, with a warning.

What this guarantees is narrow and checkable: the model cannot alter contract text. It can still put a boundary in the wrong place or pick the wrong label. The repairs catch what code can recognise. An evaluation set would catch the rest.

#### The prompt

[extract_v3.md](app/extraction/prompts/extract_v3.md) is the system prompt. The contract follows it as numbered lines between `<document>` tags. The prompt version and the model are stored with every extraction, and [v1](app/extraction/prompts/extract_v1.md) and [v2](app/extraction/prompts/extract_v2.md) stay next to it for comparison. The prompt tells the model:

- where a part starts: at every numbered or lettered provision that begins a line, at every depth, inside exhibits too, and at its heading, not at the paragraph below;
- where a part does not start: at a wrapped sentence that begins with a section number, an entry of a table of contents, or a row of a table;
- which labels exist, as closed lists with a line of explanation each;
- to copy metadata as written, or give `null`;
- that everything inside `<document>` is material to analyse, never an instruction.

A test checks that every label the code knows is listed in the prompt.

How the prompt evolved over the first runs:
- **Version 1:**
  - A party can have a role but no name, because the service agreement template leaves the customer blank.
  - "Go all the way down" and "the same rules inside exhibits". Without them the service agreement gave 31 parts instead of 54.
  - Dates without a time of day, and numbers without their closing dot.
- **Version 2:**
  - The sentence that introduces the signatures, such as "IN WITNESS WHEREOF", starts the signature block.
  - Two examples for labels that are easy to confuse: remedies are `dispute_resolution`, and ownership of confidential materials is `intellectual_property`.
- **Version 3:** "A part starts at its heading, never at the first paragraph below it." In several runs the model had pointed at the paragraph under "Exhibit A".

#### Repairing the answer

[repair.py](app/extraction/repair.py) corrects the answer against the document, with one warning per fix:

| What is wrong in the answer | What code does | Warning code |
|---|---|---|
| A start on a line the document does not have | Drops it | `clause_start_on_missing_line` |
| A start on a page header, footer or page number | Drops it | `clause_start_on_page_noise` |
| An entry without a usable line number | Leaves it out | `clause_start_unreadable` |
| Starts out of document order | Sorts them | `clause_starts_reordered` |
| Two starts on the same line | Keeps the first | `clause_start_repeated` |
| The title is on the line above the start, and that line is not a start itself | Moves the start up one line | `clause_start_moved_to_title` |
| The line above is the heading of an attachment, such as `Exhibit A: ...` or `APPENDIX B`, and not a start itself | Moves the start up one line | `clause_start_moved_to_heading` |
| The number is also another start's number, and the line above ends in the middle of a sentence | Drops the start | `clause_start_in_mid_sentence` |
| The first start is not on the first line | Adds a preamble part from the first line | `preamble_added` |

**When the answer is refused.** If more than 10% of the clause starts needed one of these fixes, the request fails with `502`. The rest of such an answer cannot be trusted. One fix is always allowed, so a nine-clause NDA is not refused over a single correction.

Some problems are reported but do not count towards the 10%, because they are not about boundaries:
- a label outside the list, which becomes `other` (`unknown_clause_type`);
- an unknown contract type;
- metadata that cannot be read.

A closing dot on a clause number (`5.1.`) is trimmed without a warning.

Three of the rules come from mistakes the model made on the samples:

- **A start under its heading.** The model sometimes pointed at the first line under "Exhibit A: Access Security Requirements". The heading then ended up in the part before. The start is moved up in two cases:
  - the line above holds the title the model gave;
  - the line above is an attachment heading, and the line before that ends a sentence. An attachment heading is Exhibit, Appendix, Schedule or Annex, then an identifier, then a colon or the end of the line.

  Beginning with the word is not enough. In the long agreement, "Appendix C of this Agreement…" ends a sentence, and a looser rule moved the clause below it. A start on such a heading itself stays put, because the line above can be the last row of a list of exhibits.
- **A reference wrapped onto a new line.** In "…as set forth in Article", followed by a line "2.3.1.2.3. During the suspension period", the model took the second line for clause 2.3.1.2.3. Such a start is dropped when two things hold:
  - its number is also the number of another start;
  - the line above ends on a word that cannot end a sentence, such as `Article`, `Section`, `the`, `of` or `pursuant`.

  A missing full stop is not enough, because headings have none either.

Replayed over nine saved answers, these rules changed only that one start.

#### Cutting the text and checking the strings

[clauses.py](app/extraction/clauses.py) cuts the text of each clause: the lines from its start to the line before the next start, joined by line breaks, without the page noise. A test cuts every sample at arbitrary lines and checks that the clauses add up to the document.

[verification.py](app/extraction/verification.py) then looks up every string the model returned:

| Value | Where it must be written | If it is not |
|---|---|---|
| Clause number | At the start of the clause's first line, or right after its first word: `X-1` in `Exhibit X-1:` | `null` and `clause_number_not_verified` |
| Clause title | In the first three lines of the clause | `null` and `clause_title_not_verified` |
| Contract title | Anywhere in the document | `null` and `title_not_verified` |
| Party name, party role | Anywhere in the document | `null` and `party_not_verified` |
| Effective date | Anywhere in the document, in a form code can read as a date | `null` and `effective_date_not_verified` or `effective_date_not_parsed` |

- **Whitespace and the style of quotes and dashes may differ. Nothing else may.** A value that is found is stored in the document's own characters.
- **A number must be the whole number.** `1` does not pass for a line that starts with `11.` or `1.2`.
- **Dates written with numbers only are not guessed.** `03/04/2021` is 4 March in one country and 3 April in another.
- **These checks do not count towards the 10%.** They are about labels, not boundaries.

Over 19 saved answers, all 1,674 numbers, 1,419 titles and 126 metadata values passed. The rules about the first word and about quotes come from the only three values that failed in the first runs, all of them real text:
- `X-1` and `X-2`, for lines that start with `Exhibit X-1:` and `Exhibit X-2:`;
- a title with straight quotes, where the document has curly ones.

### LLM provider and model

- **The provider is configuration, not code.** Four small pieces make that work:
  - The pipeline depends on one interface (Strategy).
  - Each provider API has an adapter that maps its SDK and its errors to the service's own (Adapter).
  - A factory builds the adapter named in the environment. It stops the server at startup on an unknown name or a missing key (Factory).
  - FastAPI injects the provider, which is also how the tests use a fake (dependency injection).
- **Adapters know nothing about contracts.** Text goes in; text and token counts come out. Another OpenAI-compatible provider is only a change of settings. A provider with its own API is one new class and one line in the factory.
- **DeepSeek is the default, chosen for cost.** It is called through the official `openai` SDK. For real client contracts, the provider has to be chosen on its data terms: region, retention, and use for training. That is why it is configuration.
- **The answer is requested in JSON mode and validated here**, not by a provider's strict-schema feature, so every provider gets the same checks and repairs.
- **Retries, backoff and the timeout of one attempt come from the SDK.** There is no overall deadline: one upload can wait through three attempts of up to 300 s each.
- **Prompts and answers are never logged.** They contain contract text.

### Document parsers

- **One parser per format, picked by file type.** Every parser returns the same output: the lines in reading order, each with a number, its text, an optional page and a noise flag. The rest of the pipeline never knows the format. Page fields are optional, because Word has no pages.
- **Parsing runs in a worker thread**, so it does not block the event loop.
- **Hidden text never reaches the prompt.** White text on a white background is invisible to a reader, which makes it the obvious place to plant instructions for an LLM. The PDF parser leaves it out and quotes it in `warnings`. One sample NDA carries such a line: "Updated 2016". White text on a coloured shape or an image is visible, and is kept. Text with Word's own "hidden" attribute is treated the same way.
- **Lines are grouped with a tolerance of 5 points.** pdfplumber's default of 3 puts a clause number in a larger font on a line of its own, above its title.

#### Page noise in PDFs

A pure function flags running headers, footers and page numbers. It never removes or renumbers a line. The rules are conservative, because a stray footer inside a clause is harmless and lost contract text is not:

1. A line in the top or bottom three lines of at least half of the pages is a header or footer. The lines must be identical apart from a page label such as "Page 3 of 9", so table rows and numbered headings never match.
2. The outermost remaining line of a page is a page number if it is written like one: `Page 4`, `- 3 -`, `ii`, `B-7`.
3. Outermost lines that are bare numbers are page numbers if they count upwards from page to page.

On the samples this flags every header, footer and page number, and nothing else:
- 2 of 87 lines on an NDA;
- 45 of 822 on the service agreement;
- 528 of 3,514 on the 176-page agreement.

The response lists what was left out: each line once, with a count, and lines that differ only in their numbers grouped with `#`, as in `Page # of #`.

#### Choice of PDF library

I chose pdfplumber over pypdfium2.
- **Speed favours pypdfium2.** It is 20 to 40 times faster: 0.17 s against 6.4 s on the 176-page agreement.
- **Reading order decides it.** pypdfium2 returns text in file order, so footers and page numbers land at the top or in the middle of a page. It put the footer last on 0 of the 176 pages; pdfplumber did on all 176. Numbering the lines and finding the page noise both need visual order.
- **The cost is small.** A few seconds of parsing is little next to the LLM call.

PyMuPDF was ruled out by its AGPL licence.

#### Word documents

A `.docx` file is a ZIP of XML parts. The parser reads three of them with the standard library: the text, the styles and the numbering.

- **A paragraph is a line, and so is a table row**, with its cells joined by ` | `. There are no pages.
- **Clause numbers are computed.** In a Word contract the numbers are usually not in the text. A paragraph only says which list and level it is on, often through its style, and Word works out `1.`, `1.1` and `(a)` when it shows the page. [docx_numbering.py](app/extraction/parsers/docx_numbering.py) does the same. The sample master agreement has 90 such numbers and none in its text. The `numbers_computed` warning says how many were computed.
- **No Word library:**
  - `python-docx` has no number labels, and it skips content controls, which hold 117 of the 140 paragraphs of that sample.
  - `docx2python` ignores numbers that come from styles.
- **Page numbers typed into the body are flagged.** The other sample was converted from a web page and keeps its page numbers as paragraphs, three of them in mid-sentence. A run is flagged when it has at least three bare numbers that count up by one, with text between them, and does not start at 1. Two kinds of run are left alone:
  - numbers with four digits, because years count up too;
  - a run starting at 1, because it could be a list typed by hand.
- **What is not read is reported.** Text boxes, footnotes and the deleted text of tracked changes are not read, and a warning says so. Tracked changes are read as accepted. Comments are not contract text and are ignored.
- **The file is not trusted:**
  - a part may unpack to 50 MB at most, or the upload is refused with `413`;
  - a ZIP directory that understates a size fails its checksum;
  - a part that declares a document type is refused before any entity expands;
  - nothing is written to disk.

Not handled:
- **White text, and hidden text that comes from a style.** The parser cannot see the page.
- **The old `.doc` format.** It is refused, with a message to save the file as `.docx` or PDF.
- **A paragraph that holds a heading and its first sub-clause.** It stays one clause, with every word present.
- **Corners of numbering that the format leaves open**, such as several lists sharing one definition.

### Data model

Two tables:
- **`extractions`**, one row per file. It holds:
  - the file facts, with the SHA-256 of the bytes, which is unique;
  - the contract metadata;
  - the LLM run: model, prompt version, tokens and duration;
  - the warnings and the page noise.
- **`clauses`**, one row per clause in document order: position, type, number, title, text, and lines and pages in the source.

I kept two tables on purpose:
- a result is written once and never changed;
- nothing queries inside the JSON columns;
- the API serves a document and its extraction as one resource.

The splits I would make in production, and what would trigger each:

| Split | Trigger |
|---|---|
| `documents` and `extraction_runs` (one document, many runs) | Re-processing a document after a better prompt or model, a run history for audit, ids that stay stable across runs |
| `parties` table in place of the JSON column | Searching contracts by party |
| `llm_calls` table, one row per call | Chunking, when one extraction makes many LLM calls |
| `clause_types` lookup table | Clause types that customers can configure |

- **One result per file, written once.** Uploading the same file again returns the stored result. Re-processing a file is what the `documents` and `extraction_runs` split would add. With that split, duplicate detection would be scoped per customer, so one customer's upload never reveals another customer's document.
- **Flat clauses.** There is no level and no parent. The number shows the hierarchy.
- **One transaction per result.** A file has its complete result or none.
- **No connection is held during the LLM call.** The lookup and the write use separate short sessions.
- **Two uploads of the same new file at once both call the LLM.** The first write is stored, and both requests get that result back.
- **Migrations run with SQLite foreign keys off.** SQLite changes a table by rebuilding it, and with foreign keys on, dropping the old `extractions` table cascades into every clause. The second migration did exactly that. A test now runs the migrations over existing rows and checks that they survive.

### API

- **One file per request.** `POST /api/extract` takes one PDF or Word (`.docx`) file as multipart form data. It extracts synchronously and returns `201` with the full result and a `Location` header.
- **The size limit is enforced while the body arrives.** FastAPI reads the whole upload before the endpoint runs, so a check in the endpoint would come too late. Instead, a middleware:
  - refuses a declared `Content-Length` over the limit before reading anything;
  - counts the body as it streams, so a missing or wrong `Content-Length` cannot get around the limit.
- **The file type is decided by content.** The file name and the declared type are ignored. An old `.doc` file and a password-protected Word file are refused, with a message that says what to do.
- **Same file, same id.** A repeat upload, recognised by its SHA-256, returns the stored result with `200` and makes no LLM call. It stores nothing, so the file name of the first upload stays.
- **One resource, read two ways.** `GET /api/extractions/{document_id}` returns exactly the body the upload returned. `GET /api/extractions` returns summaries without clause text: the file, the metadata, and the clause and warning counts.
- **A failed extraction stores nothing**, so the next upload of the file tries again.
- **Pagination data is in the response body**, as `items`, `total`, `limit` and `offset`. That way it is typed, documented in OpenAPI, and hard to miss.
  - The list is newest first, with the id breaking ties, so the order is the same on every request.
  - `limit` is 20 by default and 100 at most.
- **One error shape**: `{"error": {"code": "...", "message": "..."}}`.
  - `413`, `415` and `422` for the upload.
  - `422` for an id that is not a UUID, or a page parameter out of range.
  - `404` for an unknown id.
  - `502` when the LLM provider fails, or its answer cannot be used: it is empty, is not a JSON object, was cut off at the output limit, has no usable clause start, or needed more than 10% of its starts fixed.
  - `504` when the provider does not answer in time.

### Logging

- **One line of text per event**: time in UTC, level, event, logger, and the fields as `key=value`. Library lines use the same format.
- **Text, not JSON.** A person reads these logs at a terminal. The events are still named and have fields, through structlog, so JSON for a log collector is one formatting step away.
- **Every request has an id**, on every line logged for it and in the `X-Request-ID` header. A caller may send its own id, made of letters, digits, `.`, `_` and `-`, at most 64 characters.
- **Contract content is never logged.** That rules out the contract's text, the prompt and the model's answer. It also rules out file names, which tend to name the parties, and warning messages, which quote the document. A test plants marker words in all of these and checks that no log line contains one.
- **Three leaks that need no log call are closed too:**
  - tracebacks are logged without local variables;
  - database errors are logged without statement values;
  - the HTTP libraries are never logged below `INFO`.
- **Every request ends with `request_finished`.** A refused request also logs `request_refused` with its error code, and an error nobody handled logs `unhandled_error` with its traceback.

One upload of a sample NDA:

```text
2026-11-25 19:00:51 [info     ] document_parsed                [app.extraction.service] duration_ms=160 file_type=pdf lines=83 noise_lines=2 pages=3 request_id=demo-upload-1 size_bytes=285372
2026-11-25 19:00:51 [info     ] HTTP Request: POST https://api.deepseek.com/chat/completions "HTTP/1.1 200 OK" [httpx2] request_id=demo-upload-1
2026-11-25 19:00:52 [info     ] llm_call_finished              [app.llm.adapters.openai_compatible] duration_ms=1685 finish_reason=stop input_tokens=3326 model=deepseek-flash output_tokens=312 provider=deepseek request_id=demo-upload-1
2026-11-25 19:00:52 [info     ] extraction_finished            [app.extraction.service] clauses=11 prompt_version=v3 request_id=demo-upload-1 warnings={'hidden_text_removed': 1}
2026-11-25 19:00:52 [info     ] extraction_stored              [app.extraction.service] created=True document_id=01a21415-0956-77ff-8bf7-4a6b5472df05 request_id=demo-upload-1
2026-11-25 19:00:52 [info     ] request_finished               [app.api.middleware] duration_ms=1890 method=POST path=/api/extract request_id=demo-upload-1 status_code=201
```

## What I'd do with more time

The submission is scoped to the core flow. These are left out on purpose. The first two would come next.

### First: divide and conquer for long contracts

The whole contract goes to the LLM in one call. A long contract is slow, and one failed call wastes all of it. Next I would:

1. Split the numbered lines into chunks at page breaks.
2. Send the chunks to the LLM in parallel, a few at a time.
3. Validate each answer on its own, and retry only a chunk that failed.
4. Merge the answers by line number.

Because the model returns line numbers, the merge is a sort. The care is at chunk edges, where a chunk can begin in the middle of a clause.

### Second: tables and side-by-side columns

This is a known bug in the PDF parser. Text that sits side by side on a page is merged into one line, left to right. It already shows in the samples:
- the signature blocks of all five PDFs, such as "Name: Victor Mullin Name: Paul Haering";
- the contact table in Exhibit X-2 of the service agreement;
- the milestone table and the interconnection table in Appendices B and C of the 176-page agreement.

Every word is still there, but rows and columns are interleaved. No numbered clause in the samples runs in columns. A contract whose clauses did would be cut wrongly while looking fine.

The fix is to detect columns and table cells from the positions of the words, before lines are built, and to read each one on its own.

### Ingestion and processing

- **Direct-to-storage uploads.** A pre-signed URL sends large files straight to object storage. That also gives virus scanning, encryption at rest and retention an obvious home.
- **Background extraction.** An upload event starts a job that moves through `uploaded → processing → completed / failed`. `POST` returns `202`, and the client polls or gets a webhook. The synchronous endpoint is the biggest compromise in this submission: it holds a connection open for work that can outlast a proxy timeout.
- **Batch uploads**, where each file gets its own result or its own error.
- **Cancellation** that also stops the LLM call.
- **Documents and extraction runs.** See the data model.
- **More of Word:**
  - read footnotes into the clause that refers to them;
  - detect white text;
  - check the computed numbers against documents rendered by Word.
- **Scanned documents.** OCR as a separate path, marked as lower confidence, split by page and run in parallel.
- **Filled-in form fields.** In the service agreement's notice form, the typed values are interleaved with the underscores of the blanks: `P_.O._ B_o_x_ 7_6_6`.

### Extraction quality

- **A labelled evaluation set**, scored on every prompt or model change. The hand-checked starts behind "Results on the sample contracts" are a beginning.
- **Model-flagged page noise** for one-off stamps that the rules miss, with every removed line still reported.
- **Human review** of the least certain segments, with the corrections fed back into the evaluation set.

### API

- **Clause hierarchy.** Store each clause's level and parent, and return the clauses flat or nested.
- **Search and filtering** by clause type and contract metadata, within a document and across documents.
- **RFC 9457 problem details** in place of the custom error shape.
- **Model and reasoning effort per request.**
- **Streaming with Server-Sent Events.** The starts arrive in document order, so each clause can be pushed as soon as the next start arrives. It stays provisional until the whole answer passes validation.

### Running it in production

- **Authentication and tenant isolation.** Every extraction belongs to a tenant, and every query is scoped to it.
- **Rate limits and quotas** that count tokens, not only requests.
- **Circuit breaker and provider fallback** around the LLM call.
- **An overall deadline for the LLM call.**
- **JSON logs**, plus metrics, tracing and alerting: latency and tokens per stage, validation failures, and cost per document.
- **PostgreSQL and keyset pagination.** With offsets, a new extraction shifts every later page, and deep pages are slow.
- **PDF parsing in a process pool.** pdfplumber is pure Python, so a long parse slows other requests.
- **Health checks.**
- **Prompt-injection hardening.**
  - Already in place: the model has no tools, its output is checked against the source, and hidden text is removed (white text on white in PDFs, Word's hidden attribute).
  - Still open: white text in Word, invisible text rendering, tiny fonts, text off the page or under a shape, and text in the colour of its background.
  - Also needed: adversarial test contracts and monitoring of the outputs.
- **CORS** for known origins, once a browser client exists, and **response compression** at the edge.

### Product

- **A review UI** that shows the extracted clauses next to the original, with each clause highlighted in the source.
