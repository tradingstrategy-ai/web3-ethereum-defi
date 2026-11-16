"""Exercise retry scheduling through the actual flaky and timeout plugins."""

from types import SimpleNamespace

import pytest

from eth_defi.testing import hypersync_retry

pytest_plugins = ("pytester",)


@pytest.fixture
def retry_run(pytester: pytest.Pytester) -> pytest.Pytester:
    """Load the production test policy in an isolated pytest session.

    :param pytester: Temporary nested pytest session.
    :return: Session with the retry plugin enabled.
    """
    pytester.makeconftest('pytest_plugins = ("eth_defi.testing.hypersync_retry",)')
    return pytester


@pytest.mark.parametrize("error,expected_waits", [("RuntimeError('inner receiver: rate limited by server (remaining=0/100 reqs, resets_in=20s)')", [21, 30]), ("TimeoutError('Hypersync request timed out')", [15, 30])])
def test_hypersync_retry_waits_once_per_failure_and_honours_reset(retry_run: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, error: str, expected_waits: list[int]) -> None:
    """Retry real test attempts with provider-aware waits and fresh fixtures.

    Virtual time exercises the policy without waiting for a live service. Flaky
    consults its filter twice, but each failed attempt must sleep only once.

    :param retry_run: Isolated pytest session.
    :param monkeypatch: Replace only the policy clock, not pytest's own timers.
    :param error: Representative quota or request timeout exception.
    :param expected_waits: Expected waits between the three attempts.
    :return: None after checking successful recovery and fixture teardown.
    """
    elapsed = [0.0]
    waits = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        elapsed[0] += seconds

    monkeypatch.setattr(hypersync_retry, "time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=sleep))
    retry_run.makepyfile(f"""
        import pytest
        attempts = []
        closed = []

        @pytest.fixture
        def connection():
            yield
            closed.append(True)

        @pytest.mark.hypersync_retry
        def test_read(connection):
            attempts.append(True)
            assert len(closed) == len(attempts) - 1
            if len(attempts) < 3:
                raise {error}
    """)
    retry_run.runpytest("-q").assert_outcomes(passed=1)
    assert waits == expected_waits


@pytest.mark.parametrize("error", ["AssertionError('bad calculation')", "KeyError('vault_slug')", "RuntimeError('invalid configuration')"])
def test_hypersync_retry_preserves_programming_failures(retry_run: pytest.Pytester, error: str) -> None:
    """Deterministic failures must be reported after the first attempt.

    A repeated call writes another line, making accidental retries observable.

    :param retry_run: Isolated pytest session.
    :param error: Representative assertion, metadata or configuration failure.
    :return: None after verifying a single failed attempt.
    """
    retry_run.makepyfile(f"""
        import pytest
        from pathlib import Path

        @pytest.mark.hypersync_retry
        def test_read():
            with Path("attempts.txt").open("a") as output:
                output.write("attempt\\n")
            raise {error}
    """)
    retry_run.runpytest("-q").assert_outcomes(failed=1)
    assert (retry_run.path / "attempts.txt").read_text().splitlines() == ["attempt"]


@pytest.mark.parametrize("error,expected_waits,expected_elapsed", [("HTTP 429 Too Many Requests", [15, 30, 60, 60, 60, 60], 285), ("rate limited by server (resets_in=297s)", [], 0)])
def test_hypersync_retry_stops_before_another_wait_exceeds_budget(retry_run: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, error: str, expected_waits: list[int], expected_elapsed: int) -> None:
    """An ongoing quota outage ends within the shared five-minute budget.

    Repeated transient failures exercise the complete scheduling path with a
    virtual clock, retaining the final exception instead of skipping the test.

    :param retry_run: Isolated pytest session.
    :param monkeypatch: Replace only the retry policy's clock.
    :param error: Transient failure, optionally with a reset near the deadline.
    :param expected_waits: Backoff delays that fit the deadline and margin.
    :param expected_elapsed: Total simulated time before the final failure.
    :return: None after verifying bounded waits and final failure.
    """
    elapsed = [0.0]
    waits = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        elapsed[0] += seconds

    monkeypatch.setattr(hypersync_retry, "time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=sleep))
    retry_run.makepyfile(f"""
        import pytest

        @pytest.mark.hypersync_retry
        def test_read():
            raise RuntimeError({error!r})
    """)
    retry_run.runpytest("-q").assert_outcomes(failed=1)
    assert waits == expected_waits
    assert elapsed[0] == expected_elapsed


def test_hypersync_timeout_includes_attempt_execution(retry_run: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow attempt consumes the same deadline and fails only its own test.

    A shortened real timer verifies that the protocol timeout works with flaky,
    rather than merely checking the marker or the helper's virtual clock.

    :param retry_run: Isolated pytest session.
    :param monkeypatch: Shorten the policy budget for this offline regression.
    :return: None after checking timeout failure and continued test execution.
    """
    monkeypatch.setattr(hypersync_retry, "HYPERSYNC_TEST_TIMEOUT", 0.2)
    retry_run.makepyfile("""
        import time
        import pytest

        @pytest.mark.hypersync_retry
        def test_slow_read():
            time.sleep(1)

        def test_worker_still_runs():
            assert True
    """)
    result = retry_run.runpytest("-q", "--timeout-method=thread")
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(["*Timeout*0.2*"])
