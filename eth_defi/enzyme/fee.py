"""User-facing Enzyme fee presentation helpers.

Enzyme adapters expose the fee rate paid by an investor, rather than the
protocol's internal settlement mechanics or recipient accounting.
"""

from eth_defi.types import Percent


def combine_user_facing_management_fee(manager_fee: Percent | None, protocol_fee: Percent | None) -> Percent | None:
    """Combine a vault-manager and an additional protocol management fee.

    The vault scanner's management-fee field is an investor-facing rate. It
    must include every current recurring management charge paid on top of the
    vault manager's fee, regardless of whether the protocol settles that
    charge through minting, asset transfers, or internal debt accounting.

    :param manager_fee:
        Current vault-manager rate, or ``None`` when unconfigured.
    :param protocol_fee:
        Current additional protocol rate, or ``None`` when the reviewed
        protocol surface has no separately configured rate.
    :return:
        The combined management rate, or ``None`` when both components are
        unavailable.
    """

    if manager_fee is None and protocol_fee is None:
        return None

    # Export the combined investor-facing rate. The separate protocol field is
    # a breakdown, not a second charge to add to this result.
    return Percent(float((manager_fee or 0) + (protocol_fee or 0)))
