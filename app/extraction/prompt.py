"""Builds what is sent to the LLM: a fixed system prompt and the contract as numbered lines."""

from functools import cache
from pathlib import Path

from app.extraction.models import ParsedDocument

# Stored with every extraction. Change it whenever the prompt changes, by adding a new
# file: results produced by different prompts must stay distinguishable. Earlier
# versions stay in the prompts folder, so the history of the prompt can be read.
PROMPT_VERSION = "v3"

_PROMPT_FILE = Path(__file__).parent / "prompts" / f"extract_{PROMPT_VERSION}.md"


@cache
def system_prompt() -> str:
    """The instructions for the model. The same for every contract."""
    return _PROMPT_FILE.read_text(encoding="utf-8").strip()


def build_user_prompt(document: ParsedDocument) -> str:
    """Render a document as numbered lines between `<document>` tags.

    Each line keeps the number the parser gave it, so the model's answer points
    straight back into the document. Page noise is left out; its line numbers are
    simply missing. A `[page N]` marker shows where a page begins, for formats that
    have pages.
    """
    rendered = ["<document>"]
    current_page: int | None = None
    for line in document.content_lines:
        if line.page is not None and line.page != current_page:
            rendered.append(f"[page {line.page}]")
            current_page = line.page
        rendered.append(f"{line.number}| {line.text}")
    rendered.append("</document>")
    return "\n".join(rendered)
