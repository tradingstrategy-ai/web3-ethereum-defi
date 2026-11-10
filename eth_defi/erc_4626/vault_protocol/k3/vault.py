"""K3 asynchronous epoch-settled vault readers.

See https://github.com/K3-Capital/k3-vault-contracts/blob/main/ARCHITECTURE.md.
Direct wrappers use operator-reported post-fee NAV at settlement. Amphor-backed
MetaVaultWrapper deployments value underlying vault shares at the last settled rate. The reader
has no certified transaction manager; generic ERC-4626 execution is unsafe.
"""

# Protocol interface overrides intentionally retain constant return values.
# ruff: noqa: PLR6301

from functools import cached_property
from typing import TYPE_CHECKING

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.k3.offchain_metadata import K3VaultMetadata, fetch_k3_vault_metadata
from eth_defi.erc_4626.vault_protocol.k3.tags import STRATEGY_TAGS
from eth_defi.types import Percent
from eth_defi.vault.base import WithdrawalDelayType, WithdrawalPeriod, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

if TYPE_CHECKING:
    from eth_defi.vault.deposit_redeem import VaultDepositManager


#: Length of an ABI-encoded scalar.
EVM_WORD_BYTES = 32


class K3Vault(ERC4626Vault):
    """Read K3's allowlisted wrappers with separate request and claim phases.

    ``maxDeposit`` and ``maxRedeem`` describe settled controller claims, not
    global request capacity. ``paused`` can block new requests; MetaVaultWrapper
    additionally enforces account blacklists and underlying-vault conditions.
    """

    @cached_property
    def k3_metadata(self) -> K3VaultMetadata | None:
        """Fetch first-party identity once per reader instance.

        :return: Validated offchain identity or ``None`` on a registry outage.
        """
        return fetch_k3_vault_metadata(self.chain_id, self.vault_address)

    @property
    def description(self) -> str | None:
        """Return researched individual-vault copy without registry boilerplate.

        Registry identity remains available through :attr:`k3_metadata`.
        Product descriptions use their own sources and do not depend on a
        successful registry request.

        :return: Normalised Markdown, or ``None`` for an unreviewed vault.
        """
        copy = get_handwritten_vault_metadata(self.chain_id, self.vault_address)
        if copy is None:
            return None
        return normalise_vault_description(copy.description)

    @property
    def short_description(self) -> str | None:
        """Return the maintained one-sentence product summary.

        :return: Plain single-line summary, or ``None`` for an unreviewed vault.
        """
        copy = get_handwritten_vault_metadata(self.chain_id, self.vault_address)
        return normalise_vault_short_description(copy.short_description) if copy else None

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return evidence-based tags, retaining unknown strategies where undisclosed.

        :return: Independent tag set or ``None``.
        """
        return lookup_strategy_tags(STRATEGY_TAGS, self.vault_address)

    def get_link(self, referral: str | None = None) -> str:
        """Return K3's public app.

        :param referral: Unused referral identifier.
        :return: Official application URL.
        """
        del referral
        return "https://app.k3.capital/"

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Keep unconfirmed investment-account fee rates unknown.

        The cbBTC documentation gives defaults subject to vault-specific terms,
        which do not establish a rate for a particular block or other products.

        :param block_identifier: Compatibility argument; no fee rate is exposed.
        :return: ``None``, not a zero-fee claim.
        """
        del block_identifier
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Keep unconfirmed performance fee rates unknown.

        Published cbBTC defaults can be overridden by vault-specific terms;
        the reader cannot verify an effective rate from contract state.

        :param block_identifier: Compatibility argument; no fee rate is exposed.
        :return: ``None``.
        """
        del block_identifier
        return None

    def get_withdrawal_period(self) -> WithdrawalPeriod:
        """Describe operator-dependent settlement without promising a duration.

        :return: Asynchronous delay with unknown bounds.
        """
        return WithdrawalPeriod(None, None, WithdrawalDelayType.delay)

    def can_check_deposit(self) -> bool:
        """Disable the misleading zero-address claim-capacity probe.

        :return: ``False``; request availability uses the pause flag instead.
        """
        return False

    def can_check_redeem(self) -> bool:
        """Disable the zero-address redemption-claim probe.

        :return: ``False`` because claim balances are controller-specific.
        """
        return False

    def _fetch_request_closed_reason(self) -> str | None:
        """Read the request pause gate without conflating it with claims.

        :return: Pause reason or ``None``. RPC failures propagate as unknown.
        """
        value = self.web3.eth.call({"to": self.vault_address, "data": bytes.fromhex("5c975abb")}, block_identifier=self._get_block_identifier())
        if len(value) != EVM_WORD_BYTES or int.from_bytes(value) not in {0, 1}:
            message = "Invalid K3 paused() response"
            raise ValueError(message)
        return "New requests are paused" if int.from_bytes(value) else None

    def fetch_deposit_closed_reason(self) -> str | None:
        """Read whether new deposit requests are paused.

        :return: Request pause reason, or ``None``.
        """
        return self._fetch_request_closed_reason()

    def fetch_redemption_closed_reason(self) -> str | None:
        """Read whether new redemption requests are paused.

        :return: Request pause reason, or ``None``; account gates are separate.
        """
        return self._fetch_request_closed_reason()

    def supports_generic_deposit_manager(self) -> bool:
        """Prevent generic synchronous execution of asynchronous claim methods.

        :return: ``False`` until a dedicated lifecycle is certified.
        """
        return False

    def get_deposit_manager(self) -> "VaultDepositManager":
        """Reject execution until the complete K3 lifecycle has guarded coverage.

        :return: Never returns.
        :raises NotImplementedError: K3 execution is not certified.
        """
        message = "K3 asynchronous transaction execution is not supported"
        raise NotImplementedError(message)
