import enum


class IntegrationManagerActionId(enum.Enum):
    CallOnIntegration = 0
    AddTrackedAssetsToVault = 1
    RemoveTrackedAssetsFromVault = 2
