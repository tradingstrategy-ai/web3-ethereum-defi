"""
Prepare and verify the Safe transaction that whitelists a GMX ExchangeRouter

GMX rotates its ``ExchangeRouter`` between contract releases. A Lagoon vault's
Guard enforces a **fixed on-chain address allowlist**, so until the new router is
whitelisted every order-creating transaction reverts with
``execution reverted: Target not allowed`` — **including exits**, which means the
bot cannot flatten risk while it lasts.

Fixing it is a single Safe transaction. This script prepares that transaction and
verifies it afterwards.

**This script never signs, sends, or holds a private key.** It only reads the
chain and prints what to submit. Signing happens in the Safe UI, through the
normal multisig flow.

Usage
-----

Before the Safe transaction — check state and print what to submit::

    export JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc"
    python scripts/gmx/prepare-guard-whitelist.py

After the Safe transaction has been executed — confirm it worked::

    python scripts/gmx/prepare-guard-whitelist.py --verify

For a different vault, or a future GMX release::

    python scripts/gmx/prepare-guard-whitelist.py \\
        --vault 0x... --exchange-router 0x... --notes "GMX v2.2d"

Environment variables
---------------------

``JSON_RPC_ARBITRUM``
    Arbitrum mainnet RPC endpoint. Read-only access is sufficient.

What the transaction does
-------------------------

One call to ``whitelistGMX()`` on the vault's Guard, which atomically:

1. allows ``multicall`` on the new ExchangeRouter,
2. allows the SyntheticsRouter as an approval destination,
3. maps the new router to its OrderVault (for ``sendWnt``/``sendTokens`` checks),
4. whitelists the collateral tokens.

It is **additive**. The previous router stays whitelisted, so rollback remains
available and nothing that currently works stops working.

Exit codes
----------

``0``
    Nothing to do, or (with ``--verify``) the whitelist is confirmed correct.
``1``
    Action required, or verification failed. Suitable for a monitoring check.
"""

import argparse
import os
import sys

from eth_defi.abi import get_contract
from web3 import HTTPProvider, Web3

#: Lagoon vault whose Guard is being updated. Default is the gmx_ai production vault.
DEFAULT_VAULT = "0xEb49c6f1078FbB69f953F4A3e4B87A70acfed1d3"

#: GMX v2.2c ExchangeRouter on Arbitrum — the address that must be whitelisted.
DEFAULT_EXCHANGE_ROUTER = "0x7dE39FF2e232A2203196788d37e234cF8F1b83f1"

#: SyntheticsRouter. Unchanged between v2.2b and v2.2c, so existing ERC-20
#: approvals stay valid and no re-approval transaction is needed.
DEFAULT_SYNTHETICS_ROUTER = "0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6"

#: OrderVault. Also unchanged, so the Guard's router-to-vault mapping value is
#: identical to the one already in place for the previous router.
DEFAULT_ORDER_VAULT = "0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5"

#: Collateral token the vault trades with (native USDC on Arbitrum).
DEFAULT_COLLATERAL = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

#: Previous ExchangeRouter, checked only to report that rollback stays possible.
PREVIOUS_EXCHANGE_ROUTER = "0x1C3fa76e6E1088bCE750f23a5BFcffa1efEF6A41"

#: GMX RoleStore, the authority on whether a router is a genuine, still-authorised
#: GMX contract. Guards against whitelisting a decommissioned or bogus address.
ROLE_STORE = "0x3c3d99FD298f679DBC2CEcd132b4eC4d0F5e6e72"

RULE = "=" * 78


def parse_args() -> argparse.Namespace:
    """Parse command line arguments.

    :return: Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Prepare and verify the Safe transaction that whitelists a GMX ExchangeRouter on a Lagoon vault Guard.",
    )
    parser.add_argument("--vault", default=DEFAULT_VAULT, help="Lagoon vault address (default: the gmx_ai production vault)")
    parser.add_argument("--exchange-router", default=DEFAULT_EXCHANGE_ROUTER, help="ExchangeRouter to whitelist")
    parser.add_argument("--synthetics-router", default=DEFAULT_SYNTHETICS_ROUTER, help="SyntheticsRouter")
    parser.add_argument("--order-vault", default=DEFAULT_ORDER_VAULT, help="OrderVault")
    # No default here: with action="append" argparse appends to the default
    # rather than replacing it, so a string default would be iterated per
    # character. Fall back to DEFAULT_COLLATERAL below instead.
    parser.add_argument("--collateral", action="append", help=f"Collateral token; repeatable (default: {DEFAULT_COLLATERAL})")
    parser.add_argument("--notes", default="GMX v2.2c", help="On-chain notes string recorded with the whitelist")
    parser.add_argument("--verify", action="store_true", help="Check the whitelist AFTER the Safe transaction has executed")
    return parser.parse_args()


def resolve_web3() -> Web3:
    """Connect to Arbitrum using the configured RPC endpoint.

    :return: Connected Web3 instance.
    :raises SystemExit: If no endpoint is configured or it is not Arbitrum.
    """
    url = os.environ.get("JSON_RPC_ARBITRUM") or os.environ.get("ARBITRUM_CHAIN_JSON_RPC")
    if not url:
        sys.exit("Set JSON_RPC_ARBITRUM to an Arbitrum RPC endpoint (read-only access is enough).")
    web3 = Web3(HTTPProvider(url))
    if web3.eth.chain_id != 42161:
        sys.exit(f"Expected Arbitrum mainnet (chain 42161), connected to chain {web3.eth.chain_id}.")
    return web3


def find_guard(web3: Web3, vault_address: str) -> tuple[str, str]:
    """Resolve a vault's Safe and the Guard module enabled on it.

    :param web3: Connected Web3 instance.
    :param vault_address: Lagoon vault address.
    :return: Tuple of ``(safe_address, guard_address)``.
    :raises SystemExit: If the Safe has no enabled module.
    """
    from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
    from eth_defi.vault.base import VaultSpec

    vault = LagoonVault(web3, VaultSpec(42161, vault_address))
    safe_address = Web3.to_checksum_address(vault.safe_address)

    guard_address = vault.trading_strategy_module_address
    if not guard_address:
        # LagoonVault does not always resolve the module, so read the Safe's
        # enabled-module list directly. SENTINEL_MODULES == address(0x1).
        safe_abi = [
            {
                "inputs": [{"name": "start", "type": "address"}, {"name": "pageSize", "type": "uint256"}],
                "name": "getModulesPaginated",
                "outputs": [{"name": "array", "type": "address[]"}, {"name": "next", "type": "address"}],
                "stateMutability": "view",
                "type": "function",
            }
        ]
        safe = web3.eth.contract(address=safe_address, abi=safe_abi)
        modules, _ = safe.functions.getModulesPaginated("0x0000000000000000000000000000000000000001", 10).call()
        if not modules:
            sys.exit(f"Safe {safe_address} has no enabled modules — cannot locate the Guard.")
        guard_address = modules[0]

    return safe_address, Web3.to_checksum_address(guard_address)


def check_router_is_authorised(web3: Web3, router: str) -> bool:
    """Ask GMX's RoleStore whether a router is a genuine, authorised GMX contract.

    Whitelisting an address that GMX never authorised — or has since
    decommissioned — would allow the vault to send orders somewhere useless.
    Cheap insurance against a typo or a copied-from-the-wrong-place address.

    :param web3: Connected Web3 instance.
    :param router: ExchangeRouter address to check.
    :return: ``True`` if the router currently holds GMX's CONTROLLER role.
    """
    from eth_abi import encode

    role_key = web3.keccak(encode(["string"], ["CONTROLLER"]))
    abi = [
        {
            "inputs": [{"name": "account", "type": "address"}, {"name": "roleKey", "type": "bytes32"}],
            "name": "hasRole",
            "outputs": [{"type": "bool"}],
            "stateMutability": "view",
            "type": "function",
        }
    ]
    store = web3.eth.contract(address=Web3.to_checksum_address(ROLE_STORE), abi=abi)
    return store.functions.hasRole(Web3.to_checksum_address(router), role_key).call()


def main() -> int:
    """Report the Guard's state and print the Safe transaction to submit.

    :return: Process exit code.
    """
    args = parse_args()
    web3 = resolve_web3()

    router = Web3.to_checksum_address(args.exchange_router)
    synthetics_router = Web3.to_checksum_address(args.synthetics_router)
    order_vault = Web3.to_checksum_address(args.order_vault)
    collateral = [Web3.to_checksum_address(c) for c in dict.fromkeys(args.collateral or [DEFAULT_COLLATERAL])]

    safe_address, guard_address = find_guard(web3, args.vault)
    guard = web3.eth.contract(address=guard_address, abi=get_contract(web3, "guard/GuardV0.json").abi)

    print(RULE)
    print("GMX ROUTER WHITELIST — Lagoon vault Guard")
    print(RULE)
    print(f"  chain          Arbitrum ({web3.eth.chain_id}), block {web3.eth.block_number:,}")
    print(f"  vault          {args.vault}")
    print(f"  Safe           {safe_address}")
    print(f"  Guard          {guard_address}")

    allowed = guard.functions.isAllowedGMXRouter(router).call()
    mapped_vault = guard.functions.gmxOrderVaults(router).call()
    mapping_ok = Web3.to_checksum_address(mapped_vault) == order_vault

    if args.verify:
        print(f"\n{RULE}\nVERIFICATION\n{RULE}")
        print(f"  isAllowedGMXRouter({router[:10]}…)  = {allowed}")
        print(f"  gmxOrderVaults({router[:10]}…)      = {mapped_vault}")
        print(f"  expected OrderVault                  = {order_vault}")
        print(f"  isAllowedReceiver(Safe)              = {guard.functions.isAllowedReceiver(safe_address).call()}")
        if allowed and mapping_ok:
            print("\n  RESULT: PASS — the router is whitelisted and correctly mapped.")
            print("  Trading can resume. Note the bot pauses a pair for 1 hour after 3")
            print("  consecutive reverts, so restart the service to clear it immediately.")
            return 0
        print("\n  RESULT: FAIL — the whitelist is not in place. Orders will still revert.")
        return 1

    # --- Pre-flight report -------------------------------------------------
    print(f"\n{RULE}\nALREADY IN PLACE (no action needed)\n{RULE}")
    owner = guard.functions.owner().call()
    checks = [
        ("Guard owner is the Safe", Web3.to_checksum_address(owner) == safe_address),
        ("Safe allowed as order receiver", guard.functions.isAllowedReceiver(safe_address).call()),
        ("Previous router still allowed (rollback stays available)", guard.functions.isAllowedGMXRouter(Web3.to_checksum_address(PREVIOUS_EXCHANGE_ROUTER)).call()),
    ]
    for token in collateral:
        checks.append((f"Collateral {token[:10]}… allowed as asset", guard.functions.isAllowedAsset(token).call()))
    for label, ok in checks:
        print(f"  [{'OK' if ok else '!!'}] {label}")
    print("  [OK] SyntheticsRouter unchanged — existing ERC-20 approval stays valid")
    print("  [OK] OrderVault unchanged — no guard remap needed")

    print(f"\n{RULE}\nWHAT IS MISSING\n{RULE}")
    if allowed and mapping_ok:
        print(f"  Nothing. {router} is already whitelisted and correctly mapped.")
        print("  No transaction required.")
        return 0
    print(f"  [!!] isAllowedGMXRouter({router}) = {allowed}")
    print("       Until this is true, every order-creating transaction reverts with")
    print("       'execution reverted: Target not allowed' — exits included.")

    # --- Sanity-check the router before asking anyone to sign for it -------
    authorised = check_router_is_authorised(web3, router)
    print(f"\n  GMX RoleStore says this router is authorised: {authorised}")
    if not authorised:
        print("  REFUSING to print a transaction for a router GMX has not authorised.")
        print("  Check the address against https://github.com/gmx-io/gmx-synthetics")
        return 1

    data = guard.encode_abi("whitelistGMX", args=[router, synthetics_router, order_vault, collateral, args.notes])

    print(f"\n{RULE}\nTHE TRANSACTION TO SUBMIT (one Safe transaction)\n{RULE}")
    print(f"  From   {safe_address}   <- the Safe; it owns the Guard")
    print(f"  To     {guard_address}   <- the Guard")
    print("  Value  0")
    print(f"  Data   {data}")
    print("\n  Decoded:")
    print("    whitelistGMX(")
    print(f"      {router},   // ExchangeRouter — the only new address")
    print(f"      {synthetics_router},   // SyntheticsRouter — unchanged")
    print(f"      {order_vault},   // OrderVault — unchanged")
    print(f"      {collateral},")
    print(f'      "{args.notes}"')
    print("    )")

    try:
        web3.eth.call({"to": guard_address, "from": safe_address, "data": data})
        print("\n  Simulation from the Safe: OK — this call succeeds at the current block.")
    except Exception as exc:  # noqa: BLE001 - surface whatever the node reports
        print(f"\n  Simulation from the Safe: FAILED — {type(exc).__name__}: {exc}")
        print("  Do not submit. Investigate before signing.")
        return 1

    print(f"\n{RULE}\nHOW TO SUBMIT\n{RULE}")
    print("  1. Safe UI -> New transaction -> Transaction Builder")
    print(f"  2. Contract address: {guard_address}")
    print("  3. Paste the Data field above as raw hex (ABI not required), value 0")
    print("  4. Collect signatures and execute. Roughly 231,000 gas.")
    print("  5. Confirm with:  python scripts/gmx/prepare-guard-whitelist.py --verify")
    print("\n  This is additive — the previous router stays whitelisted, so rollback")
    print("  remains available and nothing currently working is affected.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
