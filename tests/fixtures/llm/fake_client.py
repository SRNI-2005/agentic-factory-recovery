"""Scripted LLM double (spec §11 Test Infrastructure).

responses: list -> popped in order regardless of prompt;
           dict -> value selected by the first key that is a substring of
           the user prompt. Exhaustion raises AssertionError so tests fail
           loudly instead of silently degrading.
"""


class FakeLLMClient:
    def __init__(self, responses) -> None:
        self._ordered = isinstance(responses, list)
        self._responses = responses
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((user, system))
        if self._ordered:
            if not self._responses:
                raise AssertionError("FakeLLMClient exhausted")
            return self._responses.pop(0)
        for key, value in self._responses.items():
            if key in user:
                return value
        raise AssertionError(f"no canned response matches {user!r}")
