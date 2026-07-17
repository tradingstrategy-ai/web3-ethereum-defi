"""Historical reader for reviewed Libeara fund shares."""

import datetime
from collections.abc import Iterable
from decimal import Decimal

from eth_defi.event_reader.conversion import convert_int256_bytes_to_int
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.vault.base import VaultHistoricalRead, VaultHistoricalReader


class LibearaVaultHistoricalReader(VaultHistoricalReader):
    """Read supply and any reviewed issuer NAV fields at historical blocks."""

    def construct_multicalls(self) -> Iterable[EncodedCall]:
        """Construct the reviewed value calls for this product.

        :return: Supply plus CMTAT NAV calls, or supply only for ULTRA.
        """

        calls = (
            ("totalSupply", self.vault.share_token.contract.functions.totalSupply()),
            ("latestNAV", self.vault.cmtat_contract.functions.latestNAV()),
            ("NAVScalingFactor", self.vault.cmtat_contract.functions.NAVScalingFactor()),
        )
        if self.vault.is_ultra:
            calls = calls[:1]
        for name, call in calls:
            yield EncodedCall.from_contract_call(call, extra_data={"function": name}, first_block_number=self.first_block)

    def process_result(self, block_number: int, timestamp: datetime.datetime, call_results: list[EncodedCallResult]) -> VaultHistoricalRead:
        """Convert available Libeara values to a scan row.

        :param block_number: Sampled EVM block.
        :param timestamp: Naive UTC block timestamp.
        :param call_results: Results for :meth:`construct_multicalls`.
        :return: Supply and NAV-derived USD total assets, or errors.
        """

        values: dict[str, int] = {}
        errors: list[str] = ["No verified on-chain ULTRA NAV/share source is configured"] if self.vault.is_ultra else []
        for result in call_results:
            name = result.call.extra_data["function"]
            if result.success:
                values[name] = convert_int256_bytes_to_int(result.result)
            else:
                errors.append(f"Libeara CMTAT {name} call failed")
        supply = self.vault.share_token.convert_to_decimals(values["totalSupply"]) if "totalSupply" in values else None
        scale = values.get("NAVScalingFactor")
        nav = Decimal(values["latestNAV"]) / Decimal(scale) if scale and "latestNAV" in values else None
        total_assets = supply * nav if supply is not None and nav is not None else None
        return VaultHistoricalRead(vault=self.vault, block_number=block_number, timestamp=timestamp, share_price=nav, total_assets=total_assets, total_supply=supply, performance_fee=None, management_fee=None, errors=errors or None, deposits_open=False, redemption_open=False)
