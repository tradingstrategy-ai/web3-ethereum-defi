import io
import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from eth_defi import coloured_logging
from eth_defi.coloured_logging import is_running_inside_docker, should_do_colour_logging
from eth_defi.utils import setup_console_logging

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MAX_SHORT_RICH_LINE_LENGTH = 200
pytestmark = pytest.mark.usefixtures("restore_root_logger")


class DummyStream(io.StringIO):
    """Test stream with controlled TTY detection."""

    def __init__(self, *, is_tty: bool = False):
        super().__init__()
        self.is_tty = is_tty

    def isatty(self) -> bool:
        """Return the configured TTY state."""

        return self.is_tty


@pytest.fixture()
def restore_root_logger() -> Iterator[None]:
    """Restore root logging after each test.

    ``setup_console_logging()`` intentionally rewrites root logger handlers.
    Tests need to put the previous pytest logging configuration back so other
    tests in the same process are not affected.
    """

    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level

    yield

    for handler in root.handlers:
        if handler not in old_handlers:
            handler.close()
    root.handlers.clear()
    root.handlers.extend(old_handlers)
    root.setLevel(old_level)


def test_should_do_colour_logging_respects_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """NO_COLOR must override TTY and force-colour settings."""

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")

    assert should_do_colour_logging(DummyStream(is_tty=True)) is False


def test_should_do_colour_logging_detects_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker autodetection enables colours for non-TTY logs."""

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
    monkeypatch.setattr(coloured_logging, "is_running_inside_docker", lambda: True)

    assert should_do_colour_logging(DummyStream(is_tty=False)) is True


def test_should_do_colour_logging_can_disable_docker_autodetection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker log autodetection can be explicitly disabled."""

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
    monkeypatch.setattr(coloured_logging, "is_running_inside_docker", lambda: True)

    assert should_do_colour_logging(DummyStream(is_tty=False), autodetect_docker_log=False) is False


def test_setup_console_logging_detects_docker_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker log autodetection is enabled unless explicitly disabled."""

    stream = DummyStream(is_tty=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
    monkeypatch.setattr(coloured_logging, "is_running_inside_docker", lambda: True)

    root = setup_console_logging(default_log_level="info", stream=stream)

    assert root.handlers[0].__class__.__name__ == "EthDefiRichHandler"


def test_is_running_inside_docker_detects_marker_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker marker files are accepted as container indicators."""

    def fake_exists(path: Path) -> bool:
        """Pretend only the Docker marker file exists."""

        return str(path) == "/.dockerenv"

    monkeypatch.setattr(Path, "exists", fake_exists)

    assert is_running_inside_docker() is True


def test_setup_console_logging_installs_rich_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """FORCE_COLOR installs the Rich console handler."""

    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)

    root = setup_console_logging(default_log_level="info")

    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert handler.__class__.__name__ == "EthDefiRichHandler"
    assert handler.level == logging.INFO
    assert handler.console.width == coloured_logging.RICH_LOG_WIDTH
    assert handler.rich_tracebacks is False
    assert handler._log_render.show_path is False
    assert handler._log_render.omit_repeated_times is False


def test_setup_console_logging_accepts_integer_levels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integer logging levels can be used as defaults."""

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    root = setup_console_logging(default_log_level=logging.INFO)

    assert root.handlers[0].level == logging.INFO


def test_file_logging_does_not_filter_console_level(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Root logger level must let both file and console handlers receive logs."""

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    root = setup_console_logging(
        default_log_level="warning",
        std_out_log_level="debug",
        log_file=tmp_path / "test.log",
    )

    assert root.level == logging.DEBUG


def test_setup_console_logging_uses_plain_handler_when_colour_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NO_COLOR disables Rich even when colour is otherwise forced."""

    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", "1")

    root = setup_console_logging(default_log_level="info")

    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert handler.__class__.__name__ != "EthDefiRichHandler"
    assert isinstance(handler.formatter, logging.Formatter)


def test_rich_logging_outputs_ansi_and_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rich output contains ANSI escapes, module context and printf values."""

    stream = DummyStream(is_tty=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)

    setup_console_logging(default_log_level="info", stream=stream)

    logger = logging.getLogger("eth_defi.tests.logging")
    logger.info("Value %s count %d ratio %.2f", "abc", 123, 4.56)

    output = stream.getvalue()
    plain_output = ANSI_RE.sub("", output)
    assert "\x1b[" in output
    assert "INFO" in output
    assert "eth_defi.tests.logging" in output
    assert "Value abc count 123 ratio 4.56" in plain_output


def test_simplified_rich_logging_outputs_message_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simplified logging keeps message-only output when Rich is enabled."""

    stream = DummyStream(is_tty=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)

    setup_console_logging(default_log_level="info", simplified_logging=True, stream=stream)

    logger = logging.getLogger("eth_defi.tests.simplified")
    logger.info("Simple value %s", "abc")

    plain_output = ANSI_RE.sub("", stream.getvalue())
    assert "Simple value abc" in plain_output
    assert "INFO" not in plain_output
    assert "eth_defi.tests.simplified" not in plain_output


def test_rich_logging_does_not_pad_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rich logging must not right-pad short Docker log lines."""

    stream = DummyStream(is_tty=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)

    setup_console_logging(default_log_level="info", stream=stream)

    logger = logging.getLogger("eth_defi.tests.padding")
    logger.info("x")

    plain_line = ANSI_RE.sub("", stream.getvalue()).splitlines()[0]
    assert plain_line == plain_line.rstrip(" \t")
    assert len(plain_line) < MAX_SHORT_RICH_LINE_LENGTH


def test_rich_logging_uses_compact_standard_tracebacks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rich logging must not emit verbose boxed tracebacks."""

    stream = DummyStream(is_tty=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)

    setup_console_logging(default_log_level="info", stream=stream)

    logger = logging.getLogger("eth_defi.tests.traceback")
    try:
        raise RuntimeError("inner receiver")
    except RuntimeError:
        logger.exception("Failure with compact traceback")

    output = stream.getvalue()
    plain_output = ANSI_RE.sub("", output)
    assert "Failure with compact traceback" in plain_output
    assert "Traceback (most recent call last)" in plain_output
    assert "RuntimeError: inner receiver" in plain_output
    assert "\u256d" not in output
    assert "\u2502" not in output
    assert "\u2570" not in output


def test_rich_thread_colours_are_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rich thread colours are stable and distinct for different threads."""

    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)

    root = setup_console_logging(default_log_level="info", coloured_threads=True)
    handler = root.handlers[0]

    first_style = handler.get_thread_style("worker-a")
    assert handler.get_thread_style("worker-a") == first_style
    assert handler.get_thread_style("worker-b") != first_style


def test_no_color_disables_thread_ansi(monkeypatch: pytest.MonkeyPatch) -> None:
    """NO_COLOR suppresses ANSI escapes even when thread colours are requested."""

    stream = DummyStream(is_tty=False)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    setup_console_logging(default_log_level="info", coloured_threads=True, stream=stream)

    logger = logging.getLogger("eth_defi.tests.plain_threads")
    logger.info("Thread colour value %s", "abc")

    output = stream.getvalue()
    assert "\x1b[" not in output
    assert "MainThread" in output
    assert "Thread colour value abc" in output


def test_file_logging_stays_plain_with_rich_console(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """File handlers must not receive ANSI escape sequences."""

    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = DummyStream(is_tty=False)
    log_file = tmp_path / "test.log"

    root = setup_console_logging(default_log_level="info", log_file=log_file, stream=stream)

    logger = logging.getLogger("eth_defi.tests.file_logging")
    logger.info("File value %s", "abc")

    for handler in root.handlers:
        handler.flush()

    output = log_file.read_text(encoding="utf-8")
    assert "\x1b[" not in output
    assert "eth_defi.tests.file_logging" in output
    assert "File value abc" in output
