"""Enzyme-specific enums in their Solidity code."""

import enum


class IntegrationManagerActionId(enum.Enum):
    """Different actions a vault integration manager can perform."""

    CallOnIntegration = 0
    AddTrackedAssetsToVault = 1
    RemoveTrackedAssetsFromVault = 2
