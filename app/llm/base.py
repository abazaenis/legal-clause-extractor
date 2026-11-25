"""The interface every LLM provider implements.

This package knows nothing about contracts. It sends text and returns text. The
prompt, the parsing of the answer and every check on it belong to the extraction code.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """A provider's answer, in the application's own terms."""

    # Kept out of the repr: the answer is derived from contract text, and a repr
    # can end up in a log line or a traceback.
    text: str = field(repr=False)
    provider: str
    # The model that actually answered, as the provider reports it.
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: int


class LLMProvider(Protocol):
    """What the extraction pipeline needs from an LLM, and nothing more.

    There is one adapter per provider API. Each one translates this interface to a
    vendor SDK, and translates the vendor's errors to the application's own.
    """

    async def complete(self, *, system: str, user: str) -> LLMResponse:
        """Send a system prompt and a user prompt, and return the model's answer.

        The answer is requested as a JSON object. It is returned as text and is not
        parsed or checked here.

        Raises `LLMTimeoutError`, `LLMUnavailableError`, `LLMRequestRejectedError`
        or `LLMInvalidResponseError`.
        """
        ...

    async def aclose(self) -> None:
        """Release the provider's network connections."""
        ...
