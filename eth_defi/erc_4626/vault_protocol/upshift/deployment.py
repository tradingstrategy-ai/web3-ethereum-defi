"""Configure existing GuardV0 validators for Upshift custody."""

from collections.abc import Iterator

from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault


def fetch_upshift_guard_calls(guard: Contract, vault: UpshiftVault, notes: str) -> Iterator[ContractFunction]:
    """Build owner calls using the guard's existing validated selectors.

    Multi-asset pools have a separate LP token. Register the denomination
    token rather than every accepted input. The vault converts optional
    alternative inputs, which are outside the executor's supported path.
    Library callers using them through guarded custody must separately allow
    those tokens with the existing guard configuration calls.
    Legacy pools use standard deposit and ERC-7540-shaped request selectors
    plus the dated claim. No operator batch or legacy instant selector is
    granted. See the verified
    interfaces in ``eth_defi/abi/upshift/README.md``.

    This builds protocol configuration for the shared ``setup_guard()`` and
    guarded tests. Deployment callers pass descriptors to that existing setup;
    they do not need another Upshift resolver or whitelist implementation.
    Request, claim and payout logic stays in the deposit manager.

    :param guard: GuardV0 or a compatible Lagoon trading module.
    :param vault: Upshift deployment to admit.
    :param notes: Human-readable governance note.
    :return: Configuration calls to execute with the guard owner's authority.
    """
    if vault.multi_asset_like:
        yield guard.functions.whitelistUpshift(vault.address, vault.denomination_token.address, notes)
    else:
        for signature in ("deposit(uint256,address)", "requestRedeem(uint256,address,address)", "claim(uint256,uint256,uint256,address)"):
            yield guard.functions.allowCallSite(vault.address, Web3.keccak(text=signature)[:4], notes)
        yield guard.functions.allowApprovalDestination(vault.address, notes)
        yield guard.functions.whitelistToken(vault.denomination_token.address, notes)
    yield guard.functions.whitelistToken(vault.share_token.address, notes)
