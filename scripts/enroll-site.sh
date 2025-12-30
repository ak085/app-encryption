#!/bin/bash
#
# Site Enrollment Script for PKI
# Requests a client certificate from step-ca for MQTT mTLS authentication
#
# Usage: ./enroll-site.sh <site-name> <step-ca-url> <ca-fingerprint> [provisioner-password]
#
# Example:
#   ./enroll-site.sh site-001 https://pki.example.com:9000 abc123... mypassword
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
PROVISIONER_NAME="iot-devices"
CERT_VALIDITY="720h"  # 30 days
OUTPUT_DIR="./certs"

# Functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

show_usage() {
    cat << EOF
Usage: $0 <site-name> <step-ca-url> <ca-fingerprint> [provisioner-password]

Arguments:
  site-name            Name of the site (becomes certificate CN)
  step-ca-url          URL of step-ca server (e.g., https://pki.example.com:9000)
  ca-fingerprint       Fingerprint of the CA root certificate
  provisioner-password Password for the iot-devices provisioner (optional, will prompt if not provided)

Options:
  -o, --output-dir     Output directory for certificates (default: ./certs)
  -v, --validity       Certificate validity (default: 720h = 30 days)
  -h, --help           Show this help message

Example:
  $0 site-001 https://10.0.60.10:9000 abc123def456...

Output:
  Creates directory with:
  - ca.crt        : CA root certificate
  - <site>.crt    : Client certificate
  - <site>.key    : Private key
EOF
}

check_step_cli() {
    if command -v step &> /dev/null; then
        log_info "step CLI found: $(step version 2>&1 | head -1)"
        return 0
    else
        return 1
    fi
}

install_step_cli() {
    log_info "Installing step CLI..."

    # Detect OS and architecture
    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    ARCH=$(uname -m)

    case $ARCH in
        x86_64) ARCH="amd64" ;;
        aarch64) ARCH="arm64" ;;
        armv7l) ARCH="armv7" ;;
        *) log_error "Unsupported architecture: $ARCH"; exit 1 ;;
    esac

    STEP_VERSION="0.27.2"
    DOWNLOAD_URL="https://github.com/smallstep/cli/releases/download/v${STEP_VERSION}/step_${OS}_${STEP_VERSION}_${ARCH}.tar.gz"
    # Alternative URL pattern (some releases use this format)
    ALT_DOWNLOAD_URL="https://dl.smallstep.com/gh-release/cli/gh-release-header/v${STEP_VERSION}/step_${OS}_${STEP_VERSION}_${ARCH}.tar.gz"

    log_info "Downloading from: $DOWNLOAD_URL"

    # Download and extract
    TEMP_DIR=$(mktemp -d)
    curl -fsSL "$DOWNLOAD_URL" | tar -xz -C "$TEMP_DIR"

    # Install to /usr/local/bin if we have permission, otherwise ~/bin
    if [ -w /usr/local/bin ]; then
        sudo mv "${TEMP_DIR}/step_${STEP_VERSION}/bin/step" /usr/local/bin/
        log_info "Installed step to /usr/local/bin/step"
    else
        mkdir -p ~/bin
        mv "${TEMP_DIR}/step_${STEP_VERSION}/bin/step" ~/bin/
        export PATH="$HOME/bin:$PATH"
        log_info "Installed step to ~/bin/step"
        log_warn "Add ~/bin to your PATH: export PATH=\"\$HOME/bin:\$PATH\""
    fi

    rm -rf "$TEMP_DIR"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -o|--output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -v|--validity)
            CERT_VALIDITY="$2"
            shift 2
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            break
            ;;
    esac
done

# Required positional arguments
SITE_NAME="$1"
STEP_CA_URL="$2"
CA_FINGERPRINT="$3"
PROVISIONER_PASSWORD="$4"

# Validate arguments
if [ -z "$SITE_NAME" ] || [ -z "$STEP_CA_URL" ] || [ -z "$CA_FINGERPRINT" ]; then
    log_error "Missing required arguments"
    show_usage
    exit 1
fi

# Validate site name
if ! [[ "$SITE_NAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    log_error "Site name should only contain letters, numbers, hyphens, and underscores"
    exit 1
fi

# Check/install step CLI
if ! check_step_cli; then
    log_warn "step CLI not found"
    read -p "Install step CLI? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        install_step_cli
    else
        log_error "step CLI is required. Install manually from https://smallstep.com/docs/step-cli/installation"
        exit 1
    fi
fi

# Get provisioner password if not provided
if [ -z "$PROVISIONER_PASSWORD" ]; then
    read -sp "Enter provisioner password for '$PROVISIONER_NAME': " PROVISIONER_PASSWORD
    echo
fi

# Create output directory
mkdir -p "$OUTPUT_DIR"
log_info "Output directory: $OUTPUT_DIR"

# Bootstrap CA trust
log_info "Bootstrapping CA trust..."
step ca bootstrap \
    --ca-url "$STEP_CA_URL" \
    --fingerprint "$CA_FINGERPRINT" \
    --force

# Copy CA certificate to output
CA_CERT_PATH="$OUTPUT_DIR/ca.crt"
cp "$(step path)/certs/root_ca.crt" "$CA_CERT_PATH"
log_info "CA certificate saved to: $CA_CERT_PATH"

# Request certificate
CERT_PATH="$OUTPUT_DIR/${SITE_NAME}.crt"
KEY_PATH="$OUTPUT_DIR/${SITE_NAME}.key"

log_info "Requesting certificate for: $SITE_NAME"

# Create password file temporarily
PASS_FILE=$(mktemp)
echo "$PROVISIONER_PASSWORD" > "$PASS_FILE"
chmod 600 "$PASS_FILE"

step ca certificate \
    "$SITE_NAME" \
    "$CERT_PATH" \
    "$KEY_PATH" \
    --provisioner "$PROVISIONER_NAME" \
    --provisioner-password-file "$PASS_FILE" \
    --not-after "$CERT_VALIDITY" \
    --force

# Clean up password file
rm -f "$PASS_FILE"

# Set secure permissions
chmod 644 "$CERT_PATH"
chmod 600 "$KEY_PATH"

log_info "Certificate saved to: $CERT_PATH"
log_info "Private key saved to: $KEY_PATH"

# Show certificate info
echo ""
log_info "Certificate details:"
step certificate inspect "$CERT_PATH" --short

# Show Mosquitto bridge configuration hint
echo ""
log_info "Mosquitto bridge configuration:"
cat << EOF

# Add to mosquitto.conf for bridge to EMQX:
connection emqx-bridge
address mqtt.ak-sg.com:8883
bridge_cafile ${OUTPUT_DIR}/ca.crt
bridge_certfile ${OUTPUT_DIR}/${SITE_NAME}.crt
bridge_keyfile ${OUTPUT_DIR}/${SITE_NAME}.key
topic # out 0

EOF

log_info "Enrollment complete!"
