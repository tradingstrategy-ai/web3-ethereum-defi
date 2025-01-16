"""Checks that HSM is correctly set up.

- Reads HSM environment variables and checks if we can produce a functional wallet

To run:

.. code-block:: shell

    python scripts/hsm/check-hsm-address.py

"""
import os
import json
from web3_google_hsm.config import BaseConfig

from eth_defi.gcloud_hsm_wallet import GCloudHSMWallet


def main():

    credentials = json.loads(os.environ["GCP_ADC_CREDENTIALS_STRING"])
    config = BaseConfig.from_env()
    print("Environment configured successfully!")
    print(f"Project ID: {config.project_id}")
    print(f"Region: {config.location_id}")
    print(f"Credentials client email {credentials['client_email']}")

    hsm_wallet = GCloudHSMWallet(config, credentials=credentials)

    # This will crash if your credentials have
    # access issues
    print(f"Google Cloud HSM wallet configured.")
    print(f"HSM account is: {hsm_wallet.address}")

if __name__ == '__main__':
    main()