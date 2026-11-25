"""Every log line has the same shape, and none of them can carry the text of a contract."""

import logging
import re
from collections.abc import Iterator

import pytest
import structlog

from app.log_setup import configure_logging

# 2026-11-25 18:55:00 [info     ] llm_call_finished ...
STARTS_WITH_TIME_AND_LEVEL = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[(\w+) *\] ")


@pytest.fixture(autouse=True)
def restored_logging() -> Iterator[None]:
    """Logging is set up for the whole process. Put it back the way the test found it."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


def lines_of(capsys: pytest.CaptureFixture[str]) -> list[str]:
    """What was logged so far, line by line."""
    return capsys.readouterr().out.splitlines()


def fail_while_holding_contract_text() -> None:
    clause_text = "CONFIDENTIAL CLAUSE TEXT"
    raise RuntimeError(f"Could not cut {len(clause_text)} characters.")


def test_event_is_written_as_one_line_of_text(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO")

    structlog.get_logger("app.llm").info("llm_call_finished", model="fake-model", input_tokens=1000)

    (line,) = lines_of(capsys)
    # The time in UTC and the level, then the event, where it came from, and its fields.
    start = STARTS_WITH_TIME_AND_LEVEL.match(line)
    assert start is not None
    assert start.group(1) == "info"
    assert line[start.end() :].split() == [
        "llm_call_finished",
        "[app.llm]",
        "input_tokens=1000",
        "model=fake-model",
    ]


def test_traceback_is_logged_without_the_values_of_local_variables(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging()

    try:
        fail_while_holding_contract_text()
    except RuntimeError:
        structlog.get_logger("app.api").exception("unhandled_error", status_code=500)

    output = capsys.readouterr().out
    first_line = output.splitlines()[0]
    assert "[error" in first_line
    assert "unhandled_error" in first_line
    assert "status_code=500" in first_line
    # The traceback says where it failed and why. What the variables held is not in it.
    assert "in fail_while_holding_contract_text" in output
    assert "RuntimeError: Could not cut 24 characters." in output
    assert "CONFIDENTIAL" not in output


def test_http_libraries_are_never_logged_below_info() -> None:
    # Their DEBUG output describes requests in detail, and the requests carry contracts.
    configure_logging(level="DEBUG")

    assert logging.getLogger().level == logging.DEBUG
    for name in ("openai", "httpx2", "httpcore"):
        assert logging.getLogger(name).getEffectiveLevel() == logging.INFO

    # A stricter level applies to them like to everything else.
    configure_logging(level="ERROR")

    assert logging.getLogger("openai").getEffectiveLevel() == logging.ERROR
