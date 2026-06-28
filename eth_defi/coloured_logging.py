"""Coloured console logging helpers."""

import logging
import os
import sys
from pathlib import Path
from typing import Any, ClassVar, TextIO

from rich.console import Console
from rich.highlighter import ReprHighlighter
from rich.logging import RichHandler
from rich.text import Text
from rich.theme import Theme

# Rich only honours fixed redirected-console width when both dimensions are set.
RICH_LOG_WIDTH = 1000
RICH_LOG_HEIGHT = 25
DISABLED_FORCE_COLOUR_VALUES: set[str | None] = {None, "", "0"}
RICH_LOG_THEME = Theme(
    {
        "log.module": "blue",
        "log.thread": "magenta",
        "log.thread_bracket": "dim",
    }
)


class EthDefiRichHandler(RichHandler):
    """Rich log handler that preserves eth_defi's module/thread fields."""

    _THREAD_STYLES: ClassVar[list[str]] = [
        "bold cyan",
        "bold yellow",
        "bold magenta",
        "bold green",
        "bold blue",
        "bold bright_red",
        "bold bright_cyan",
        "bold bright_yellow",
        "bold bright_magenta",
        "bold bright_blue",
    ]

    def __init__(self, *args: Any, show_context: bool = True, colour_threads: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.show_context = show_context
        self.colour_threads = colour_threads
        self.thread_styles: dict[str, str] = {}
        self.next_thread_style = 0

    def get_thread_style(self, thread_name: str) -> str:
        """Get a stable Rich style for a thread name."""

        if not self.colour_threads:
            return "log.thread"

        if thread_name not in self.thread_styles:
            self.thread_styles[thread_name] = self._THREAD_STYLES[self.next_thread_style % len(self._THREAD_STYLES)]
            self.next_thread_style += 1

        return self.thread_styles[thread_name]

    def render_message(self, record: logging.LogRecord, message: str) -> Text:
        """Render log message with module and thread context."""

        message_text = super().render_message(record, message)

        if not self.show_context:
            return message_text

        text = Text()
        text.append(record.name.ljust(44), style="log.module")
        text.append(" [", style="log.thread_bracket")
        text.append(record.threadName, style=self.get_thread_style(record.threadName))
        text.append("] ", style="log.thread_bracket")
        text.append_text(message_text)
        return text


class TrailingSpaceStrippingStream:
    """Stream wrapper that removes Rich's right-padding from redirected logs."""

    def __init__(self, wrapped: TextIO) -> None:
        self.wrapped = wrapped
        self.buffer = ""

    def write(self, text: str) -> int:
        """Write text while trimming trailing spaces before newlines."""

        self.buffer += text

        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self.wrapped.write(line.rstrip(" \t") + "\n")

        return len(text)

    def flush(self) -> None:
        """Flush the wrapped stream."""

        if self.buffer:
            self.wrapped.write(self.buffer.rstrip(" \t"))
            self.buffer = ""
        self.wrapped.flush()

    def isatty(self) -> bool:
        """Return the wrapped stream's TTY status."""

        return self.wrapped.isatty()

    def __getattr__(self, name: str) -> Any:
        """Delegate stream attributes to the wrapped stream."""

        return getattr(self.wrapped, name)


def is_running_inside_docker() -> bool:
    """Detect whether the current process runs inside a container.

    Docker and Compose logs do not usually present stdout/stderr as a TTY,
    but they preserve ANSI escape sequences. We use common container marker
    files and cgroup identifiers to enable colours for this case.

    :return:
        ``True`` if the process appears to run inside Docker or a compatible
        container runtime.
    """

    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True

    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8")
    except OSError:
        return False

    return any(marker in cgroup for marker in ("docker", "containerd", "kubepods"))


def should_do_colour_logging(stream: TextIO, *, autodetect_docker_log: bool = True) -> bool:
    """Determine whether console logging should use ANSI colours.

    :param stream:
        Console stream that receives log output.
    :param autodetect_docker_log:
        Enable colours when the process is inside Docker even if the stream is
        not a TTY. Enabled by default because Docker Compose preserves ANSI
        escape sequences in ``logs -f``.
    :return:
        ``True`` if ANSI colour output should be enabled.
    """

    if os.environ.get("NO_COLOR"):
        return False

    if any(os.environ.get(name) not in DISABLED_FORCE_COLOUR_VALUES for name in ("FORCE_COLOR", "CLICOLOR_FORCE")):
        return True

    if stream.isatty():
        return True

    return autodetect_docker_log and is_running_inside_docker()


def create_rich_log_handler(
    level: int,
    *,
    stream: TextIO,
    simplified_logging: bool = False,
    coloured_threads: bool = False,
) -> EthDefiRichHandler:
    """Create a Rich log handler for colour console output.

    Rich handles the timestamp and log level columns. We prepend module and
    thread information to the message so the output keeps the useful shape of
    the previous formatter while gaining Rich's colours and highlighting.

    :param level:
        Handler log level.
    :param stream:
        Console stream that receives log output.
    :param simplified_logging:
        Do not add module and thread fields when ``True``.
    :param coloured_threads:
        Use a stable per-thread colour palette when ``True``.
    :return:
        Configured Rich handler.
    """

    console = Console(
        file=TrailingSpaceStrippingStream(stream),
        force_terminal=True,
        color_system="standard",
        width=RICH_LOG_WIDTH,
        height=RICH_LOG_HEIGHT,
        theme=RICH_LOG_THEME,
    )
    return EthDefiRichHandler(
        level=level,
        console=console,
        show_time=not simplified_logging,
        show_level=not simplified_logging,
        show_path=False,
        omit_repeated_times=False,
        markup=False,
        rich_tracebacks=False,
        highlighter=ReprHighlighter(),
        show_context=not simplified_logging,
        colour_threads=coloured_threads,
        log_time_format="[%Y-%m-%d %H:%M:%S]",
    )


def create_plain_log_handler(
    level: int,
    stream: TextIO,
    fmt: str,
    date_fmt: str,
) -> logging.Handler:
    """Create a plain standard library stream log handler.

    :param level:
        Handler log level.
    :param stream:
        Console stream that receives log output.
    :param fmt:
        Logging format string.
    :param date_fmt:
        Date format string.
    :return:
        Configured standard stream handler.
    """

    stream_handler = logging.StreamHandler(stream)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(logging.Formatter(fmt, date_fmt))
    return stream_handler


def resolve_log_level(level: str | int) -> int:
    """Resolve a logging level name or integer to a numeric level."""

    if isinstance(level, int):
        return level

    numeric_level = getattr(logging, level.upper(), None)
    assert isinstance(numeric_level, int), f"No level: {level}"
    return numeric_level


def setup_console_logging(
    default_log_level: str | int = "warning",
    *,
    simplified_logging: bool = False,
    log_file: str | Path | None = None,
    std_out_log_level: str | int | None = None,
    only_log_file: bool = False,
    clear_log_file: bool = True,
    coloured_threads: bool = False,
    autodetect_docker_log: bool = True,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Set up coloured log output.

    - Helper function to have nicer logging output in tutorial scripts.
    - Tune down some noisy dependency library logging.

    :param default_log_level:
        Default logging level if ``LOG_LEVEL`` is not set.
    :param simplified_logging:
        Do not add module and thread fields when ``True``.
    :param log_file:
        Output both console and this log file.
    :param std_out_log_level:
        Override the console logging level.
    :param only_log_file:
        Do not install a console handler when ``True``.
    :param clear_log_file:
        Truncate the log file before writing when ``True``.
    :param coloured_threads:
        When ``True``, each thread name in the log output gets a unique ANSI
        colour so interleaved parallel logs are easy to follow visually.
    :param autodetect_docker_log:
        Enable coloured Rich output inside Docker even when stderr is not a
        TTY. Enabled by default because Docker Compose preserves ANSI escape
        sequences in ``logs -f``.
    :param stream:
        Console stream that receives log output. Defaults to ``sys.stderr``.
    :return:
        Root logger.
    """

    if stream is None:
        stream = sys.stderr

    configured_level = os.environ.get("LOG_LEVEL", default_log_level)
    numeric_level = resolve_log_level(configured_level)

    if std_out_log_level is None:
        std_out_log_level = numeric_level
    else:
        std_out_log_level = resolve_log_level(std_out_log_level)

    if simplified_logging:
        # Simplified logging format for tutorials
        fmt = "%(message)s"
        date_fmt = "%H:%M:%S"
    else:
        fmt = "%(asctime)s %(name)-44s %(levelname)-8s [%(threadName)s] %(message)s"
        date_fmt = "%Y-%m-%d %H:%M:%S"

    colour_console = should_do_colour_logging(stream, autodetect_docker_log=autodetect_docker_log)

    if colour_console:
        stream_handler = create_rich_log_handler(
            level=std_out_log_level,
            stream=stream,
            simplified_logging=simplified_logging,
            coloured_threads=coloured_threads,
        )
    else:
        stream_handler = create_plain_log_handler(
            level=std_out_log_level,
            stream=stream,
            fmt=fmt,
            date_fmt=date_fmt,
        )

    root = logging.getLogger()
    root.handlers.clear()

    if log_file:
        log_file = Path(log_file)

        log_file.parent.mkdir(parents=True, exist_ok=True)

        # When using a file, the file is always logged with INFO level and
        # env var controls only terminal output
        min_level = min(logging.INFO, numeric_level)
        if clear_log_file:
            mode = "w"
        else:
            mode = "a"

        # File handler always uses plain formatter (no ANSI codes)
        file_handler = logging.FileHandler(log_file, mode=mode, encoding="utf-8")
        file_handler.setLevel(min_level)
        file_handler.setFormatter(logging.Formatter(fmt, date_fmt))

        root.setLevel(min(min_level, std_out_log_level))
        root.addHandler(file_handler)

        if not only_log_file:
            root.addHandler(stream_handler)

    else:
        root.setLevel(std_out_log_level)
        root.addHandler(stream_handler)

    # Mute noise
    logging.getLogger("web3.providers.HTTPProvider").setLevel(logging.WARNING)
    logging.getLogger("web3.RequestManager").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    logging.getLogger("eth_defi.token").setLevel(logging.WARNING)
    return logging.getLogger()
