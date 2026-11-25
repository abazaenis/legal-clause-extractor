"""A stand-in for a provider's HTTP API, so the LLM tests need no network and no key."""

from typing import Any

import httpx2

# What the fake API should do with one request: answer with a status code and a JSON
# body, or fail at the network level by raising an exception.
type Reply = tuple[int, dict[str, Any]] | Exception


def completion(
    content: str | None = '{"ok": true}',
    *,
    finish_reason: str = "stop",
    model: str = "served-model-2026",
    prompt_tokens: int = 120,
    completion_tokens: int = 30,
) -> dict[str, Any]:
    """The JSON body of a successful chat completion."""
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def api_error(message: str = "Something went wrong.") -> dict[str, Any]:
    """The JSON body providers send with an error status."""
    return {"error": {"message": message, "type": "error"}}


class FakeAPI:
    """Answers the SDK's HTTP requests and remembers them.

    Replies are used in order. The last one is repeated for any further request.
    """

    def __init__(self, *replies: Reply) -> None:
        self._replies = list(replies) or [(200, completion())]
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        reply = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        # Tells the SDK to retry almost at once, which keeps the retry tests fast.
        return httpx2.Response(status, json=body, headers={"retry-after-ms": "1"})

    def http_client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self))
