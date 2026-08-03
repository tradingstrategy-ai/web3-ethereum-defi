"""Shared native perpetual DEX vault metadata helpers."""

from dataclasses import dataclass

from eth_defi.vault.deposit_redeem import VaultDepositPermission

#: Qualification appended when the compatibility status represents closed
#: public deposits rather than an account allow-list.
PERP_VAULT_PUBLIC_DEPOSITS_CLOSED_NOTE = "Native perp DEX compatibility status: public deposits are unavailable; this does not imply an approved-account deposit route"


@dataclass(slots=True, frozen=True)
class PerpVaultDepositAccess:
    """Normalised public-deposit metadata for a native perp DEX vault.

    Native perp DEX APIs expose public availability or lifecycle status rather
    than a KYC or account allow-list mechanism. The shared vault export still
    expects :class:`~eth_defi.vault.deposit_redeem.VaultDepositPermission`.
    Closed public participation therefore uses the compatibility value
    :attr:`~eth_defi.vault.deposit_redeem.VaultDepositPermission.whitelisted`
    with a mandatory qualification note.
    """

    #: Shared export value.
    permission: VaultDepositPermission

    #: Qualification exported as ``whitelist.notes``.
    whitelist_notes: str | None

    #: Source-backed public-deposit availability detail.
    deposit_closed_reason: str | None


def classify_perp_vault_deposit_access(
    *,
    public_deposits_open: bool | None,
    closed_reason: str | None = None,
) -> PerpVaultDepositAccess:
    """Map native perp DEX public availability to the shared export contract.

    ``whitelisted`` is a compatibility value here: it means the adapter's
    documented source-status mapping determines that public participation is
    unavailable. It does not assert that selected accounts can deposit.
    ``closed_reason`` is required for that classification so the exported
    qualification cannot be silently omitted.

    :param public_deposits_open:
        ``True`` when source state proves public participation is open,
        ``False`` when a documented source-status mapping proves it is
        unavailable, or ``None`` when the scanner cannot classify the state.
    :param closed_reason:
        Source-specific explanation used when public deposits are unavailable.
    :return:
        Normalised permission value and optional qualification note.
    :raises ValueError:
        If closed public deposits have no explanation.
    """
    if public_deposits_open is True:
        return PerpVaultDepositAccess(VaultDepositPermission.permissionless, None, closed_reason)
    if public_deposits_open is None:
        return PerpVaultDepositAccess(VaultDepositPermission.unknown, None, closed_reason)
    if not closed_reason:
        message = "closed_reason is required when public deposits are unavailable"
        raise ValueError(message)

    reason = closed_reason.rstrip(".")
    notes = f"{reason}. {PERP_VAULT_PUBLIC_DEPOSITS_CLOSED_NOTE}."
    return PerpVaultDepositAccess(VaultDepositPermission.whitelisted, notes, closed_reason)
