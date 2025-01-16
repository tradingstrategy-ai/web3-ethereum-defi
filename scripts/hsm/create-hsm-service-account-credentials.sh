#!/bin/bash
#
# Creates a new service account, with rights only to.
#
# 1. Prepare Google cloud project, key ring, upfront
# 2. This script takes these inputs as env variables
# 3. To run this script you need to have a gcloud init done
#
# Needs:
#
# - $GOOGLE_CLOUD_PROJECT
# - $GOOGLE_CLOUD_REGION
# - $KEY_RING
# - $KEY_NAME
# -
set -e
set -u

# Replace spaces and dashes with underscore,
# as role id only allows underscores
KEY_NAME_CLEAN="${KEY_NAME//[ -]/_}"

ROLE_ID="${KEY_NAME_CLEAN}_crypto_signer"
ROLE_TITLE="${KEY_RING}-{$KEY_NAME} crypto signer"
DESCRIPTION="${KEY_NAME} crypto signer"

echo "Creating role: ${ROLE_ID}"

# Create a custom role for KMS signing operations.
if ! gcloud iam roles list --project=$GOOGLE_CLOUD_PROJECT --format="value(name)"  | grep -q "$ROLE_ID"; then
    # Role does not exist, so we create it
    # gcloud does not like whitespaces
    gcloud iam roles create $ROLE_ID \
      --project=$GOOGLE_CLOUD_PROJECT \
      --title="$ROLE_TITLE" \
      --description="${DESCRIPTION}" \
      --permissions="cloudkms.cryptoKeyVersions.useToSign,cloudkms.cryptoKeyVersions.viewPublicKey,cloudkms.cryptoKeys.get,cloudkms.keyRings.get"
    echo "Role $ROLE_ID created successfully."
else
    echo "Role $ROLE_ID already exists. Skipping creation."
fi

# Generate service account email address from the keyring and thr project
SA_NAME_UNCLEAN="crypto_signer_${KEY_NAME_CLEAN}"
# Replace underscores with dashes.
# Yes, this is opposite as in the role naming above...
SA_NAME="${SA_NAME_UNCLEAN//_/-}"
SA_EMAIL="${SA_NAME}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"

echo "Creating service account: ${SA_NAME}"
if ! gcloud iam service-accounts list --format="value(email)" --filter="email:${SA_EMAIL}" | grep -q "${SA_EMAIL}"; then
  echo "Creating credentials for service accounts ${SA_EMAIL} for role ${ROLE_ID}"
  gcloud kms keys add-iam-policy-binding $KEY_NAME \
      --keyring=$KEY_RING \
      --location=$GOOGLE_CLOUD_REGION \
      --member="serviceAccount:${SA_EMAIL}" \
      --role="projects/$GOOGLE_CLOUD_PROJECT/roles/$ROLE_ID"
else
    echo "Service account ${SA_EMAIL} already exists. Skipping creation."
fi

# Create a temporary file that will be automatically deleted afer
# the script completed
temp_file=$(mktemp)

trap 'rm -f "$temp_file"' EXIT ERR

gcloud iam service-accounts keys create $temp_file \
    --iam-account="${SA_EMAIL}"

echo "Role id: ${ROLE_ID}"
echo "Role title: ${ROLE_TITLE}"
echo "Service account email: ${SA_EMAIL}"
echo "Your service credentials are:"

cat $temp_file