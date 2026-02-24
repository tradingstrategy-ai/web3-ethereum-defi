"""Forge smart contract development toolchain integration.

- Compile and deploy smart contracts using Forge

- Verify smart contracts on Etherscan, Blockscout, Sourcify, or OKLink

- See `Foundry book <https://book.getfoundry.sh/>`__ for more information.
"""

import datetime
import logging
import time as _time
from pathlib import Path
from shutil import which
from subprocess import DEVNULL, PIPE
from typing import Literal, Tuple

import psutil
from eth_account.signers.local import LocalAccount
from eth_typing import ChecksumAddress, HexAddress, HexStr
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import register_contract
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import is_anvil
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)


#: Crash unless forge completes in 4 minutes
#:
DEFAULT_TIMEOUT = 4 * 60


class ForgeFailed(Exception):
    """Forge command failed."""


#: Because of Forge's
#:
_last_deploy: datetime.datetime | None = None


def _find_deploy_tx_hash(
    web3: Web3,
    deployer_address: str,
    nonce: int,
    scan_blocks: int = 50,
) -> str | None:
    """Find the transaction hash for a contract deployment by nonce.

    Scans recent blocks for a transaction from ``deployer_address`` with the
    given ``nonce``.  Returns the hex tx hash or ``None`` if not found.
    """
    latest = web3.eth.block_number
    deployer_lower = deployer_address.lower()
    for block_num in range(latest, max(latest - scan_blocks, 0), -1):
        try:
            block = web3.eth.get_block(block_num, full_transactions=True)
        except Exception:
            continue
        for tx in block.get("transactions", []):
            if isinstance(tx, dict) and tx.get("from", "").lower() == deployer_lower and tx.get("nonce") == nonce:
                return tx["hash"].hex() if hasattr(tx["hash"], "hex") else tx["hash"]
    return None


def _try_recover_deployment(
    web3: Web3,
    deployer_address: str,
    used_nonce: int,
) -> tuple[str, str] | None:
    """Check if a forge deploy that reported failure actually succeeded on-chain.

    Forge converts all ``PendingTransactionError`` variants into the generic
    ``"contract was not deployed"`` message (`foundry#1362`_).  On load-balanced
    RPCs this is often a false positive — the transaction was mined but forge
    couldn't retrieve the receipt.

    This function checks the on-chain nonce, scans recent blocks for the
    deploy transaction, and returns ``(contract_address, tx_hash)`` if the
    contract is live.

    :return:
        ``(contract_address, tx_hash)`` strings, or ``None`` if recovery failed.

    .. _foundry#1362: https://github.com/foundry-rs/foundry/issues/1362
    """
    onchain_nonce = web3.eth.get_transaction_count(deployer_address)
    if onchain_nonce <= used_nonce:
        # Nonce not consumed — transaction was never mined
        return None

    tx_hash = _find_deploy_tx_hash(web3, deployer_address, used_nonce)
    if not tx_hash:
        return None

    try:
        receipt = web3.eth.get_transaction_receipt(tx_hash)
    except Exception:
        return None

    contract_address = receipt.get("contractAddress")
    if not contract_address:
        return None

    # Final sanity check: is there actually code at this address?
    code = web3.eth.get_code(contract_address)
    if len(code) == 0:
        return None

    return str(contract_address), str(tx_hash)


def _exec_cmd(
    cmd_line: list[str],
    censored_command: str,
    timeout=DEFAULT_TIMEOUT,
    verbose: bool = False,
    cwd: Path | None = None,
) -> Tuple[str, str]:
    """Execute the command line.

    :param timeout:
        Timeout in seconds

    :param cwd:
        Working directory for the forge process.
        Thread-safe alternative to ``os.chdir()``.

    :return:
        Tuple(deployed contract address, tx hash)
    """

    for x in cmd_line:
        assert type(x) == str, f"Got non-string in command line: {x} in {cmd_line}"

    # out = DEVNULL if sys.platform == "win32" else PIPE
    out = PIPE  # TODO: Are we set on a failure on Windows
    proc = psutil.Popen(cmd_line, stdin=DEVNULL, stdout=out, stderr=out, cwd=cwd)
    result = proc.wait(timeout)

    try:
        output = proc.stdout.read().decode("utf-8") + proc.stderr.read().decode("utf-8")
    finally:
        proc.stdout.close()
        proc.stderr.close()

    if result != 0:
        # "No files changed, compilation skipped" only means forge used cached artifacts —
        # it does NOT mean the deployment succeeded. Check for "Deployed to:" to distinguish
        # a successful deploy with cached compilation from an actual failure.
        if "Deployed to:" not in output:
            # Try to extract transaction hash even on failure — forge may have sent
            # the tx before reporting failure (e.g. constructor revert)
            tx_hash_hint = ""
            for line in output.split("\n"):
                if line.startswith("Transaction hash: "):
                    tx_hash_hint = f"\nTransaction hash: {line.split(':')[1].strip()}"
                    break
            raise ForgeFailed(f"forge return code {result} when running: {censored_command}{tx_hash_hint}\nOutput is:\n{output}")

    logger.debug("forge result:\n%s", output)

    address = tx_hash = None

    for line in output.split("\n"):
        # Deployed to: 0x604Da6680Cb97A87403600B9AafBE60eeda97CA4
        if line.startswith("Deployed to: "):
            address = line.split(":")[1].strip()

        if line.startswith("Transaction hash: "):
            tx_hash = line.split(":")[1].strip()

    if not (address and tx_hash):
        raise ForgeFailed(f"Could not parse forge output:\n{output}\nCommand line was:{' '.join(cmd_line)}")

    return address, tx_hash


def deploy_contract_with_forge(
    web3: Web3,
    project_folder: Path,
    contract_file: Path | str,
    contract_name: str,
    deployer: HotWallet | LocalAccount,
    constructor_args: list[str] | None = None,
    etherscan_api_key: str | None = None,
    verifier: Literal["etherscan", "blockscout", "sourcify", "oklink"] | None = None,
    verifier_url: str | None = None,
    register_for_tracing=True,
    timeout=DEFAULT_TIMEOUT,
    wait_for_block_confirmations=0,
    verify_delay=20,
    verify_retries=9,
    verbose=False,
    contract_file_out: Path | str | None = None,
    forge_libraries: dict[str, str] | None = None,
    cache_dir: Path | None = None,
    deploy_retries: int = 1,
) -> Tuple[Contract, HexBytes]:
    """Deploy and verify smart contract with Forge.

    - The smart contracts must be developed with Foundry tool chain and its `forge` command

    - Uses Forge to verify the contract on Etherscan

    - For normal use :py:func:`deploy_contract` is much easier

    Example:

    .. code-block:: python

        guard, tx_hash = deploy_contract_with_forge(
            web3,
            CONTRACTS_ROOT / "guard",  # Foundry projec path
            "GuardV0.sol",  # src/GuardV0.sol
            f"GuardV0",  # GuardV0 is the smart contract name
            deployer,  # Local account with a private key we use for the deployment
            etherscan_api_key=etherscan_api_key,  # Etherscan API key we use for the verification
        )
        logger.info("GuardV0 is %s deployed at %s", guard.address, tx_hash.hex())

        # Test the deployed contract
        assert guard.functions.getInternalVersion().call() == 1

    Assumes standard Foundry project layout with foundry.toml, src and out.

    **Known forge issues with "contract was not deployed"**

    Forge's ``forge create`` has a known issue where all ``PendingTransactionError``
    variants (receipt polling timeout, transaction dropped from mempool, RPC failure)
    are silently converted into a single generic ``"contract was not deployed"``
    message in `crates/forge/src/cmd/create.rs
    <https://github.com/foundry-rs/foundry/blob/master/crates/forge/src/cmd/create.rs>`__:

    .. code-block:: rust

        impl From<PendingTransactionError> for ContractDeploymentError {
            fn from(_err: PendingTransactionError) -> Self {
                Self::ContractNotDeployed  // original error discarded
            }
        }

    On load-balanced RPCs (e.g. drpc.live), this is frequently a **false positive**:
    the transaction was mined but forge polled a different backend node that didn't
    have the receipt yet. `Foundry #1362 <https://github.com/foundry-rs/foundry/issues/1362>`__
    reported ~80% failure rate on testnets. Forge does **not** print the transaction
    hash on failure, so the deployer address + nonce are needed to look up the tx
    on a block explorer.

    This function handles the issue with two mechanisms:

    1. **False-positive recovery**: after a failure, checks the on-chain nonce and
       scans recent blocks to detect if the contract was actually deployed (see
       :py:func:`_try_recover_deployment`).

    2. **Retry with nonce re-sync**: if recovery fails and ``deploy_retries > 1``,
       re-syncs the nonce from the chain and retries the deployment.

    Other relevant Foundry issues:

    - `#877 <https://github.com/foundry-rs/foundry/issues/877>`__ — "forge create sometimes takes two invocations"
    - `#13352 <https://github.com/foundry-rs/foundry/issues/13352>`__ — open issue to improve UX for dropped transactions
    - `#1803 <https://github.com/foundry-rs/foundry/issues/1803>`__ — ``--gas-estimate-multiplier`` not available for ``forge create`` (only ``forge script``)

    **RPC URL selection and L2 sequencers**

    ``forge create`` requires a **full RPC** that supports both reads
    (``eth_chainId``, ``eth_gasPrice``, ``eth_getTransactionReceipt``) and writes
    (``eth_sendRawTransaction``).  This rules out L2 sequencer endpoints:

    - **Arbitrum sequencers** (e.g. ``arb1-sequencer.arbitrum.io/rpc``) are
      **write-only** — they only accept ``eth_sendRawTransaction``.
      Forge will fail immediately because it calls ``eth_chainId`` first.
    - **OP Stack sequencers** (Base, Optimism) run a full ``op-geth`` but
      may return **403 Forbidden** on read calls under load.

    For forge deployments, prefer the chain's **official single-endpoint public RPC**
    over a load-balanced aggregator like drpc.live.  These route to one backend,
    avoiding the receipt-polling inconsistency that triggers foundry#1362:

    - Arbitrum Sepolia: ``https://sepolia-rollup.arbitrum.io/rpc``
    - Base Sepolia: ``https://sepolia.base.org``
    - Arbitrum One: ``https://arb1.arbitrum.io/rpc``
    - Base: ``https://mainnet.base.org``

    See :py:data:`eth_defi.chain.SEQUENCERS` for the full mapping of chain IDs
    to sequencer and public RPC URLs.

    When using :py:class:`~eth_defi.provider.multi_provider.MultiProviderWeb3`,
    this function automatically selects the **call** provider URL (not the
    ``mev+`` broadcast endpoint) via ``web3.provider.call_endpoint_uri``.

    See

    - `Foundry book <https://book.getfoundry.sh/>`__ for more information

    - :py:func:`eth_defi.deploy.deploy_contract` for simple, non-verified contract deployments

    - :ref:`multi rpc` tutorial for MEV protection and multi-provider configuration

    :param web3:
        Web3 instance

    :param deployer:
        Deployer tracked as a hot wallet.

        We need to be able to manually track the nonce across multiple contract deployments.

    :param project_folder:
        Foundry project with `foundry.toml` in the root.

    :param contract_file:
        Contract path relative to the project folder.

        E.g. `TermsOfService.sol`.

    :param contract_name:
        The smart contract name within the file.

        E.g. `TermsOfService`.

    :param constructor_args:
        Other arguments to pass to the contract's constructor.

        Need to be able to stringify these for forge.

    :param etherscan_api_key:
        API key for Etherscan-compatible verification services.

        Required when using ``verifier="etherscan"`` or ``verifier="oklink"``.

        Not needed for Blockscout or Sourcify.

        E.g. `3F3H8....`.

    :param verifier:
        The contract verification provider to use.

        Supported values:

        - ``"etherscan"``: Etherscan and compatible explorers (requires API key)
        - ``"blockscout"``: Blockscout explorers (requires verifier_url)
        - ``"sourcify"``: Sourcify verification (no API key required)
        - ``"oklink"``: OKLink explorer (requires API key)

        If ``None`` but ``etherscan_api_key`` is provided, defaults to ``"etherscan"``
        for backward compatibility.

    :param verifier_url:
        Custom verifier URL for Blockscout or other custom verification endpoints.

        Required when ``verifier="blockscout"``.

        Example: ``"https://base.blockscout.com/api/"``

    :param register_for_tracing:
        Make the symbolic contract information available on web3 instance.

        See :py:func:`get_contract_registry`

    :param wait_for_block_confirmations:
        Currently not used.

    :param verbose:
        Try to be extra verbose with Forge output to pin point errors

    :param forge_libraries:
        Pre-deployed library addresses for ``--libraries`` flag.

        Maps ``"source_path:LibraryName"`` to deployed address.
        E.g. ``{"src/lib/CowSwapLib.sol:CowSwapLib": "0x000..."}``.

        Use :py:func:`eth_defi.deploy.build_guard_forge_libraries` to build
        this mapping for guard contracts.

    :param cache_dir:
        Isolated directory for forge's ``--cache-path`` and ``--out`` flags.
        When set, forge writes compilation cache and ABI artifacts here
        instead of in ``project_folder``.  This allows multiple concurrent
        forge processes to share the same source tree without lock contention.

    :param deploy_retries:
        Number of attempts when forge reports ``"contract was not deployed"``.

        On unreliable testnet RPCs (e.g. drpc.live load-balanced Base Sepolia),
        forge may fail to confirm the deploy transaction.  Setting this > 1
        will re-sync the nonce and retry.  Default: 1 (no retries).

        Safety: values > 1 are only allowed on testnets (Anvil or chain IDs
        listed in :py:data:`eth_defi.chain.TESTNET_CHAIN_IDS`).
        A ``ValueError`` is raised if retries are requested on mainnet.

    :raise ForgeFailed:
        In the case we could not deploy the contract.

        - Running forge failed
        - Transaction could not be confirmed

    :return:
        Contract and deployment tx hash.

    """
    assert isinstance(project_folder, Path), f"Got non-Path project folder: {type(project_folder)} {project_folder}"
    assert type(contract_name) == str

    # Safety: deploy retries are only allowed on testnets/Anvil
    if deploy_retries > 1:
        from eth_defi.chain import TESTNET_CHAIN_IDS

        chain_id = web3.eth.chain_id
        if not is_anvil(web3) and chain_id not in TESTNET_CHAIN_IDS:
            raise ValueError(f"deploy_retries={deploy_retries} is only allowed on testnets or Anvil, not on chain {chain_id}. Retrying forge deployments on mainnet is dangerous.")

    # Do NOT call sync_nonce here — the caller is responsible for syncing
    # the nonce once at startup.  Re-syncing before every transaction can
    # reset the counter backwards on load-balanced RPCs (see HotWallet.sync_nonce docstring).
    if isinstance(deployer, HotWallet) and deployer.current_nonce is None:
        deployer.sync_nonce(web3)

    if constructor_args is None:
        constructor_args = []

    if type(contract_file) == str:
        contract_file = Path(contract_file)

    assert isinstance(contract_file, Path)
    assert type(constructor_args) in (list, tuple)

    # Backward compatibility: if etherscan_api_key provided without verifier, assume etherscan
    if etherscan_api_key and verifier is None:
        verifier = "etherscan"

    # Validate verifier-specific requirements
    if verifier == "blockscout" and not verifier_url:
        raise ValueError("verifier_url is required when using Blockscout verifier")

    if verifier in ("etherscan", "oklink") and not etherscan_api_key:
        raise ValueError(f"etherscan_api_key is required when using {verifier} verifier")

    # Use call provider URL instead of transact provider,
    # because MEV sequencer endpoints (mev+https://) do not support
    # standard RPC methods like eth_chainId that forge requires.
    json_rpc_url = getattr(web3.provider, "call_endpoint_uri", None) or web3.provider.endpoint_uri

    forge = which("forge")
    assert forge is not None, "No forge command in path, needed for the contract deployment"

    src_contract_file = Path("src") / contract_file

    if isinstance(deployer, HotWallet):
        private_key = deployer.private_key.hex()
        deployer_address = deployer.address
    elif isinstance(deployer, LocalAccount):
        private_key = deployer._private_key.hex()
        deployer_address = deployer.address
    else:
        raise NotImplementedError(f"Unsupported deployer: {deployer}")

    # Determine output directory for ABI artifacts
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        out_dir = cache_dir / "out"
        out_dir.mkdir(exist_ok=True)
    else:
        out_dir = project_folder / "out"

    assert (project_folder / "foundry.toml").exists(), f"foundry.toml missing: {project_folder}"

    full_contract_path = project_folder / src_contract_file
    assert src_contract_file.suffix == ".sol", f"Not Solidity source file: {contract_file}"
    assert full_contract_path.exists(), f"Contract does not exist: {full_contract_path}"

    # Build the nonce-independent portion of the command.
    # The nonce and private key are injected per attempt inside the retry loop.
    base_cmd_args = [
        "--broadcast",
        "--rpc-url",
        json_rpc_url,
    ]

    # Isolate forge cache and output to avoid lock contention
    # when running concurrent deployments from the same project folder
    if cache_dir is not None:
        base_cmd_args += [
            "--cache-path",
            str(cache_dir / "cache"),
            "--out",
            str(out_dir),
        ]

    if verbose:
        base_cmd_args.append("-vvv")

    if verifier:
        if is_anvil(web3):
            logger.warning("Contract verification skipped, running on a local fork")
        else:
            logger.info("Doing %s verification with %d retries", verifier, verify_retries)
            # Tuned retry parameters
            # https://github.com/foundry-rs/foundry/issues/6953
            base_cmd_args += [
                "--verifier",
                verifier,
                "--verify",
                "--retries",
                str(verify_retries),
                "--delay",
                str(verify_delay),
            ]

            # Add API key for verifiers that require it
            if verifier in ("etherscan", "oklink") and etherscan_api_key:
                base_cmd_args += [
                    "--etherscan-api-key",
                    etherscan_api_key,
                ]

            # Add custom verifier URL
            if verifier_url:
                base_cmd_args += [
                    "--verifier-url",
                    verifier_url,
                ]

    # Add library linking flags (--libraries path:name:address)
    if forge_libraries:
        for source_key, address in forge_libraries.items():
            base_cmd_args += ["--libraries", f"{source_key}:{address}"]

    base_cmd_args += [f"{src_contract_file}:{contract_name}"]

    if constructor_args:
        base_cmd_args += ["--constructor-args"]
        for arg in constructor_args:
            base_cmd_args.append(arg)

    # Retry loop for unreliable testnet RPCs where forge may fail
    # to confirm the deploy transaction ("contract was not deployed").
    last_error = None
    for attempt in range(1, deploy_retries + 1):
        # Allocate nonce for this attempt
        if isinstance(deployer, HotWallet):
            nonce = str(deployer.allocate_nonce())
        else:
            nonce = str(web3.eth.get_transaction_count(deployer.address))

        cmd_line = [forge, "create", "--nonce", nonce] + base_cmd_args

        try:
            censored_command = " ".join(cmd_line)
        except TypeError as e:
            raise TypeError(f"Could not splice command line: {cmd_line}") from e

        retry_tag = f" (attempt {attempt}/{deploy_retries})" if deploy_retries > 1 else ""
        logger.info(
            "Deploying %s with forge%s. Deployer %s nonce %s, cache %s, command: %s",
            contract_name,
            retry_tag,
            deployer_address,
            nonce,
            cache_dir or "default",
            censored_command,
        )

        # Inject private key after logging (not shown in logs)
        cmd_line_with_key = [forge, "create", "--private-key", private_key, "--nonce", nonce] + base_cmd_args

        try:
            # Pass cwd to _exec_cmd instead of os.chdir() which is not thread-safe
            contract_address, tx_hash = _exec_cmd(cmd_line_with_key, timeout=timeout, censored_command=censored_command, verbose=verbose, cwd=project_folder)
            break  # Success
        except ForgeFailed as e:
            last_error = e
            error_msg = str(e)
            retryable = "contract was not deployed" in error_msg or "nonce too low" in error_msg
            if attempt < deploy_retries and retryable:
                # Two common false-positive / stale-nonce scenarios on testnets:
                #
                # 1. "contract was not deployed" — Forge silently converts
                #    PendingTransactionError → ContractNotDeployed (foundry#1362).
                #    The tx may have actually been mined but forge lost the receipt.
                #
                # 2. "nonce too low" — A previous attempt's tx WAS mined but
                #    forge reported failure. On the next attempt sync_nonce
                #    returned a stale value, so forge tries to reuse a consumed
                #    nonce.  This confirms the prior deploy actually succeeded.
                #
                # In both cases, try to recover the already-deployed contract
                # before falling back to a fresh retry with re-synced nonce.
                _time.sleep(5)
                used_nonce = int(nonce)

                # For "nonce too low", the *previous* nonce was consumed,
                # so the contract was deployed at (used_nonce - 1) if this
                # is a retry, or at the current used_nonce for first failure.
                recovery_nonce = used_nonce
                if "nonce too low" in error_msg and attempt > 1:
                    recovery_nonce = used_nonce - 1

                recovered = _try_recover_deployment(web3, deployer_address, recovery_nonce)
                if recovered:
                    contract_address, tx_hash = recovered
                    logger.warning(
                        "Forge reported failure but contract was actually deployed at %s (nonce %s, tx %s). Recovering from false positive (foundry#1362).",
                        contract_address,
                        recovery_nonce,
                        tx_hash,
                    )
                    break

                logger.warning(
                    "Forge deploy failed on attempt %d/%d (nonce %s, deployer %s, error: %s). Re-syncing nonce and retrying in 5s…",
                    attempt,
                    deploy_retries,
                    nonce,
                    deployer_address,
                    error_msg[:120],
                )
                _time.sleep(5)
                # Re-sync nonce from chain — the failed tx may or may not
                # have consumed the nonce depending on whether it was sent.
                if isinstance(deployer, HotWallet):
                    deployer.sync_nonce(web3)
                continue
            raise
    else:
        # All retries exhausted
        raise last_error

    if contract_file_out is None:
        contract_file_out = contract_file

    # Check we produced an ABI file, or was created earlier
    contract_abi = out_dir / contract_file_out / f"{contract_name}.json"
    assert contract_abi.exists(), f"Forge did not produce ABI file: {contract_abi.resolve()}"

    # Mad Web3.py API
    contract_address = ChecksumAddress(HexAddress(HexStr(contract_address)))
    instance = get_deployed_contract(web3, contract_abi, contract_address)

    if register_for_tracing:
        instance.name = contract_name
        register_contract(web3, contract_address, instance)

    tx_hash = HexBytes(tx_hash)
    assert_transaction_success_with_explanation(
        web3,
        tx_hash,
        RaisedException=ForgeFailed,
    )

    return instance, tx_hash
