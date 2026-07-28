"""Goat Protocol deposit and redemption support."""

from hexbytes import HexBytes

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.vault.deposit_redeem import DepositRedeemEventAnalysis, DepositRedeemEventFailure, DepositTicket, RedemptionTicket


class GoatDepositManager(ERC4626DepositManager):
    """Synchronous Goat ERC-4626 manager with explicit deposit-event decoding.

    Goat's ``Multistrategy`` contract also inherits a two-argument
    ``Deposit(uint256,address)`` event. Web3 resolves an event by bare name to
    that non-ERC-4626 overload, so the normal analyser cannot see the
    ERC-4626 deposit. This manager selects the canonical four-argument event
    defined by `EIP-4626 <https://eips.ethereum.org/EIPS/eip-4626#events>`__.
    """

    def analyse_deposit(
        self,
        claim_tx_hash: HexBytes | str,
        deposit_ticket: DepositTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse a Goat deposit using its canonical ERC-4626 event overload.

        :param claim_tx_hash:
            Mined direct or Guard-mediated deposit transaction hash.
        :param deposit_ticket:
            Optional ticket whose owner identifies a guarded SimpleVault.
        :return:
            Decoded raw deposit and minted-share quantities, or a failure.
        """
        return self._analyse_deposit(
            claim_tx_hash,
            deposit_ticket,
            deposit_event_signature="Deposit(address,address,uint256,uint256)",
        )

    def analyse_redemption(
        self,
        claim_tx_hash: HexBytes | str,
        redemption_ticket: RedemptionTicket | None,
    ) -> DepositRedeemEventAnalysis | DepositRedeemEventFailure:
        """Analyse a Goat redemption using its canonical ERC-4626 event overload.

        :param claim_tx_hash:
            Mined direct or Guard-mediated redemption transaction hash.
        :param redemption_ticket:
            Optional ticket whose owner identifies a guarded SimpleVault.
        :return:
            Decoded raw redemption and burned-share quantities, or a failure.
        """
        return self._analyse_redemption(
            claim_tx_hash,
            redemption_ticket,
            redemption_event_signature="Withdraw(address,address,address,uint256,uint256)",
        )
