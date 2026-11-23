"""Wall-clock stage timings for the sequential vault scan and publication pipeline."""

from time import perf_counter


class ScanTimings:
    """Accumulate consecutive intervals for a single scan summary.

    Each stage starts when the previous one finishes. Repeated stages add up,
    so a streaming reader can separate collection from writing without logging
    each batch.
    """

    def __init__(self) -> None:
        self._last = perf_counter()
        self._seconds: dict[str, float] = {}

    def finish(self, stage: str) -> None:
        """Attribute wall time since the previous boundary to this stage.

        :param stage: Stable stage name for comparing cycles in the scanner log.
        """
        now = perf_counter()
        self._seconds[stage] = self._seconds.get(stage, 0.0) + (now - self._last)
        self._last = now

    def __str__(self) -> str:
        """Format the measured total and its components in seconds."""
        stages = " ".join(f"{stage}={seconds:.2f}s" for stage, seconds in self._seconds.items())
        return f"total={sum(self._seconds.values()):.2f}s {stages}"
