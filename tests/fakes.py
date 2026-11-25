"""Stand-ins for the parts of the application that tests should not exercise for real."""

import json
from typing import Any

from app.llm.base import LLMResponse

# The smallest answer the pipeline accepts: one part that starts on the first line.
MINIMAL_ANSWER: dict[str, Any] = {
    "metadata": {"title": None, "contract_type": None, "effective_date": None, "parties": []},
    "clauses": [{"line": 1, "type": "other", "number": None, "title": None}],
}


class FakeLLMProvider:
    """An LLM provider that answers from a list and remembers what it was asked.

    Answers are used in order, and the last one is repeated. An answer can be a
    dictionary (sent as JSON), a string (sent as it is), or an exception to raise.
    """

    def __init__(self, *answers: dict[str, Any] | str | Exception) -> None:
        self._answers = list(answers) or [MINIMAL_ANSWER]
        self.calls: list[dict[str, str]] = []
        self.closed = False

    async def complete(self, *, system: str, user: str) -> LLMResponse:
        self.calls.append({"system": system, "user": user})
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(answer, Exception):
            raise answer
        return LLMResponse(
            text=answer if isinstance(answer, str) else json.dumps(answer),
            provider="fake",
            model="fake-model",
            input_tokens=1000,
            output_tokens=50,
            duration_ms=7,
        )

    async def aclose(self) -> None:
        self.closed = True
