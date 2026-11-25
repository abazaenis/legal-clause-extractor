"""End-to-end demo against a running server. Needs Python 3.12+ and nothing else.

    python demo.py                    # every PDF and Word file in the demo/ folder
    python demo.py Contracts/*.pdf    # or the files you name
    python demo.py --url http://localhost:8000

For each file: upload it, upload it again (answered from storage), and read it back.
Then list everything stored, and show how a wrong file type is refused.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEMO_DIR = Path(__file__).parent / "demo"
DOCUMENTS = {".pdf", ".docx"}

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


# --- A small HTTP client, so the demo needs no packages ------------------------------------


@dataclass
class Reply:
    code: int
    reason: str
    body: Any
    seconds: float


class Api:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")

    def get(self, path: str) -> Reply:
        return self._send(urllib.request.Request(self.url + path))

    def upload(self, filename: str, data: bytes) -> Reply:
        boundary = uuid.uuid4().hex
        name = filename.replace('"', "%22")
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        )
        body = head.encode() + data + f"\r\n--{boundary}--\r\n".encode()
        content_type = f"multipart/form-data; boundary={boundary}"
        request = urllib.request.Request(
            self.url + "/api/extract", data=body, headers={"Content-Type": content_type}
        )
        return self._send(request)

    def _send(self, request: urllib.request.Request) -> Reply:
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                code, reason, raw = response.status, response.reason, response.read()
        except urllib.error.HTTPError as error:
            code, reason, raw = error.code, error.reason, error.read()
        return Reply(code, reason, json.loads(raw), time.perf_counter() - started)


# --- Output --------------------------------------------------------------------------------


def banner(title: str) -> None:
    print("\n" + paint(f"━━ {title} " + "━" * max(0, 70 - len(title)), BOLD, CYAN))


def step(method: str, path: str, note: str = "") -> None:
    print(f"\n{paint('▶', BOLD, BLUE)} {paint(method, BOLD)} {path} {paint(note, DIM)}")


def status(reply: Reply) -> str:
    color = GREEN if reply.code == 201 else CYAN if reply.code < 300 else RED
    seconds = reply.seconds
    timing = paint(f"in {seconds:.1f} s" if seconds >= 1 else f"in {seconds * 1000:.0f} ms", DIM)
    return f"{paint(f'{reply.code} {reply.reason}', BOLD, color)} {timing}"


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


def show_error(reply: Reply) -> None:
    error = reply.body["error"]
    print(f"  {paint(error['code'], BOLD, RED)}  {error['message']}")


# --- The demo ------------------------------------------------------------------------------


def demo_file(api: Api, path: Path) -> None:
    banner(path.name)
    data = path.read_bytes()

    step("POST", "/api/extract", f"({len(data) / 1024:.0f} KB, the LLM can take a minute or two)")
    reply = api.upload(path.name, data)
    print(f"  {status(reply)}")
    if reply.code >= 400:
        show_error(reply)
        return
    show_extraction(reply.body)

    step("POST", "/api/extract", "(the same file again)")
    again = api.upload(path.name, data)
    print(f"  {status(again)}  {paint('answered from storage, no LLM call', GREEN)}")

    document_id = reply.body["document_id"]
    step("GET", f"/api/extractions/{document_id}")
    read = api.get(f"/api/extractions/{document_id}")
    same = read.body == reply.body
    note = paint("same body as the upload", GREEN) if same else paint("differs!", RED)
    print(f"  {status(read)}  {note}")


def demo_list(api: Api) -> None:
    banner("Everything stored")
    step("GET", "/api/extractions?limit=10")
    page = api.get("/api/extractions?limit=10").body
    print(paint(f"  {page['total']} stored, newest first", DIM))
    for item in page["items"]:
        count = item["warning_count"]
        warnings = f"{count:>2} warning" + ("" if count == 1 else "s")
        print(
            f"  {paint(item['document_id'][:8], DIM)}  {short(item['file']['filename'], 44):<44} "
            f"{item['clause_count']:>4} clauses  {warnings}"
        )


def demo_refusal(api: Api) -> None:
    banner("A file that is not a contract (refused on purpose)")
    step("POST", "/api/extract", "(notes.txt, plain text: the API should refuse it)")
    reply = api.upload("notes.txt", b"Just some notes.")
    print(f"  {status(reply)}")
    show_error(reply)


def files_to_upload(patterns: list[str]) -> list[Path]:
    """The files named, with wildcards expanded here because PowerShell does not expand
    them. Without any, every PDF and Word file in the demo/ folder."""
    if not patterns:
        return sorted(p for p in DEMO_DIR.iterdir() if p.suffix.lower() in DOCUMENTS)
    return [
        path
        for pattern in patterns
        for path in (
            sorted(Path().glob(pattern)) if any(c in pattern for c in "*?[") else [Path(pattern)]
        )
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("files", nargs="*", help="PDF or Word files (default: those in demo/)")
    parser.add_argument("--url", default="http://localhost:8000", help="where the server runs")
    args = parser.parse_args()

    api = Api(args.url)
    try:
        api.get("/api/extractions?limit=1")
    except urllib.error.URLError, OSError:
        sys.exit(paint(f"No server at {args.url}. Start it first (see the README).", BOLD, RED))

    files = files_to_upload(args.files)
    if not files:
        sys.exit(paint("No PDF or Word files to upload. Put some in demo/.", BOLD, RED))
    for path in files:
        demo_file(api, path)
    demo_list(api)
    demo_refusal(api)
    print()


if __name__ == "__main__":
    main()
