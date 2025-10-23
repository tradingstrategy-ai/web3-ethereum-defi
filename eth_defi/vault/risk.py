"""Vaukt risk classification."""

import enum


class VaultRisk(enum.Enum):
    """Vault risk profile enum.

    This risk profile classification is about the technical risk of the vault.
    Outside technical risk, you have market risk, volatility risk and such risk factors which can be modelled separately using finance best practices.

    - Used to classify vaults by their risk profile
    - How this vault risk compares to other vaults (All vaults are high risk compared to the traditional finance)
    - Having a point of time technical audit does not meaningfully lower the risk, because all systems should be evaluated as a whole
      and have continuous transparency and open source development to be considered low risk.
    """

    #: Fully open sourced and audited vaults with transparent strategies,
    #: all data easily readable onchain. Vouched and promoted by reputable people in the space.
    #: Vaults have robust developer documentation and explanation of APIs, mechanisms and risks.
    #:
    #: E.g. Euler, Morpho, Enzyme, vaults.
    low = 1

    #: The vault is built on the good known protocol like Lagoon and Enzyme, but
    #: includes manual execution, custom smart contracts and permissions by the curator that add to the risk.
    #:
    #: E.g. Lagoon.
    #:
    lowish = 5

    #: Has Github repository with strategy code, you can follow the development transparently,
    #: but no transparency on the dev team, no meaningful communications.
    medium = 10

    #: No public Github repository to follow the development, but still publishes full source and providers integrator documentation and transparency
    #:
    #:
    high = 20

    #: Only partial source code published.
    #:
    #: No Github repository, not all contracts have been verified.
    #:
    #: E.g. Velvet Capital, Umami.
    #:
    extra_high = 40

    #: Not audited, degen, not following blockchain development best practices.
    #:
    #: E.g. Peapods.
    #:
    dangerous = 50

    def get_risk_level_name(self) -> str:
        return self.name.replace("_", " ").title()
