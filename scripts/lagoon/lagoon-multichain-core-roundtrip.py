"""Manual three-chain Lagoon investment and delayed unwind tutorial.

1. Deploy an Arbitrum Lagoon vault and Ethereum/HyperEVM satellite Safes.
2. Subscribe 25 native USDC on Arbitrum and bridge accounted capital.
3. Invest about 10 USDC in an open Hypercore user vault and Lighter public pool.
4. Save state and exit; manually run ``ACTION=unwind`` after the actual unlock.
5. Close positions, return capital to Arbitrum and redeem Lagoon shares.

Custody::

    Deployer -> Arbitrum Lagoon -> CCTP -> HyperEVM Safe -> Hypercore user vault
                                       -> Ethereum Safe -> Lighter public pool
    Deployer <- Lagoon redemption <- return CCTP <- satellite protocol withdrawals

Required private environment inputs are ``JSON_RPC_ARBITRUM``,
``JSON_RPC_ETHEREUM`` and ``JSON_RPC_HYPERLIQUID``, supporting fallback URLs.
Live transactions also require ``LAGOON_MULTCHAIN_TEST_PRIVATE_KEY`` (the existing
multichain tutorial's spelling), 25 native USDC on Arbitrum, at least 1 native
USDC on EACH satellite, and native gas on all three chains, including for
tomorrow. All balances must be prefunded in the deployer wallet before starting.
Satellite USDC reserves stay in the deployer wallet, outside vault accounting.
The deployer must also be an activated Hypercore user before deployment: native
HyperEVM gas alone does not authorise the large-block API. Check with
``eth_defi.hyperliquid.evm_escrow.is_account_activated()``. The existing
``activate_account_sponsored()`` helper can activate the deployer's own address
using an additional 2 HyperEVM USDC (including the account creation fee).
Keep the 1-USDC reserve after activation. Alternatively, transfer HYPE to Core
using the documented native system transfer; see
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/hypercore-less-than-greater-than-hyperevm-transfers.
Never put the key in shell history.
Use ``scripts/lifi/feed-cross-chain.py`` to distribute gas and, optionally,
``ETHEREUM_USDC_AMOUNT=5`` to transfer source-chain USDC to Ethereum. See
:ref:`lifi-feed-crosschain` for dry-run commands and funding details.
For example, after loading the deployment key and RPCs privately::

    PRIVATE_KEY="${LAGOON_MULTCHAIN_TEST_PRIVATE_KEY:?Load the deployment key first}" \\
    SOURCE_CHAIN=arbitrum TARGET_CHAINS=ethereum,hyperliquid SOURCE_TOKEN=usdc \\
    MIN_GAS_USD=5 TOP_UP_GAS_USD=10 ETHEREUM_USDC_AMOUNT=5 DRY_RUN=true \\
    poetry run python scripts/lifi/feed-cross-chain.py

Repeat with ``DRY_RUN=false`` and confirm the prompt to execute those quotes.
Allow 25 USDC source spending for these transfers in addition to the 25-USDC
Arbitrum subscription, plus quote headroom and source ETH. This command funds
satellite gas and Ethereum USDC only: supply the HyperEVM USDC reserve separately,
and complete deployer Core activation first. Check actual balances before deploy.
Install the optional SDK following ``lagoon-lighter-trade-example.py``.

Optional settings: ``USDC_AMOUNT=25``, ``POSITION_USDC=10``,
``CCTP_MAX_FEE_USDC=0.01``, ``MAX_RESIDUAL_USDC=0.01``, ``LOG_LEVEL=info``;
``HYPERCORE_VAULT``/``LIGHTER_POOL_INDEX`` override automatic open-target selection.
An unwind may explicitly increase ``MAX_RESIDUAL_USDC`` up to 1 USDC after
inspecting measured cash dust; it never writes off remaining vault shares.
A zero budget stops final redemption when the Core return fee margin remains.
The default hub subscription floor is 23.02 USDC, plus the two 1-USDC
satellite reserves. Account activation deposits
are not entirely fees; recover their leftover collateral on exit.

``STATE_PATH`` defaults to
``~/.tradingstrategy/lagoon-multichain-core-roundtrip/mainnet/state.json``.
It is protected (0600), with a separate API-key backup alongside it. A fresh run
refuses existing state. Later actions load this exact deployment and reconcile
original hashes/nonces. Unknown Core execution requires manual investigation;
never rerun deployment to recover a timed-out mutation. This is a sole-owner
manual test, not a production Safe ownership configuration.

After loading the private environment::

    poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py
    ACTION=status poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py
    ACTION=unwind poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py

Hypercore user vaults lock deposits for one day; actual lock/liquidity governs
exit. HLP is excluded because its lock is longer. Lighter public pools have no
such lock, but this script leaves both investments until manual unwind. Lighter
secure withdrawal/proof settlement may need another invocation. Exit code 2
means known pending work, 1 failure/uncertainty, 0 successful command. Inspect
the named outcome: day-one ``invested`` is not final redemption.
Moving Hypercore equity or limited liquidity may leave a confirmed partial exit.
Run unwind again to request its remainder under a new transaction identity.
An unobserved or rejected Core action still requires explicit investigation;
neither a zero equity guarantee nor automatic replay is available.
After verifying a rejected withdrawal's Core execution, retain that record in
the matching ``steps["core_exit"]["rejection"]`` (or ``core_exit_<n>``) entry.
Preserve the original hash/amount; see ``docs/README-Hypercore-guard.md`` for the
protected-state procedure. A timeout alone never permits this recovery.

``SIMULATE=true`` defaults to ``ACTION=all`` and runs the full flow immediately::

    SIMULATE=true poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py

It forks all three mainnets, generates a wallet and supplies local test funds.
Lagoon/Safe/guard/CCTP and Ethereum Lighter custody calls are real fork contract
calls. Core sequencing, API signatures, pool prices and proof settlement are
explicit mocks with a cash ledger. No live Core submission is made; a simulated
claim does not verify live sequencer behaviour. Simulation state uses its own
directory and cannot resume after the owned forks close. No testnet mode.

Troubleshooting: inspect ``ACTION=status``, saved transaction evidence and the
reported next command. Missing Core investment: inspect spot/perp/escrow using
``ADDRESS=<safe> NETWORK=mainnet poetry run python scripts/hyperliquid/check-hypercore-user.py``.
Pending burn: relay/reconcile the original message, never burn twice. Pending
Lighter withdrawal: reuse its original history ID. Pending Lagoon claim: claim
settled shares rather than request them again. The script never sleeps overnight.

See ``docs/README-lighter-core-execution.md``, ``docs/README-Hypercore-guard.md``
and https://docs.lagoon.finance/vault/deposit-and-withdraw-flows.
"""

import logging
import os
import shlex
from contextlib import ExitStack
from decimal import Decimal

import requests
from eth_account import Account
from tabulate import tabulate
from web3.exceptions import ContractLogicError

from eth_defi.erc_4626.vault_protocol.lagoon.deployment import LagoonAutomatedDeployment
from eth_defi.erc_4626.vault_protocol.lagoon.funding import SATELLITE_DEPLOYER_USDC
from eth_defi.erc_4626.vault_protocol.lagoon.roundtrip import (
    CHAINS,
    TERMINAL,
    RoundtripContext,
    RoundtripPendingError,
    RoundtripSettings,
    RoundtripState,
    UncertainExecutionError,
    deploy_and_invest,
    fetch_status,
    load_state,
    lock_run,
    unwind_and_redeem,
)
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil, fund_erc20_on_anvil, set_balance
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.fork_blocks import get_midnight_block
from eth_defi.testing.lagoon_roundtrip import RoundtripSimulation
from eth_defi.testing.rpc_cache import seed_default_foundry_rpc_cache
from eth_defi.token import USDC_NATIVE_TOKEN, fetch_erc20_details
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def show_status(ctx: RoundtripContext) -> None:
    """Render current observations and exact safe next commands.

    :param ctx: Loaded or current workflow, including incomplete deployments.
    """
    observations = fetch_status(ctx)
    print(tabulate([(k, observations[k]) for k in ("outcome", "observed_at")], headers=["Run", "Value"]))
    rows = []
    for chain in ctx.deployments:
        row = observations[chain]
        rows.append((chain, "vault" if chain == "arbitrum" else "satellite", row["safe"], row["usdc"]))
    print(tabulate(rows, headers=["Chain", "Role", "Safe", "USDC"]))
    for name in ("core", "lighter"):
        if name in observations:
            print(tabulate([(k, str(v)) for k, v in observations[name].items() if k != "applied"], headers=[name, "Observation"]))
    if observations["pending"]:
        print(tabulate([(name, row.get("burn_hash", "not burned")) for name, row in observations["pending"].items()], headers=["Pending bridge", "Original burn"]))
    path = shlex.quote(str(ctx.settings.state_path))
    command = "poetry run python scripts/lagoon/lagoon-multichain-core-roundtrip.py"
    print(f"State: {ctx.settings.state_path}")
    if ctx.settings.simulate:
        print("SIMULATION: contracts and transactions exist only on the owned forks.")
    else:
        print(f"Inspect: STATE_PATH={path} ACTION=status {command}")
        print(f"Unwind: STATE_PATH={path} ACTION=unwind {command}")
        explorers = {"arbitrum": "https://arbiscan.io", "ethereum": "https://etherscan.io", "hyperliquid": "https://hyperevmscan.io"}
        print(tabulate([(name, f"{explorers[row['chain']]}/tx/{row['hash']}") for name, row in ctx.state.transactions.items()], headers=["Operation", "Transaction"]))
    summary = [(key, ctx.state.results[key]) for key in ("claimed_usdc", "bridge_fees_usdc", "residual_usdc") if key in ctx.state.results]
    if "final_usdc" in ctx.state.results:
        delta = Decimal(ctx.state.results["final_usdc"]) - Decimal(ctx.state.results["initial_usdc"])
        summary.append(("Measured investor USDC delta", str(delta)))
    if summary:
        print(tabulate(summary, headers=["Result", "USDC"], disable_numparse=True))
    if "final_native" in ctx.state.results:
        print(tabulate([(chain, "HYPE" if chain == "hyperliquid" else "ETH", str(Decimal(int(ctx.state.results["initial_native"][chain]) - int(value)) / Decimal(10**18))) for chain, value in ctx.state.results["final_native"].items()], headers=["Chain", "Token", "Measured native spend"]))


def main() -> int:
    """Run the requested tutorial action using saved state and optional local forks.

    Environment settings are validated before deploying. Owned resources are
    released even when a protocol transition remains pending.

    :return: 0 success, 2 known pending work, 1 failure/uncertain execution.
    """
    setup_console_logging(os.environ.get("LOG_LEVEL", "info"))
    settings = RoundtripSettings.from_env()
    with lock_run(settings.state_path), ExitStack() as resources:
        fresh = settings.action in {"deploy", "all"}
        if fresh:
            assert not settings.state_path.exists(), "State exists; use ACTION=status/unwind or a new STATE_PATH"
            account = Account.create() if settings.simulate else Account.from_key(os.environ["LAGOON_MULTCHAIN_TEST_PRIVATE_KEY"])
            state = RoundtripState(simulate=settings.simulate, owner=account.address)
        else:
            state = load_state(settings.state_path, simulate=False)
            for name, variable in (("subscription", "USDC_AMOUNT"), ("position", "POSITION_USDC")):
                stored = Decimal(state.settings[name])
                assert variable not in os.environ or Decimal(os.environ[variable]) == stored, f"{variable} conflicts with this run's saved settings"
                setattr(settings, name, stored)
            if settings.hypercore_vault is not None:
                assert settings.hypercore_vault.lower() == state.targets["hypercore"].lower(), "Conflicting Hypercore target"
            if settings.lighter_pool is not None:
                assert settings.lighter_pool == state.targets["lighter"], "Conflicting Lighter target"
            settings.max_fee = Decimal(os.environ.get("CCTP_MAX_FEE_USDC", state.settings["max_fee"]))
            assert settings.max_fee.is_finite() and 0 <= settings.max_fee <= 1
            settings.max_residual = Decimal(os.environ.get("MAX_RESIDUAL_USDC", state.settings["max_residual"]))
            assert settings.max_residual.is_finite() and 0 <= settings.max_residual <= 1
            account = None if settings.action == "status" or state.outcome in TERMINAL else Account.from_key(os.environ["LAGOON_MULTCHAIN_TEST_PRIVATE_KEY"])
            if account:
                assert account.address.lower() == state.owner.lower(), "Key does not match saved investor"
        connections = {}
        if settings.simulate:
            seed_default_foundry_rpc_cache()
        for chain, chain_id in CHAINS.items():
            rpc = os.environ["JSON_RPC_" + ("HYPERLIQUID" if chain == "hyperliquid" else chain.upper())]
            if settings.simulate:
                fork = fork_network_anvil(rpc, fork_block_number=get_midnight_block(chain_id))
                resources.callback(fork.close)
                rpc = fork.json_rpc_url
            web3 = create_multi_provider_web3(rpc, default_http_timeout=(3, 250))
            assert web3.eth.chain_id == chain_id, f"Wrong RPC chain for {chain}"
            connections[chain] = web3
            logger.info("Connected %s chain %s at block %s", chain, chain_id, web3.eth.block_number)
        tokens = {c: fetch_erc20_details(w, USDC_NATIVE_TOKEN[CHAINS[c]]) for c, w in connections.items()}
        wallets = {c: HotWallet(account) for c in CHAINS} if account else {}
        ctx = RoundtripContext(settings, state, connections, wallets, tokens)
        resources.callback(ctx.hyper_session.close)
        resources.callback(ctx.lighter_session.close)
        ctx.deployments = {c: LagoonAutomatedDeployment.from_json_friendly_dict(connections[c], row) for c, row in state.deployments.items()}
        if settings.simulate:
            simulation = RoundtripSimulation()
            simulation.attach(ctx)
            for web3 in connections.values():
                set_balance(web3, account.address, 100 * 10**18)
            for chain, token in tokens.items():
                fund_erc20_on_anvil(connections[chain], token.address, account.address, token.convert_to_raw(settings.subscription if chain == "arbitrum" else SATELLITE_DEPLOYER_USDC))
        print(
            tabulate(
                [
                    ("Mode/action", f"{'SIMULATION' if settings.simulate else 'LIVE'} / {settings.action}"),
                    ("Investor", state.owner),
                    ("State", settings.state_path),
                    ("Subscription / each position", f"{settings.subscription} / {settings.position} USDC"),
                    ("Hub subscription floor", f"{settings.funding_floor} USDC; plus {SATELLITE_DEPLOYER_USDC} USDC reserve per satellite"),
                    *[(f"{c} deployer USDC", str(tokens[c].fetch_balance_of(state.owner))) for c in CHAINS],
                    *[(f"{c} native gas", f"{connections[c].from_wei(connections[c].eth.get_balance(state.owner), 'ether')} {'HYPE' if c == 'hyperliquid' else 'ETH'}") for c in CHAINS],
                ],
                headers=["Setting", "Value"],
            )
        )
        try:
            if fresh:
                deploy_and_invest(ctx)
            if settings.simulate:
                simulation.advance_investments()
                # Reconstruct workflow objects from disk before exit: no reliance
                # on an executor's memory or a deployment return object.
                state = load_state(settings.state_path, simulate=True)
                ctx = RoundtripContext(settings, state, connections, wallets, tokens)
                ctx.deployments = {c: LagoonAutomatedDeployment.from_json_friendly_dict(connections[c], row) for c, row in state.deployments.items()}
                resources.callback(ctx.hyper_session.close)
                resources.callback(ctx.lighter_session.close)
                simulation.attach(ctx)
            if settings.action in {"unwind", "all"}:
                unwind_and_redeem(ctx)
            if settings.simulate:
                results = ctx.state.results
                expected = settings.subscription + Decimal(results["simulated_profit_usdc"]) - Decimal(1) - Decimal("0.001") - Decimal(results["bridge_fees_usdc"]) - Decimal(results["residual_usdc"])
                assert abs(Decimal(results["claimed_usdc"]) - expected) <= Decimal("0.000001"), "Simulated capital did not reconcile"
                logger.info("Simulation conservation passed: claimed %s USDC; expected %s", results["claimed_usdc"], expected)
                # Terminal invocation must not produce another transaction.
                counts = {c: w.eth.get_transaction_count(ctx.state.owner) for c, w in connections.items()}
                unwind_and_redeem(ctx)
                assert counts == {c: w.eth.get_transaction_count(ctx.state.owner) for c, w in connections.items()}
            show_status(ctx)
            return 0
        except (RoundtripPendingError, TimeoutError) as error:
            logger.warning("Pending: %s", error)
            show_status(ctx)
            return 2
        except (AssertionError, UncertainExecutionError, ValueError, requests.RequestException, ContractLogicError, RuntimeError) as error:
            logger.error("Stopped: %s", error)
            logger.error("Recovery evidence: %s", settings.state_path)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
