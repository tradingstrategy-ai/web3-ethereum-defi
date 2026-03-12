#!/bin/bash
#
# Refresh AWS MFA session credentials for Hyperliquid S3 archive access
#
# Some AWS accounts enforce MFA authentication, which means long-term access keys
# alone are not sufficient to access requester-pays S3 buckets. This script
# obtains temporary session credentials via sts:GetSessionToken and either
# updates ~/.aws/credentials or prints export commands for your shell.
#
# Requirements:
#   - aws CLI installed and configured
#   - python3 installed (for credentials file update)
#   - MFA device registered on your IAM user
#
# Usage:
#   ./refresh-mfa-session.sh [options]
#
# Options:
#   -o, --otp CODE           6-digit OTP code from your MFA device (required)
#   -p, --profile NAME       AWS profile to update in ~/.aws/credentials (default: default)
#   -s, --serial ARN         MFA device serial number ARN. Auto-detected if not provided.
#   -d, --duration SECONDS   Session duration in seconds (default: 43200 = 12 hours)
#   -e, --export             Print export commands instead of updating credentials file
#   -h, --help               Show this help message
#
# Examples:
#   # Update credentials file for a named profile
#   ./refresh-mfa-session.sh --otp 123456 --profile tradingstrategy
#
#   # Auto-detect MFA device, use default profile
#   ./refresh-mfa-session.sh --otp 123456
#
#   # Print export commands for use in current shell (environment variable mode)
#   eval $(./refresh-mfa-session.sh --otp 123456 --export)
#
#   # Use a specific MFA device ARN and 8-hour session
#   ./refresh-mfa-session.sh --otp 123456 \
#     --serial arn:aws:iam::123456789012:mfa/my-device \
#     --duration 28800
#

set -euo pipefail

# Default values
OTP=""
PROFILE="default"
SERIAL=""
DURATION=43200
EXPORT_MODE=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -o|--otp)
            OTP="$2"
            shift 2
            ;;
        -p|--profile)
            PROFILE="$2"
            shift 2
            ;;
        -s|--serial)
            SERIAL="$2"
            shift 2
            ;;
        -d|--duration)
            DURATION="$2"
            shift 2
            ;;
        -e|--export)
            EXPORT_MODE=true
            shift
            ;;
        -h|--help)
            sed -n '3,45p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Error: Unknown option: $1" >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

# Validate required arguments
if [[ -z "$OTP" ]]; then
    echo "Error: --otp is required." >&2
    echo "Run with --help for usage." >&2
    exit 1
fi

if [[ ! "$OTP" =~ ^[0-9]{6}$ ]]; then
    echo "Error: OTP must be exactly 6 digits, got: $OTP" >&2
    exit 1
fi

# Check for required tools
for cmd in aws python3; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Error: $cmd is required but not installed." >&2
        exit 1
    fi
done

# Auto-detect MFA serial number if not provided
if [[ -z "$SERIAL" ]]; then
    echo "Auto-detecting MFA device for profile '$PROFILE'..." >&2
    SERIAL=$(aws iam list-mfa-devices \
        --profile "$PROFILE" \
        --query 'MFADevices[0].SerialNumber' \
        --output text 2>/dev/null || true)

    if [[ -z "$SERIAL" || "$SERIAL" == "None" ]]; then
        echo "Error: Could not auto-detect MFA device serial number." >&2
        echo "Provide it explicitly with --serial arn:aws:iam::ACCOUNT:mfa/DEVICE" >&2
        exit 1
    fi
    echo "Found MFA device: $SERIAL" >&2
fi

# Obtain temporary session credentials
echo "Requesting session token (duration: ${DURATION}s = $(( DURATION / 3600 ))h)..." >&2

RESULT=$(aws sts get-session-token \
    --profile "$PROFILE" \
    --serial-number "$SERIAL" \
    --token-code "$OTP" \
    --duration-seconds "$DURATION" \
    --output json 2>&1)

if ! echo "$RESULT" | python3 -c "import sys, json; json.load(sys.stdin)" &> /dev/null; then
    echo "Error: Failed to obtain session token:" >&2
    echo "$RESULT" >&2
    exit 1
fi

# Parse credentials from JSON response
ACCESS_KEY=$(echo "$RESULT" | python3 -c "import sys, json; print(json.load(sys.stdin)['Credentials']['AccessKeyId'])")
SECRET_KEY=$(echo "$RESULT" | python3 -c "import sys, json; print(json.load(sys.stdin)['Credentials']['SecretAccessKey'])")
SESSION_TOKEN=$(echo "$RESULT" | python3 -c "import sys, json; print(json.load(sys.stdin)['Credentials']['SessionToken'])")
EXPIRY=$(echo "$RESULT" | python3 -c "import sys, json; print(json.load(sys.stdin)['Credentials']['Expiration'])")

if [[ "$EXPORT_MODE" == true ]]; then
    # Print export commands for eval in current shell
    echo "export AWS_ACCESS_KEY_ID=$ACCESS_KEY"
    echo "export AWS_SECRET_ACCESS_KEY=$SECRET_KEY"
    echo "export AWS_SESSION_TOKEN=$SESSION_TOKEN"
    echo "# Session expires: $EXPIRY" >&2
else
    # Update ~/.aws/credentials using Python for reliable INI parsing
    python3 - <<PYEOF
import configparser
import os

creds_path = os.path.expanduser("~/.aws/credentials")
creds = configparser.ConfigParser()
creds.read(creds_path)

profile = "$PROFILE"
if profile not in creds:
    creds[profile] = {}

creds[profile]["aws_access_key_id"] = "$ACCESS_KEY"
creds[profile]["aws_secret_access_key"] = "$SECRET_KEY"
creds[profile]["aws_session_token"] = "$SESSION_TOKEN"

with open(creds_path, "w") as f:
    creds.write(f)

print(f"Updated profile '{profile}' in {creds_path}")
print(f"Session expires: $EXPIRY")
PYEOF

    # Verify the new credentials work
    echo "" >&2
    echo "Verifying credentials..." >&2
    IDENTITY=$(aws sts get-caller-identity --profile "$PROFILE" --output json 2>&1)
    if echo "$IDENTITY" | python3 -c "import sys, json; d=json.load(sys.stdin); print(f\"Authenticated as: {d['Arn']}\")" 2>/dev/null; then
        echo "Session credentials are valid." >&2
    else
        echo "Warning: Could not verify credentials — check output above." >&2
    fi
fi
