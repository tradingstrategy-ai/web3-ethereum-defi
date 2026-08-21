"""Current deposit-permission reader for Enzyme Onyx vaults.

Onyx stores deposit authority in a mutable set of handler accounts on each
``Shares`` contract.  The set is reconstructed from Shares events by the
chain-level discovery pipeline; this module then inspects every active handler
at one fixed block using Multicall3.  Keeping event indexing separate from
current-state reads avoids one historical scan per vault while still reflecting
handler configuration changes made after deployment.

The reviewed handler surfaces come from the canonical Onyx contracts:

* ``SyncDepositHandler`` uses ``getDepositorAllowlist()``;
* ``ERC7540LikeDepositQueue`` uses ``getDepositRestriction()``;
* ``SharesMintHandler`` permits only the vault owner or an admin to select
  recipients for shares minted after an offchain subscription.

Issuance hooks can run arbitrary policy engines, including Chainlink ACE.  A
route with an unrecognised hook is therefore not reported as public merely
because its built-in allowlist is disabled.  See the official protocol source:
https://github.com/enzymefinance/protocol-onyx/tree/main/src/components/issuance
"""

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult, read_multicall_chunked
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.vault.deposit_redeem import VaultDepositPermission

#: Size of one ABI-encoded scalar return value.
ABI_WORD_SIZE = 32


#: Getter signatures used to identify and inspect every reviewed handler type.
#: All getters for all handlers are packed into Multicall3.  Reverting selectors
#: are expected and distinguish interfaces without additional RPC requests.
ONYX_HANDLER_GETTERS: tuple[str, ...] = (
    "getDepositorAllowlist()",
    "getPostDepositHook()",
    "getDepositRestriction()",
    "getPreRequestDepositHook()",
    "getPostExecuteDepositRequestHook()",
    "getPreMintHook()",
)


def create_onyx_permission_calls(active_handlers: Mapping[HexAddress, Iterable[HexAddress]]) -> Iterator[EncodedCall]:
    """Create current-state Multicall probes for all active handlers.

    Unsupported selectors are allowed to revert inside Multicall3.  The
    combination of successful getters identifies a standard handler while a
    custom handler remains explicitly unknown.

    :param active_handlers:
        Lower-case Shares addresses mapped to active handler addresses, as
        reconstructed from ``DepositHandlerAdded`` and
        ``DepositHandlerRemoved`` events.
    :return:
        Iterator of encoded getter calls carrying Shares and handler context.
    """

    for shares_address, handlers in active_handlers.items():
        for handler_address in handlers:
            for signature in ONYX_HANDLER_GETTERS:
                function_name = signature.removesuffix("()")
                yield EncodedCall.from_keccak_signature(
                    address=HexAddress(handler_address),
                    signature=Web3.keccak(text=signature)[:4],
                    function=function_name,
                    data=b"",
                    extra_data={
                        "shares_address": shares_address.lower(),
                        "handler_address": handler_address.lower(),
                    },
                )


def _has_word(result: EncodedCallResult | None) -> bool:
    """Check that a getter returned one ABI word successfully.

    Handler identification relies on selector success. Short or reverted
    responses are treated as unsupported interfaces instead of being decoded.

    :param result: Multicall result or ``None`` for a missing selector.
    :return: ``True`` when one ABI word can be decoded.
    """

    return result is not None and result.success and len(result.result) >= ABI_WORD_SIZE


def _decode_address(result: EncodedCallResult) -> HexAddress:
    """Decode one ABI address getter result.

    ABI addresses occupy the final 20 bytes of a 32-byte return word. The
    normalised lower-case result is suitable for comparisons and map keys.

    :param result: Successful 32-byte Multicall result.
    :return: Lower-case EVM address.
    """

    return HexAddress(Web3.to_checksum_address(result.result[-20:]).lower())


def _decode_uint(result: EncodedCallResult) -> int:
    """Decode one ABI unsigned integer or enum getter result.

    The reviewed queue restriction getter returns an ABI enum value encoded as
    an unsigned integer, so the complete return word can be decoded directly.

    :param result: Successful 32-byte Multicall result.
    :return: Integer value.
    """

    return int.from_bytes(result.result[-32:], byteorder="big")


def classify_onyx_deposit_handler(
    results: Mapping[str, EncodedCallResult],
) -> VaultDepositPermission:
    """Classify one active Onyx handler from its current getter results.

    Built-in address/controller allowlists are conclusive evidence of prior
    account approval.  When those restrictions are disabled, all relevant
    issuance hooks must also be absent before a route is called public: an
    arbitrary hook can revert the transaction through an external compliance
    or policy engine.

    ``SharesMintHandler`` is permissioned even without a list because its
    ``mint()`` entrypoint is callable only by the vault owner or an admin, who
    explicitly selects each recipient.  This fits
    :class:`~eth_defi.vault.deposit_redeem.VaultDepositPermission.whitelisted`'s
    broader meaning of prior manual account approval.

    :param results:
        Successful and failed getter results keyed by function name.
    :return:
        Current identity-approval requirement for this handler.
    """

    depositor_allowlist = results.get("getDepositorAllowlist")
    post_deposit_hook = results.get("getPostDepositHook")
    if _has_word(depositor_allowlist) and _has_word(post_deposit_hook):
        allowlist_address = _decode_address(depositor_allowlist)
        hook_address = _decode_address(post_deposit_hook)
        if allowlist_address != ZERO_ADDRESS_STR:
            return VaultDepositPermission.whitelisted
        if hook_address != ZERO_ADDRESS_STR:
            return VaultDepositPermission.unknown
        return VaultDepositPermission.permissionless

    restriction_result = results.get("getDepositRestriction")
    pre_request_hook = results.get("getPreRequestDepositHook")
    post_execute_hook = results.get("getPostExecuteDepositRequestHook")
    if _has_word(restriction_result) and _has_word(pre_request_hook) and _has_word(post_execute_hook):
        restriction = _decode_uint(restriction_result)
        if restriction in {1, 2}:
            return VaultDepositPermission.whitelisted
        if restriction != 0:
            return VaultDepositPermission.unknown
        pre_hook_address = _decode_address(pre_request_hook)
        post_hook_address = _decode_address(post_execute_hook)
        if pre_hook_address != ZERO_ADDRESS_STR or post_hook_address != ZERO_ADDRESS_STR:
            return VaultDepositPermission.unknown
        return VaultDepositPermission.permissionless

    pre_mint_hook = results.get("getPreMintHook")
    if _has_word(pre_mint_hook):
        return VaultDepositPermission.whitelisted

    return VaultDepositPermission.unknown


def aggregate_onyx_vault_permission(handler_permissions: Iterable[VaultDepositPermission]) -> VaultDepositPermission:
    """Aggregate active route permissions for one Shares vault.

    A single public route makes the vault permissionless. Otherwise all active
    routes must require prior approval for the vault to be whitelisted. An
    empty handler set is permissionless for identity-policy purposes: no
    account has a privileged deposit route, although no handler can currently
    accept deposits either.

    :param handler_permissions: Classified active handler permissions.
    :return: Vault-level current deposit permission.
    """

    permissions = tuple(handler_permissions)
    if not permissions or VaultDepositPermission.permissionless in permissions:
        return VaultDepositPermission.permissionless
    if all(permission is VaultDepositPermission.whitelisted for permission in permissions):
        return VaultDepositPermission.whitelisted
    return VaultDepositPermission.unknown


def fetch_onyx_current_deposit_permissions(
    chain_id: int,
    web3factory: Web3Factory,
    active_handlers: Mapping[HexAddress, Iterable[HexAddress]],
    block_identifier: BlockIdentifier,
    max_workers: int,
) -> dict[HexAddress, VaultDepositPermission]:
    """Fetch current Onyx permission status using one batched Multicall pass.

    Every reviewed getter for every active handler is read at the same fixed
    block.  This makes the output internally consistent and avoids serial RPC
    calls from individual vault adapters.  The event-derived handler set must
    be fixed at the same block by the caller.

    Handler interfaces and their canonical source are documented in Enzyme's
    `Onyx issuance components
    <https://github.com/enzymefinance/protocol-onyx/tree/main/src/components/issuance>`__.

    :param chain_id:
        EVM chain containing the Shares vaults.
    :param web3factory:
        Reusable provider factory for Multicall workers.
    :param active_handlers:
        Shares-to-handler mapping reconstructed through the target block.
    :param block_identifier:
        Current metadata block shared with handler event discovery.
    :param max_workers:
        Number of threaded Multicall workers.
    :return:
        Lower-case Shares addresses mapped to current deposit permission.
    """

    calls = list(create_onyx_permission_calls(active_handlers))
    grouped_results: dict[tuple[str, str], dict[str, EncodedCallResult]] = defaultdict(dict)
    if calls:
        for result in read_multicall_chunked(
            chain_id=chain_id,
            web3factory=web3factory,
            calls=calls,
            block_identifier=block_identifier,
            max_workers=max_workers,
            chunk_size=120,
            progress_bar_desc=f"Reading Enzyme Onyx permission handlers on chain {chain_id}",
            timestamped_results=False,
            backend="threading",
        ):
            context = result.call.extra_data
            key = (context["shares_address"], context["handler_address"])
            grouped_results[key][result.call.func_name] = result

    output: dict[HexAddress, VaultDepositPermission] = {}
    for shares_address, handlers in active_handlers.items():
        handler_permissions = (
            classify_onyx_deposit_handler(
                grouped_results.get((shares_address.lower(), handler_address.lower()), {}),
            )
            for handler_address in handlers
        )
        output[HexAddress(shares_address.lower())] = aggregate_onyx_vault_permission(handler_permissions)
    return output
