"""Maintained investment strategy classifications for Frankencoin products."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Frankencoin Shares (FCS), Ethereum.
    #: Added: 2026-09-30.
    #: Revenue review: 2026-09-30.
    #: Decision material: Supplies loss-bearing protocol equity with look-through
    #: exposure to collateralised ZCHF lending. Borrowing fees, proposal fees
    #: and net liquidation results accrue to equity, less savings expenses.
    #: The current official position API reports outstanding ZCHF debt backed
    #: by PAXG at 0x45804880de22913dafe09f4980848ece6ecbaf78. Its issuer
    #: confirms physical gold backing, supporting RWA and RWA lending exposure.
    #: These tags describe underlying revenue and collateral exposure: FCS
    #: itself holds FPS equity, not a direct lending claim or a gold allocation.
    #: Sources:
    #: - https://docs.frankencoin.com/pool-shares
    #: - https://docs.frankencoin.com/pool-shares/fcs
    #: - https://docs.frankencoin.com/positions/open
    #: - https://docs.frankencoin.com/positions/auctions
    #: - https://docs.frankencoin.com/reserve
    #: - https://docs.frankencoin.com/savings
    #: - https://docs.frankencoin.com/api-docs/positions
    #: - https://api.frankencoin.com/positions/list
    #: - https://app.frankencoin.com/monitoring/collateral
    #: - https://www.paxos.com/pax-gold
    #: - https://github.com/Frankencoin-ZCHF/Frankencoin/blob/main/contracts/minting/MintingHub.sol
    #: - https://github.com/Frankencoin-ZCHF/Frankencoin/blob/main/contracts/minting/v2/PositionV2.sol
    #: - eth_defi/erc_4626/vault_protocol/frankencoin/shares.py
    "0xdb861830d9ae2d1fcf99fa0cfd3973de382b0b5b": {StrategyTag.protocol_equity, StrategyTag.lending, StrategyTag.rwa, StrategyTag.rwa_lending},
}
