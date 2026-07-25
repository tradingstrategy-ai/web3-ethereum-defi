"""Session-scoped Anvil fork pool for reusing forks across test modules.

**This module docstring is the canonical, authoritative description of the
shared Anvil fork + fixed fork-block + warm RPC-cache test pattern.** Other
places in the repository (``CLAUDE.md``, the ``add-vault-protocol`` skill,
:mod:`eth_defi.testing.fork_blocks`, :file:`eth_defi/testing/README.md`) only
point here; do not duplicate the rationale — update this docstring instead.

Reusable testing helper (kept under ``eth_defi`` rather than ``tests`` per the
repository convention) that lets many tests sharing the same
``(chain, fork_block_number, launch config)`` reuse a single Anvil process
instead of each launching (and archive-replaying) its own. This is Lever 1 of
the test-suite performance plan (:file:`docs/README-test-suite-performance.md`).

The pytest fixture wrapper lives in the top-level ``tests/conftest.py``; this
module holds only the reusable pool class.

Required pattern for new Anvil mainnet-fork tests
-------------------------------------------------

Any new Anvil mainnet-fork characterisation test **must** use this pattern
rather than launching its own per-file fork at a chain-tip / ad-hoc block:

1. **Fork at a fixed, shared block, never ``latest``.** Use your chain's
   canonical ``*_MIDNIGHT_BLOCK`` constant from
   :mod:`eth_defi.testing.fork_blocks` (via :func:`get_midnight_block` for a
   programmatic lookup). A mutable chain tip is non-reproducible, cannot be
   shared, and defeats the RPC cache. A fixed block also lets value assertions
   use exact numbers (per the repository test rules) rather than fuzzy bounds.
2. **Obtain Web3 from the shared pool**, not from a private
   ``fork_network_anvil`` call, so every same-chain, same-block test reuses one
   Anvil process. Have the module's ``web3`` fixture call
   :meth:`AnvilForkPool.get_web3` with the session-scoped ``anvil_fork_pool``
   fixture.
3. **Carry the matching ``xdist_group`` marker** so ``--dist loadgroup`` (used
   in CI) co-locates all sharers on one worker — session scope is per worker, so
   without the marker the "shared" fork silently forks once per worker. Use one
   stable group name per (chain, block), e.g.
   ``@pytest.mark.xdist_group("fork:ethereum:midnight")``.

Why this matters — the warm RPC cache
--------------------------------------

Anvil persists every ``eth_call`` / ``eth_getStorageAt`` archive response it
replays to ``~/.foundry/cache/rpc/<network>/<block>/`` — but **only on a graceful
shutdown** (its ``Drop`` flushes the file; a ``SIGKILL`` discards it). So
:meth:`~eth_defi.provider.anvil.AnvilLaunch.close` ``SIGTERM``s Anvil and waits
for the flush before falling back to ``SIGKILL``; without that the cache is never
written and every run cold-fetches. When many
characterisation tests fork **the same fixed block**, they read overlapping
state, so that on-disk cache becomes dense and later runs replay from disk
instead of re-hammering the upstream archive node. CI restores and re-saves this
directory across runs (see the "Foundry fork RPC cache" steps in
``.github/workflows/test-vault-protocol.yml`` and
``docs/README-test-suite-performance.md``), so a warm cache turns cold-archive
stalls (the ~476 s startup seen on cold Ethereum archive forks) into ~seconds.

Forking ``latest`` or a per-test arbitrary block breaks all three benefits: the
cache key never repeats, nothing is shared, and every run pays full
archive-replay latency. That is why the fixed shared block is mandatory.

Cold-fork read-timeout failures — the CI symptom and what it means
------------------------------------------------------------------

Fork setup can fail with a 60-second ``eth_chainId`` read timeout, historically
hinted (misleadingly) as "out of API credits". The classified ``failure_mode``
is ``read_timeout``: Anvil is blocked initialising its fork against the upstream
archive and does not answer the first call in time.

**Measured expected delay** (``scripts/measure-cold-fork-time.py``, anvil 1.7.1,
2026-07-25): a single cold fork of a midnight block completes in **~3 seconds**,
and even **six concurrent** cold forks of the same block stay at ~2.5 s each. So
the 60 s Web3 read timeout (:data:`POOL_WEB3_HTTP_TIMEOUT`) is already ~20× a
healthy cold fork and is **sufficient** — a timeout is not the cap being too
small, and raising it only delays the failure (do not raise it to mask a slow
provider).

A 60 s timeout in CI therefore means the **upstream provider is slow or is
rate-limiting the runner IP**, not that the fork is legitimately slow and not
that credits are exhausted (a healthy fork answers in seconds). The fix is
upstream reliability, not a bigger timeout: (1) a warm fork RPC cache so CI never
cold-fetches (see above and the repo-seed mechanism in
:mod:`eth_defi.testing.rpc_cache`), (2) provider failover across space-separated
``JSON_RPC_*`` endpoints, (3) a provider that does not throttle the CI IP. Run
the measurement script from a machine using the CI RPC secrets to compare its
cold-fork times against this ~3 s local baseline.

**Where the upstream-stall reason shows up.** With **multiple** providers the
fork routes through the automatic failover proxy
(:func:`eth_defi.provider.anvil._create_default_anvil_proxy_config`), which tries
each upstream once and logs every stall/error at ``WARNING`` — pytest captures
``WARNING`` and above into the failed test's "Captured log" section, so that is
where you see *which* provider stalled and *why* (connection timeout, retryable
HTTP error). With a **single** provider there is no proxy and no failover: a
:class:`SingleRpcProviderWarning` is emitted and the only signal is the local
``eth_chainId`` read timeout — configure a second provider to get both failover
and the diagnosis.

Wedged-fork recycling — one bad fork must not fail its whole group
------------------------------------------------------------------

**Background.** A long-lived shared fork can stop answering under sustained load.
This is the Anvil responsiveness degradation already documented in the
``AnvilSnapshotState`` docstring in :mod:`eth_defi.provider.anvil`, and it is the
known trade-off of the shared-fork pool introduced in `PR #1360
<https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1360>`__ (which
replaced per-test forks with long-lived pooled ones).

**Why it was so damaging.** A pooled fork is shared by every test carrying its
``xdist_group`` marker, so one wedged process failed *all* of them at setup — and
each victim paid the full 60 s Web3 read timeout against ``localhost`` before
erroring. Measured on a full local vault-protocol run (100 % warm RPC cache,
single unthrottled providers, so neither cold-fetch nor provider throttling was
involved): **8 wedged Anvil processes produced 44 failures/errors**, 19 of them
from a single ``fork:ethereum:midnight`` fork. Note the timeout host is
``localhost`` — that is the tell that distinguishes this from the upstream
throttling described in the previous section, which was the original subject of
`PR #1372 <https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1372>`__
and the CI triage in `PR #1370
<https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1370#issuecomment-5077873163>`__.

**The fix.** :meth:`AnvilForkPool.get_launch` probes a **reused** fork with
:func:`is_fork_alive` before handing it out — a raw ``eth_chainId`` bounded by
:data:`POOL_LIVENESS_TIMEOUT` and sent outside the Web3 stack so it cannot
inherit the 60 s timeout it exists to avoid. An unresponsive fork is disposed and
relaunched, turning "the rest of this group is doomed" into "one fork restarts".

**Deliberate scope limits** (read before extending this):

- **Newly launched forks are not probed.**
  :func:`~eth_defi.provider.anvil.fork_network_anvil` already smoke-tests a fresh
  process, so probing again would only add a round-trip to every cold start.
- **This makes a wedged fork recoverable, not impossible.** It does not address
  *why* Anvil degrades under sustained pooled load. A burst of
  :class:`WedgedForkRecycledWarning` is therefore a signal to investigate (cap
  tests-per-fork, recycle proactively), which is exactly why the recycling warns
  loudly instead of silently papering over the problem.
- **Recycling resets EVM state.** A relaunched fork is a *fresh* fork: any
  post-launch deployment or mutation a test group relied on is gone. That is safe
  for the read-only characterisation tests this pool currently serves (see the
  warning at the end of this docstring), but a mutating shared-fork user must
  re-establish its baseline rather than assume continuity.

Bounded provider retries — fail fast, never re-hammer a dead provider
---------------------------------------------------------------------

Forks must fail within a bounded period when an upstream archive provider is
exhausted or unavailable, so a genuine outage surfaces quickly instead of
stalling until the job timeout. The pool pins this explicitly rather than
relying on an upstream default that could regress:

- **Upstream (Anvil → archive) — one attempt per provider, no re-hammer.**
  :meth:`AnvilForkPool.get_launch` forwards to
  :func:`eth_defi.provider.anvil.fork_network_anvil` and relies on
  ``launch_anvil``'s default ``proxy_multiple_upstream=True`` (the *bounded
  automatic failover proxy*). When a ``JSON_RPC_*``
  value carries multiple space-separated providers, ``launch_anvil`` builds
  :func:`eth_defi.provider.anvil._create_default_anvil_proxy_config`, which sets
  ``retries = provider_count`` and ``backoff = 0.0`` — the proxy tries each
  upstream exactly once, fails over instead of retrying a dead endpoint, and
  keeps its whole pass under ``ANVIL_PROXY_TOTAL_TIMEOUT`` (55 s), i.e. below the
  60 s Web3 localhost read timeout, so an all-providers-down failure returns a
  classified proxy error rather than a client ``ReadTimeout``. A single-provider
  value starts no proxy (nothing to fail over to — that is why the CI secrets
  should carry two providers per chain).
- **Local (Web3 → Anvil) — zero retries.** ``POOL_WEB3_RETRIES = 0``: the client
  makes one attempt against local Anvil because the proxy has already performed
  upstream failover; retrying here would only multiply the wait.

Callers with a legitimately slow fork operation can override the Web3 retry
count and HTTP timeout in :meth:`AnvilForkPool.get_web3`, or pass an explicit
``proxy_multiple_upstream=RPCProxyConfig(...)`` / ``RPCProxy`` /
``proxy_multiple_upstream=False`` through the launch kwargs. Do **not** shorten
the per-attempt proxy timeout below a legitimate cold-archive read — the
fail-fast win is fewer attempts, not shorter ones; shorter ones re-introduce
flakiness on cold reads.

Reference tests to copy
-----------------------

- Read-only pooled fork: ``tests/erc_4626/vault_protocol/test_goat.py`` and
  ``tests/erc_4626/vault_protocol/test_aarna.py``.
- Shared fork plus once-per-session expensive deployment (Safe/Lagoon):
  ``tests/lagoon/conftest.py``.

Copy-paste module skeleton
--------------------------

.. code-block:: python

    import os

    import pytest
    from web3 import Web3

    from eth_defi.testing.anvil_fork_pool import AnvilForkPool
    from eth_defi.testing.fork_blocks import ETHEREUM_MIDNIGHT_BLOCK

    JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

    pytestmark = [
        pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
        # Co-locate every same-block Ethereum sharer on one xdist worker.
        pytest.mark.xdist_group("fork:ethereum:midnight"),
    ]


    @pytest.fixture(scope="module")
    def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
        # Read-only test: shares one Anvil fork from the session pool, so no
        # snapshot/revert reset is needed between tests.
        return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, ETHEREUM_MIDNIGHT_BLOCK)

Design notes:

- **Opt-in, never autouse.** A test module's own ``web3`` fixture calls the pool
  explicitly. An ``autouse=True`` restore fixture in a shared ``conftest.py``
  causes ``ScopeMismatch`` when sibling modules override ``web3`` at function
  scope.
- **Pin sharers to one xdist worker.** ``--dist loadgroup`` (used in CI) sends
  all tests marked with the same ``xdist_group`` to one worker, and pytest
  session scope is per worker — so tests sharing a fork must carry an identical
  ``@pytest.mark.xdist_group("fork:<chain>:<block>")`` marker.
- **The registry key is the full launch config**, not just ``(chain, block)``:
  :func:`eth_defi.provider.anvil.fork_network_anvil` is a thin alias of the fully
  configurable ``launch_anvil``, so differing hardfork / gas / unlocked-account /
  tracing options must not collide on one cached process.

.. warning::

    **Proof-of-concept, gated on CI.** The repository documents that repeated
    snapshot/revert cycles on a long-lived, module/session-scoped fork can
    degrade Anvil responsiveness and hang CI under ``pytest-xdist`` (see the
    ``AnvilSnapshotState`` docstring in :mod:`eth_defi.provider.anvil`). The
    initial proof-of-concept only shares forks between **read-only** tests, which
    do not mutate fork state and therefore need no snapshot/revert between tests.
    Mutating tests that share a fork must additionally reset it with
    :func:`eth_defi.testing.evm_snapshot_fixture.evm_snapshot_revert` or
    :func:`eth_defi.provider.anvil.reset_anvil_snapshot`; that path is not yet
    wired here, pending a bounded CI run that proves no xdist hang.
"""

import dataclasses
import logging
import warnings
from typing import Any

import requests
from web3 import Web3

from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.provider.rpc_proxy import RPCProxy, RPCProxyConfig
from eth_defi.utils import get_url_domain

logger = logging.getLogger(__name__)


def _redacted_upstream(rpc_url: str) -> str:
    """Return the key-redacted upstream provider domain(s) for error messages.

    Splits a space-separated multi-provider fork URL and redacts each entry to
    its domain via :func:`eth_defi.utils.get_url_domain`, so a fork-setup error
    or warning can name the vendor — e.g. to identify which provider is failing
    and needs topping up — without leaking the API key embedded in the URL
    path/query. JSON-RPC API keys are not security critical (see
    ``get_url_domain``), but are still not printed in full.

    :param rpc_url:
        One or more space-separated upstream JSON-RPC URLs.

    :return:
        Comma-separated redacted domains, e.g. ``arb-mainnet.example.com``.
    """
    return ", ".join(get_url_domain(u) for u in rpc_url.split() if u)


class SingleRpcProviderWarning(UserWarning):
    """Warn that a fork RPC session has only one upstream provider.

    With a single upstream there is no failover: an exhausted or rate-limited
    archive provider fails the fork instead of switching to a second endpoint.
    Emitted (and shown in the pytest warnings summary + CI logs) so a
    single-provider ``JSON_RPC_*`` configuration is visible as the root cause of
    flaky fork tests. Configure two space-separated providers per chain to
    silence it — see :file:`docs/README-test-suite-performance.md`.
    """


#: Do not retry a wedged local Anvil after its proxy has tried every upstream.
POOL_WEB3_RETRIES: int = 0

#: Preserve the established connect/read timeout for legitimate cold-cache calls.
POOL_WEB3_HTTP_TIMEOUT: tuple[float, float] = (3.0, 60.0)

#: Seconds allowed for the liveness probe on a **reused** pooled fork.
#:
#: A healthy Anvil answers ``eth_chainId`` from memory in milliseconds, so this
#: budget only has to cover process scheduling and a loaded CI runner — not any
#: real work. It must stay **far below** :data:`POOL_WEB3_HTTP_TIMEOUT` (60 s),
#: because the entire point of the probe is to detect a wedged fork in seconds
#: instead of paying that 60 s timeout once per test in the group (see the
#: "Wedged-fork recycling" section of the module docstring — 8 wedged forks cost
#: 44 test failures before this existed). Raising it towards 60 s would defeat
#: the mechanism; lowering it risks recycling a merely busy fork, which is
#: cheap but wasteful.
POOL_LIVENESS_TIMEOUT: float = 5.0


class WedgedForkRecycledWarning(UserWarning):
    """Warn that an unresponsive pooled Anvil fork was disposed and relaunched.

    A long-lived shared fork can stop answering under sustained load (see the
    ``AnvilSnapshotState`` docstring in :mod:`eth_defi.provider.anvil`, and the
    "Wedged-fork recycling" section of this module's docstring). Without
    recycling, every remaining test sharing that fork fails at setup with a 60 s
    ``read_timeout`` against ``localhost``.

    This is a :class:`UserWarning` **on purpose**: recycling repairs the symptom
    so the suite can continue, but it does not fix the underlying degradation.
    Surfacing it in the pytest warnings summary keeps a fork that wedges
    repeatedly visible — treat a burst of these as a signal to cap
    tests-per-fork or recycle proactively, not as normal background noise.

    Introduced in `PR #1372
    <https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1372>`__.
    """


def is_fork_alive(launch: AnvilLaunch, timeout: float = POOL_LIVENESS_TIMEOUT) -> bool:
    """Check that a pooled Anvil fork still answers JSON-RPC promptly.

    Used by :meth:`AnvilForkPool.get_launch` to decide whether a **reused** fork
    is still healthy or must be recycled. See the "Wedged-fork recycling" section
    of this module's docstring for the full rationale and the measurements that
    motivated it.

    Three deliberate design choices, each of which matters:

    1. **``eth_chainId`` as the probe call.** Anvil answers it from memory
       without touching the upstream archive, so a slow reply isolates *Anvil
       process* unresponsiveness from *upstream provider* slowness. Probing with
       a state-reading call (``eth_getBalance``, ``eth_call``) would conflate the
       two and could wrongly recycle a healthy fork that is merely waiting on a
       cold archive read.
    2. **Raw ``requests`` instead of Web3.** Going through
       :func:`~eth_defi.provider.multi_provider.create_multi_provider_web3` would
       inherit :data:`POOL_WEB3_HTTP_TIMEOUT` (60 s read) — the very timeout this
       probe exists to avoid paying once per test in the group. A raw POST lets
       us bound the check at :data:`POOL_LIVENESS_TIMEOUT`.
    3. **Fail closed.** Any failure — timeout, refused connection, HTTP error, or
       an unparseable body — is treated as "not usable". Relaunching a fork that
       was actually fine costs one extra fork (seconds, and the RPC cache is
       warm); *not* relaunching a wedged one costs every remaining test in the
       group a 60 s timeout.

    :param launch:
        Pooled Anvil launch to probe. Only its ``json_rpc_url`` is used, so this
        is cheap to call and safe on a process that may already be dead.

    :param timeout:
        Seconds to wait for the reply before declaring the fork wedged. Defaults
        to :data:`POOL_LIVENESS_TIMEOUT`.

    :return:
        ``True`` when Anvil replied with a well-formed JSON-RPC result within the
        timeout, ``False`` otherwise (caller should dispose and relaunch).
    """
    # Hand-rolled JSON-RPC envelope: see design note 2 above — we must not build
    # a Web3 client here or we inherit its 60-second read timeout.
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}
    try:
        resp = requests.post(launch.json_rpc_url, json=payload, timeout=timeout)
        resp.raise_for_status()
        # A wedged Anvil can accept the connection and return a body that is not
        # a valid JSON-RPC reply, so require the "result" key rather than
        # trusting the HTTP status alone.
        return "result" in resp.json()
    except (requests.exceptions.RequestException, ValueError):
        # RequestException covers timeout / connection refused / HTTP error;
        # ValueError covers a non-JSON body from resp.json(). Deliberately narrow
        # (no bare `except Exception`) per the repository exception rules, while
        # still failing closed for every way a dead process can misbehave.
        return False


def _freeze(value: Any) -> Any:
    """Recursively turn a value into a hashable, order-stable form.

    Used to build the pool's cache key from arbitrary ``launch_anvil`` keyword
    arguments — some of which are lists (e.g. ``unlocked_addresses``) or dicts —
    which are otherwise unhashable and cannot be used in a ``dict`` key.

    :param value:
        Any launch-argument value.

    :return:
        A hashable representation (lists/tuples become tuples, dicts become
        sorted key/value tuples, sets become sorted tuples, scalars unchanged).
    """
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, RPCProxyConfig):
        return (
            RPCProxyConfig,
            tuple((field.name, _freeze(getattr(value, field.name))) for field in dataclasses.fields(value)),
        )
    if isinstance(value, RPCProxy):
        return (RPCProxy, id(value))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(_freeze(v) for v in value))
    return value


@dataclasses.dataclass(slots=True)
class AnvilForkPool:
    """Registry of shared Anvil forks keyed by launch configuration.

    One :class:`~eth_defi.provider.anvil.AnvilLaunch` is created per distinct
    launch configuration and reused for every caller (on the same xdist worker)
    that requests it. Call :meth:`close_all` once to tear every launch down.

    Intended for **read-only** fork tests in its current form; see the module
    warning for the mutating-test caveat.
    """

    #: Cached launches keyed by (rpc_url, fork_block_number, sorted launch kwargs).
    launches: dict[tuple, AnvilLaunch] = dataclasses.field(default_factory=dict)

    def get_launch(
        self,
        rpc_url: str,
        fork_block_number: int,
        **launch_kwargs: Any,
    ) -> AnvilLaunch:
        """Return a shared Anvil launch for this exact launch configuration.

        Launches Anvil lazily on the first request for a configuration and
        returns the cached process on every subsequent request.

        :param rpc_url:
            Upstream archive JSON-RPC URL to fork from.

        :param fork_block_number:
            Fixed block to fork at. Required — a mutable chain tip cannot be
            shared safely.

        :param launch_kwargs:
            Any other state-affecting ``fork_network_anvil`` arguments; they are
            part of the cache key so incompatible configs never share a process.
            When ``proxy_multiple_upstream`` is omitted, ``launch_anvil``'s
            default applies (``True`` — the bounded, fail-fast automatic failover
            proxy; see the module docstring); pass an explicit
            :class:`~eth_defi.provider.rpc_proxy.RPCProxyConfig`,
            :class:`~eth_defi.provider.rpc_proxy.RPCProxy`, or ``False`` to
            override.

        :return:
            The shared :class:`~eth_defi.provider.anvil.AnvilLaunch`.
        """
        key = (rpc_url, fork_block_number, _freeze(launch_kwargs))
        launch = self.launches.get(key)

        if launch is not None:
            # REUSE PATH. Only a fork we are handing out a second (or hundredth)
            # time can have wedged — a brand new one was already smoke-tested by
            # fork_network_anvil, so the probe is deliberately skipped below.
            #
            # Without this check a wedged Anvil fails not just this test but
            # every remaining test carrying the same xdist_group marker, each
            # burning the full 60s Web3 read timeout against localhost. Measured:
            # 8 wedged forks -> 44 failures. See the "Wedged-fork recycling"
            # section of the module docstring and PR #1372.
            if is_fork_alive(launch):
                return launch

            # Wedged. Warn loudly (see WedgedForkRecycledWarning: recycling
            # treats the symptom, not the cause) and fall through to relaunch.
            message = f"Pooled Anvil fork for block {fork_block_number} at {launch.json_rpc_url} stopped responding within {POOL_LIVENESS_TIMEOUT}s — disposing and relaunching it."
            warnings.warn(message, WedgedForkRecycledWarning, stacklevel=2)
            logger.warning("%s", message)
            self._dispose(key, launch)
            # NOTE: the replacement is a *fresh* fork with clean EVM state. Safe
            # for the read-only tests this pool serves; a mutating sharer would
            # have to re-establish its baseline.

        launch = self._launch(rpc_url, fork_block_number, **launch_kwargs)
        self.launches[key] = launch
        return launch

    def _launch(self, rpc_url: str, fork_block_number: int, **launch_kwargs: Any) -> AnvilLaunch:
        """Start a new Anvil fork, warning when it has no upstream failover.

        :param rpc_url:
            Upstream archive JSON-RPC URL(s) to fork from.

        :param fork_block_number:
            Fixed block to fork at.

        :param launch_kwargs:
            Additional ``fork_network_anvil`` arguments.

        :return:
            The new :class:`~eth_defi.provider.anvil.AnvilLaunch`.
        """
        # Warn once per unique fork if there is no upstream failover: a single
        # provider cannot fail over when it runs out of credits or is rate
        # limited, which is the dominant cause of flaky fork tests.
        provider_count = len([u for u in rpc_url.split() if u])
        if provider_count < 2:
            message = f"Fork RPC session for block {fork_block_number} on upstream provider(s) {_redacted_upstream(rpc_url)} uses only {provider_count} — no failover if it is exhausted or rate limited. Configure two space-separated providers per chain."
            warnings.warn(message, SingleRpcProviderWarning, stacklevel=2)
            logger.warning("%s", message)
        return fork_network_anvil(
            rpc_url,
            fork_block_number=fork_block_number,
            **launch_kwargs,
        )

    def _dispose(self, key: tuple, launch: AnvilLaunch) -> None:
        """Drop a fork from the registry and tear its process down.

        The registry entry is removed first so a failure to close a wedged
        process cannot leave the dead launch cached and handed to the next
        caller.

        :param key:
            Registry key of the launch being disposed.

        :param launch:
            The launch to tear down.
        """
        # Evict FIRST, close second. If close() raises on an already-dead or
        # unkillable process, the entry must still be gone — otherwise the next
        # caller is handed the same dead launch and the group fails anyway,
        # which is the exact failure this method exists to prevent.
        self.launches.pop(key, None)
        try:
            launch.close(log_level=logging.ERROR)
        except Exception as e:  # noqa: BLE001 - a wedged process can fail to close in many ways
            # Swallow-and-log is correct here: we are already on the recovery
            # path, the entry is evicted, and the OS reaps the process on exit.
            # Re-raising would turn a recoverable wedge back into a group
            # failure. Not a silent swallow — it is logged at WARNING.
            logger.warning("Could not cleanly close wedged Anvil fork %s: %s", launch.json_rpc_url, e)

    def get_web3(
        self,
        rpc_url: str,
        fork_block_number: int,
        *,
        web3_retries: int = POOL_WEB3_RETRIES,
        web3_http_timeout: tuple[float, float] = POOL_WEB3_HTTP_TIMEOUT,
        **launch_kwargs: Any,
    ) -> Web3:
        """Return a fresh Web3 pointed at a shared Anvil fork.

        The underlying Anvil process is shared via :meth:`get_launch`; the
        returned :class:`web3.Web3` object itself is created per call and is not
        shared.

        :param rpc_url:
            Upstream archive JSON-RPC URL to fork from.

        :param fork_block_number:
            Fixed block to fork at.

        :param web3_retries:
            Number of outer retries against the local Anvil endpoint. Keep this
            low because the inner RPC proxy already performs provider failover.

        :param web3_http_timeout:
            Connect and read timeout for local Anvil requests. Override this for
            a known slow cold-cache operation.

        :param launch_kwargs:
            Additional ``fork_network_anvil`` arguments (part of the cache key).

        :return:
            A :class:`web3.Web3` connected to the shared Anvil RPC endpoint.
        """
        launch = self.get_launch(rpc_url, fork_block_number, **launch_kwargs)
        # Pass the redacted upstream vendor domain(s) as the error hint. The
        # returned Web3 points at local Anvil, so a fork-setup failure (e.g. the
        # 60s eth_chainId read timeout) otherwise names only "localhost:<port>";
        # the hint surfaces which upstream provider to investigate / top up in
        # the raised RuntimeError ("... Hint is forking upstream provider(s): X").
        return create_multi_provider_web3(
            launch.json_rpc_url,
            default_http_timeout=web3_http_timeout,
            retries=web3_retries,
            hint=f"forking upstream provider(s): {_redacted_upstream(rpc_url)}",
        )

    def close_all(self) -> None:
        """Tear down every launched Anvil process.

        Every launch is attempted even if an earlier one fails to close, so a
        single teardown error cannot leak the remaining processes.
        """
        launches = list(self.launches.values())
        self.launches.clear()
        errors: list[BaseException] = []
        for launch in launches:
            try:
                launch.close()
            except (OSError, AssertionError) as e:
                # Anvil teardown is best-effort. shutdown_hard() raises
                # AssertionError if the process will not terminate in time and
                # OSError on process/socket issues; record and continue so one
                # wedged process does not leak the rest. Re-raised below.
                errors.append(e)
        if errors:
            raise errors[0]
