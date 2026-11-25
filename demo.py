"""End-to-end demo against a running server.

    uv run python demo.py                      # the sample NDA in demo/
    uv run python demo.py Contracts/*.pdf      # any PDF or Word files
    uv run python demo.py --url http://localhost:8000 contract.docx

For each file: upload it, upload it again (answered from storage), and read it back.
Then list everything stored, and show how a wrong file type is refused.
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

SAMPLE = Path(__file__).parent / "demo" / "sample-nda.pdf"

if sys.platform == "win32":
    os.system("")  # switches on ANSI colours in the Windows console
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def paint(text: object, *codes: int) -> str:
    if not USE_COLOR or not codes:
        return str(text)
    return f"\033[{';'.join(map(str, codes))}m{text}\033[0m"


BOLD, DIM = 1, 2
RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = 31, 32, 33, 34, 35, 36, 37
TYPE_COLORS = {
    "preamble": DIM, "definitions": CYAN, "confidentiality": MAGENTA, "payment": GREEN,
    "term_and_termination": YELLOW, "dispute_resolution": RED, "governing_law": BLUE,
    "signature_block": DIM, "exhibit": BLUE,
}  # fmt: skip


def banner(title: str) -> None:
    print("\n" + paint(f"━━ {title} " + "━" * max(0, 70 - len(title)), BOLD, CYAN))


def step(method: str, path: str, note: str = "") -> None:
    print(f"\n{paint('▶', BOLD, BLUE)} {paint(method, BOLD)} {path} {paint(note, DIM)}")


def status(response: httpx.Response, seconds: float) -> str:
    code = response.status_code
    color = GREEN if code == 201 else CYAN if code < 300 else RED
    timing = paint(f"in {seconds:.1f} s" if seconds >= 1 else f"in {seconds * 1000:.0f} ms", DIM)
    return f"{paint(f'{code} {response.reason_phrase}', BOLD, color)} {timing}"


def field(name: str, value: object) -> None:
    print(f"  {paint(f'{name:<10}', DIM)}{value}")


def short(text: str | None, width: int) -> str:
    text = text or ""
    return text if len(text) <= width else text[: width - 1] + "…"


def show_extraction(body: dict[str, Any]) -> None:
    meta, llm = body["metadata"], body["llm"]
    parties = " · ".join(f"{p['name'] or '—'} ({p['role'] or '—'})" for p in meta["parties"])
    field("Document", paint(body["document_id"], BOLD))
    field("Title", meta["title"] or paint("not found", DIM))
    field("Type", f"{meta['contract_type']}   {paint('effective', DIM)} {meta['effective_date']}")
    field("Parties", parties or paint("none", DIM))
    field(
        "LLM",
        paint(
            f"{llm['model']} · prompt {llm['prompt_version']} · {llm['input_tokens']:,} in / "
            f"{llm['output_tokens']:,} out tokens · {llm['duration_ms'] / 1000:.1f} s",
            DIM,
        ),
    )

    print(f"\n  {paint(f'Clauses ({len(body["clauses"])})', BOLD)}")
    print(paint(f"  {'#':>3}  {'lines':<9} {'type':<24} {'number':<8} title", DIM))
    for clause in body["clauses"]:
        kind = clause["type"]
        lines = f"{clause['start_line']}-{clause['end_line']}"
        label = paint(f"{kind:<24}", TYPE_COLORS.get(kind, WHITE))
        number, title = clause["number"] or "", short(clause["title"], 40)
        print(f"  {clause['position']:>3}  {lines:<9} {label} {number:<8} {title}")

    numbered = next((c for c in body["clauses"] if c["number"]), None)
    if numbered:
        print(
            f"\n  {paint(f'Text of clause {numbered["number"]}, copied from the document:', BOLD)}"
        )
        for line in numbered["text"].splitlines()[:4]:
            print(f"  {paint('│', DIM)} {line}")

    if body["warnings"]:
        print(f"\n  {paint(f'Warnings ({len(body["warnings"])})', BOLD, YELLOW)}")
        for warning in body["warnings"]:
            code = paint(warning["code"], YELLOW)
            print(f"  {paint('!', BOLD, YELLOW)} {code}  {short(warning['message'], 90)}")
    if body["page_noise"]:
        noise = ", ".join(f'"{n["text"]}" x{n["occurrences"]}' for n in body["page_noise"])
        print(f"\n  {paint('Left out as page noise:', DIM)} {noise}")


def show_error(response: httpx.Response) -> None:
    error = response.json()["error"]
    print(f"  {paint(error['code'], BOLD, RED)}  {error['message']}")


def demo_file(client: httpx.Client, path: Path) -> None:
    banner(path.name)
    data = path.read_bytes()

    step("POST", "/api/extract", f"({len(data) / 1024:.0f} KB, the LLM can take a minute or two)")
    started = time.perf_counter()
    response = client.post("/api/extract", files={"file": (path.name, data)})
    print(f"  {status(response, time.perf_counter() - started)}")
    if response.is_error:
        show_error(response)
        return
    body = response.json()
    show_extraction(body)

    step("POST", "/api/extract", "(the same file again)")
    started = time.perf_counter()
    again = client.post("/api/extract", files={"file": (path.name, data)})
    print(
        f"  {status(again, time.perf_counter() - started)}  "
        f"{paint('answered from storage, no LLM call', GREEN)}"
    )

    step("GET", f"/api/extractions/{body['document_id']}")
    started = time.perf_counter()
    read = client.get(f"/api/extractions/{body['document_id']}")
    same = (
        paint("same body as the upload", GREEN) if read.json() == body else paint("differs!", RED)
    )
    print(f"  {status(read, time.perf_counter() - started)}  {same}")


def demo_list(client: httpx.Client) -> None:
    banner("Everything stored")
    step("GET", "/api/extractions?limit=10")
    page = client.get("/api/extractions", params={"limit": 10}).json()
    print(paint(f"  {page['total']} stored, newest first", DIM))
    for item in page["items"]:
        count = item["warning_count"]
        warnings = f"{count:>2} warning" + ("" if count == 1 else "s")
        print(
            f"  {paint(item['document_id'][:8], DIM)}  {short(item['file']['filename'], 44):<44} "
            f"{item['clause_count']:>4} clauses  {warnings}"
        )


def demo_refusal(client: httpx.Client) -> None:
    banner("A file that is not a contract")
    step("POST", "/api/extract", "(notes.txt, plain text)")
    started = time.perf_counter()
    response = client.post("/api/extract", files={"file": ("notes.txt", b"Just some notes.")})
    print(f"  {status(response, time.perf_counter() - started)}")
    show_error(response)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("files", nargs="*", help="PDF or Word files (default: the sample NDA)")
    parser.add_argument("--url", default="http://localhost:8000", help="where the server runs")
    args = parser.parse_args()
    # PowerShell passes "Contracts/*.pdf" through as it is, so wildcards are expanded here.
    files = [
        path
        for pattern in args.files
        for path in (
            sorted(Path().glob(pattern)) if any(c in pattern for c in "*?[") else [Path(pattern)]
        )
    ]

    with httpx.Client(base_url=args.url, timeout=900) as client:
        try:
            client.get("/api/extractions", params={"limit": 1}).raise_for_status()
        except httpx.HTTPError:
            sys.exit(paint(f"No server at {args.url}. Start it first (see the README).", BOLD, RED))
        for path in files or [SAMPLE]:
            demo_file(client, path)
        demo_list(client)
        demo_refusal(client)
    print()


if __name__ == "__main__":
    main()
