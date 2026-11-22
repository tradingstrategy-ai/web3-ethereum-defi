# Derive session keys for vault traders

A session key lets a trader sign orders for a Derive account. On Derive v3, the owner is an Ethereum account or multisig. The first deposit creates the account, and a multisig owner registers its session key onchain. The repository's LightAccount onboarding helpers apply to v2. See the [integration and version notes](../source/api/derive/README.md) and the [onboarding module](../../eth_defi/derive/onboarding.py).

## Signing in with a registered key

These screenshots show the v2 Lagoon vault setup. Use the session key supplied for the trading subaccount.

1. Import the session key into an Ethereum-compatible wallet. In the version of Rabby shown below, select **Create account** and then **Import private key**.

   ![Rabby account import menu](rabby.png)

2. Open the [Derive app](https://app.derive.xyz), select **Connect** and choose **Session key**.

   ![Derive connection dialogue with the session key option](derive-connect-session-key.png)

3. Sign the login message and check the subaccount and balance before trading. The example shows the vault balance under **Account 1**.

   ![Vault balance in the Derive trading subaccount](balance.png)

## How the v2 vault was funded

In the Lagoon prototype on Derive L2, the Safe controlled the vault's USDC and registered the session key. After deposit settlement, USDC could move to the Derive subaccount for trading. The executor read the subaccount value for the vault's NAV update. Redemptions required free cash. If margin was tied up in open positions, the trader had to release it before settlement.
