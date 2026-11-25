"""Logging: one event per line, as text, in the same shape from every part of the service.

Application code logs an event name and key-value pairs:

    log = structlog.get_logger(__name__)
    log.info("llm_call_finished", model="deepseek-flash", input_tokens=3210)

which comes out as one line: the time in UTC, the level, the event, the logger, the fields.

    2026-11-25 18:55:00 [info     ] llm_call_finished  [app.llm] input_tokens=3210 model=...

Contract content is never logged. That means the text of a contract, the prompt built
from it and the model's answer, and also two things that carry such text: file names,
which tend to name the parties, and the messages of warnings, which quote the document.
What is logged is counts, sizes, durations, ids and codes.
"""

import logging
import sys
from typing import TextIO

import structlog
from structlog.typing import Processor

_HANDLER_NAME = "app.log_setup"

# Libraries whose DEBUG output describes HTTP requests and responses in detail. What
# they print there is theirs to decide, and the requests carry contracts.
_NEVER_BELOW_INFO = ("openai", "httpx2", "httpcore")


def configure_logging(*, level: str = "INFO") -> None:
    """Send every log line of the process through one handler, in one format.

    That covers events logged through structlog and lines from libraries that use the
    standard `logging` module, such as uvicorn and SQLAlchemy. It is safe to call
    more than once: each call replaces what the call before it set up.
    """
    # Applied to every line, wherever it comes from.
    shared: list[Processor] = [
        # The request id, bound when the request came in.
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=True),
    ]
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            # Hands the event over to the handler below, which writes the line.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # Loggers are looked up again on every use, so tests can capture their events.
        cache_logger_on_first_use=False,
    )

    handler = _StdoutHandler()
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            # For lines that did not come through structlog.
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(
                    # Colours for a person at a terminal, plain text into a file or a pipe.
                    colors=sys.stdout.isatty(),
                    # A traceback is written without the values of local variables.
                    # In this service a local variable can hold the text of a contract.
                    exception_formatter=structlog.dev.plain_traceback,
                ),
            ],
        )
    )

    root = logging.getLogger()
    for earlier in [h for h in root.handlers if h.get_name() == _HANDLER_NAME]:
        root.removeHandler(earlier)
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs handlers of its own. Without them its lines reach ours.
    for name in ("uvicorn", "uvicorn.error"):
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True
    # The line per request is written by the application's own middleware, which
    # knows the request id.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False

    for name in _NEVER_BELOW_INFO:
        logging.getLogger(name).setLevel(max(logging.INFO, root.level))


class _StdoutHandler(logging.StreamHandler[TextIO]):
    """Writes to standard output, wherever that points at the moment of writing.

    A plain `StreamHandler` keeps the stream it was created with. Test runners and
    some servers replace `sys.stdout` later, and the lines would go to the old one.
    """

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)
