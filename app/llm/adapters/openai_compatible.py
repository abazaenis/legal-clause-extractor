"""Adapter for every API that speaks the OpenAI chat-completions protocol."""

import logging
import time

import openai
from openai import AsyncOpenAI

from app.errors import (
    LLMInvalidResponseError,
    LLMRequestRejectedError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.base import LLMResponse

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider:
    """Talks to DeepSeek, and to any other API that shares the OpenAI wire format.

    Retries with backoff and the per-attempt timeout are the SDK's, configured on the
    client this adapter is given. The adapter adds what the SDK cannot know:

    - the request the pipeline needs: two messages, answered as a JSON object;
    - the vendor's errors translated into the application's own;
    - an answer that arrived but cannot be used (empty, or cut off) reported as an error.

    Prompts and answers are never logged. They contain contract text.
    """

    def __init__(
        self,
        *,
        name: str,
        client: AsyncOpenAI,
        model: str,
        max_output_tokens: int,
        reasoning_effort: str | None = None,
    ) -> None:
        self._name = name
        self._client = client
        self._model = model
        self._max_output_tokens = max_output_tokens
        # Sent in the body as it is. Providers accept different values here, and the
        # SDK's own parameter only allows OpenAI's.
        self._extra_body = {"reasoning_effort": reasoning_effort} if reasoning_effort else None

    async def complete(self, *, system: str, user: str) -> LLMResponse:
        started = time.perf_counter()
        try:
            completion = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                max_tokens=self._max_output_tokens,
                extra_body=self._extra_body,
            )
        except openai.APITimeoutError as error:
            # Listed first: the SDK's timeout error is a kind of connection error.
            self._log_failure("timed out", error)
            raise LLMTimeoutError("The LLM provider did not answer in time.") from error
        except (
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ) as error:
            self._log_failure("is unavailable", error)
            raise LLMUnavailableError(
                "The LLM provider is unavailable. Try again later."
            ) from error
        except openai.APIStatusError as error:
            # Any other 4xx: the provider understood the request and refused it.
            self._log_failure("rejected the request", error)
            raise LLMRequestRejectedError(
                "The LLM provider rejected the request. The server's LLM settings need attention."
            ) from error
        except openai.OpenAIError as error:
            self._log_failure("failed", error)
            raise LLMUnavailableError(
                "The LLM provider is unavailable. Try again later."
            ) from error

        duration_ms = round((time.perf_counter() - started) * 1000)
        choice = completion.choices[0] if completion.choices else None
        text = (choice.message.content or "") if choice else ""
        usage = completion.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        logger.info(
            "LLM call finished: provider=%s model=%s input_tokens=%d output_tokens=%d "
            "duration_ms=%d finish_reason=%s",
            self._name,
            completion.model,
            input_tokens,
            output_tokens,
            duration_ms,
            choice.finish_reason if choice else None,
        )

        if choice is not None and choice.finish_reason == "length":
            raise LLMInvalidResponseError(
                "The LLM's answer was cut off at the output limit before it was complete."
            )
        if not text.strip():
            raise LLMInvalidResponseError("The LLM provider returned an empty answer.")

        return LLMResponse(
            text=text,
            provider=self._name,
            model=completion.model or self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
        )

    async def aclose(self) -> None:
        await self._client.close()

    def _log_failure(self, what: str, error: openai.OpenAIError) -> None:
        # Only the kind of error and its status. The provider's message can quote the request.
        logger.warning(
            "LLM provider %s %s: error=%s status=%s",
            self._name,
            what,
            type(error).__name__,
            getattr(error, "status_code", None),
        )
