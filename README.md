# Private PKI for IoT MQTT mTLS

Private Certificate Authority for securing MQTT communication between remote IoT sites and central EMQX broker using mutual TLS (mTLS).

## Table of Contents

- [Overview](#overview)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Server Setup (EMQX)](#server-setup-emqx)
- [Client Setup (Remote Sites)](#client-setup-remote-sites)
- [GUI Reference](#gui-reference)
- [Enrollment Script](#enrollment-script)
- [Troubleshooting](#troubleshooting)
- [Backup & Recovery](#backup--recovery)

## Overview

This system provides:

1. **Private Certificate Authority** - Issues and manages X.509 certificates
2. **Web GUI** - User-friendly interface for certificate operations
3. **mTLS for MQTT** - Mutual authentication between MQTT clients and broker

### What is mTLS?

Regular TLS only verifies the server's identity. **Mutual TLS (mTLS)** requires both parties to present certificates:

- **Server** presents its certificate to prove it's the real MQTT broker
- **Client** presents its certificate to prove it's an authorized device

This prevents unauthorized devices from connecting to your MQTT infrastructure.

## Quick Start

### 1. Start the PKI Server

```bash
cd app-pki
docker compose up -d
```

Wait ~30 seconds for step-ca to initialize, then access:
- **GUI**: http://localhost:8502
- **CA API**: https://localhost:9000

### 2. Issue Server Certificate (for EMQX)

1. Open GUI → **Issue Certificate**
2. Enter:
   - Name: `mqtt.ak-sg.com`
   - Type: `Server`
   - Validity: `365 days`
   - SANs: `mqtt.ak-sg.com, 10.0.80.50`
3. Click **Generate Certificate**
4. Download ZIP bundle

### 3. Issue Client Certificate (for each remote site)

1. Open GUI → **Issue Certificate**
2. Enter:
   - Name: `site-001`
   - Type: `Client`
   - Validity: `30 days`
3. Click **Generate Certificate**
4. Download ZIP bundle
5. Deploy to remote site

## Architecture

```
                    ┌─────────────────┐
                    │   PKI Server    │
                    │   (This App)    │
                    │                 │
                    │  GUI: 8502      │
                    │  CA:  9000      │
                    └────────┬────────┘
                             │
              Issues certificates to both
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│     EMQX        │ │   Site-001      │ │   Site-002      │
│  (Server Cert)  │ │ (Client Cert)   │ │ (Client Cert)   │
│                 │ │                 │ │                 │
│  mqtt.ak-sg.com │ │   Mosquitto     │ │   Mosquitto     │
│  Port: 8883     │ │   Bridge        │ │   Bridge        │
└────────┬────────┘ └────────┬────────┘ └────────┬────────┘
         │                   │                   │
         │          mTLS     │          mTLS     │
         │◄──────────────────┴───────────────────┘
         │
    ┌────▼────┐
    │ HAProxy │  (TCP passthrough, no TLS termination)
    │ :8883   │
    └─────────┘
```

## Server Setup (EMQX)

### Step 1: Generate Server Certificate

In the PKI GUI:

1. Go to **Issue Certificate**
2. Fill in:
   - **Site/Device Name**: `mqtt.ak-sg.com` (or your MQTT domain)
   - **Certificate Type**: `Server`
   - **Validity Period**: `365 days`
   - **Subject Alternative Names**: `mqtt.ak-sg.com, 10.0.80.50, emqx.local`
3. Click **Generate Certificate**
4. Download the ZIP bundle

### Step 2: Extract Certificates

```bash
# On EMQX server
unzip mqtt.ak-sg.com-certs.zip
sudo mkdir -p /etc/emqx/certs
sudo cp mqtt.ak-sg.com/ca.crt /etc/emqx/certs/
sudo cp mqtt.ak-sg.com/mqtt.ak-sg.com.crt /etc/emqx/certs/server.crt
sudo cp mqtt.ak-sg.com/mqtt.ak-sg.com.key /etc/emqx/certs/server.key
sudo chown -R emqx:emqx /etc/emqx/certs
sudo chmod 600 /etc/emqx/certs/server.key
```

### Step 3: Configure EMQX

Edit `/etc/emqx/emqx.conf` (or use EMQX Dashboard):

```hocon
listeners.ssl.default {
  bind = "0.0.0.0:8883"

  ssl_options {
    cacertfile = "/etc/emqx/certs/ca.crt"
    certfile = "/etc/emqx/certs/server.crt"
    keyfile = "/etc/emqx/certs/server.key"

    # Require client certificates (mTLS)
    verify = verify_peer
    fail_if_no_peer_cert = true

    # Use certificate CN as MQTT username
    peer_cert_as_username = cn
  }
}
```

### Step 4: Restart EMQX

```bash
sudo systemctl restart emqx
```

### Step 5: Verify Configuration

```bash
# Check EMQX is listening on 8883
sudo netstat -tlnp | grep 8883

# Check logs for SSL errors
sudo tail -f /var/log/emqx/emqx.log
```

## Client Setup (Remote Sites)

Each remote site runs Mosquitto as a bridge to EMQX.

### Option A: Manual Setup (Recommended for initial deployment)

#### Step 1: Generate Client Certificate

In the PKI GUI:

1. Go to **Issue Certificate**
2. Fill in:
   - **Site/Device Name**: `site-001` (unique name for this site)
   - **Certificate Type**: `Client`
   - **Validity Period**: `30 days` (or as needed)
3. Click **Generate Certificate**
4. Download the ZIP bundle

#### Step 2: Deploy to Remote Site

```bash
# Copy ZIP to remote site
scp site-001-certs.zip user@site-001:/tmp/

# On remote site
cd /tmp
unzip site-001-certs.zip
sudo mkdir -p /etc/mosquitto/certs
sudo cp site-001/ca.crt /etc/mosquitto/certs/
sudo cp site-001/site-001.crt /etc/mosquitto/certs/client.crt
sudo cp site-001/site-001.key /etc/mosquitto/certs/client.key
sudo chown -R mosquitto:mosquitto /etc/mosquitto/certs
sudo chmod 600 /etc/mosquitto/certs/client.key
```

#### Step 3: Configure Mosquitto Bridge

Edit `/etc/mosquitto/conf.d/bridge.conf`:

```conf
# Bridge to central EMQX broker
connection emqx-central
address mqtt.ak-sg.com:8883

# TLS Configuration
bridge_cafile /etc/mosquitto/certs/ca.crt
bridge_certfile /etc/mosquitto/certs/client.crt
bridge_keyfile /etc/mosquitto/certs/client.key

# Bridge settings
bridge_protocol_version mqttv311
bridge_insecure false
cleansession true
start_type automatic
try_private true

# Topic bridging - adjust as needed
topic # out 0
topic # in 0
```

#### Step 4: Restart Mosquitto

```bash
sudo systemctl restart mosquitto
```

#### Step 5: Verify Connection

```bash
# Check Mosquitto logs
sudo tail -f /var/log/mosquitto/mosquitto.log

# Should see: "Connected to broker"
```

### Option B: Automated Enrollment

For mass deployment, use the enrollment script:

```bash
# Get CA fingerprint from PKI GUI → CA Settings
CA_FINGERPRINT="abc123..."

# Run enrollment script
./enroll-site.sh site-001 https://pki-server:9000 $CA_FINGERPRINT
```

The script will:
1. Install step CLI if needed
2. Bootstrap CA trust
3. Request and download certificate
4. Output Mosquitto configuration

## GUI Reference

### Dashboard

- **CA Status**: Green = healthy, Red = problem with step-ca
- **Certificates**: Count of active certificates
- **Expiring Soon**: Certificates expiring within 7 days

### Issue Certificate

| Field | Description |
|-------|-------------|
| Site/Device Name | Becomes the certificate CN (Common Name). Use lowercase letters, numbers, hyphens. |
| Certificate Type | **Client** for devices, **Server** for EMQX |
| Validity Period | How long until certificate expires |
| SANs | Additional DNS names or IPs for server certificates |

### View Certificates

Shows all issued certificates with:
- **CN**: Certificate common name
- **Issued**: When certificate was created
- **Expires**: When certificate will expire
- **Status**: Active, Expired, or Error

### Revoke Certificate

Permanently revokes a certificate. The device will no longer be able to connect.

**When to revoke:**
- Device is decommissioned
- Private key was compromised
- Site is no longer authorized

### CA Settings

- **CA Fingerprint**: Needed for `step ca bootstrap` command
- **Download CA Certificate**: Get `ca.crt` for manual distribution
- **Bootstrap Command**: Command to run on remote sites to trust this CA

## Enrollment Script

The `scripts/enroll-site.sh` script automates certificate enrollment for remote sites.

### Usage

```bash
./enroll-site.sh <site-name> <step-ca-url> <ca-fingerprint> [provisioner-password]
```

### Example

```bash
./enroll-site.sh site-001 https://10.0.60.10:9000 abc123def456... mypassword
```

### Options

| Option | Description |
|--------|-------------|
| `-o, --output-dir` | Output directory (default: ./certs) |
| `-v, --validity` | Certificate validity (default: 720h = 30 days) |
| `-h, --help` | Show help |

### Output

Creates:
```
./certs/
├── ca.crt        # CA certificate
├── site-001.crt  # Client certificate
└── site-001.key  # Private key
```

## Troubleshooting

### CA not healthy (GUI shows red)

```bash
# Check step-ca logs
docker compose logs step-ca

# Restart step-ca
docker compose restart step-ca
```

### Certificate generation fails

1. Check step-ca is running: `docker compose ps`
2. Check provisioner password exists:
   ```bash
   docker compose exec step-ca cat /home/step/secrets/password
   ```
3. Check CA is initialized:
   ```bash
   docker compose exec step-ca ls -la /home/step/certs/
   ```

### EMQX rejects client connection

1. Verify CA certificate matches:
   ```bash
   # Compare fingerprints
   openssl x509 -in /etc/emqx/certs/ca.crt -fingerprint -noout
   openssl x509 -in /etc/mosquitto/certs/ca.crt -fingerprint -noout
   ```

2. Check certificate is not expired:
   ```bash
   openssl x509 -in /etc/mosquitto/certs/client.crt -dates -noout
   ```

3. Check EMQX logs:
   ```bash
   sudo tail -f /var/log/emqx/emqx.log | grep -i ssl
   ```

### Mosquitto bridge won't connect

1. Check Mosquitto logs:
   ```bash
   sudo tail -f /var/log/mosquitto/mosquitto.log
   ```

2. Test TLS connection manually:
   ```bash
   openssl s_client -connect mqtt.ak-sg.com:8883 \
     -CAfile /etc/mosquitto/certs/ca.crt \
     -cert /etc/mosquitto/certs/client.crt \
     -key /etc/mosquitto/certs/client.key
   ```

3. Verify certificate chain:
   ```bash
   openssl verify -CAfile /etc/mosquitto/certs/ca.crt \
     /etc/mosquitto/certs/client.crt
   ```

### HAProxy blocking connections

HAProxy must be in **TCP mode** (no TLS termination):

```haproxy
frontend mqtt_frontend
    bind *:8883
    mode tcp
    default_backend mqtt_backend

backend mqtt_backend
    mode tcp
    server emqx 10.0.80.50:8883 check
```

## Backup & Recovery

### Backup CA (Critical!)

The CA private key is the most important thing to backup. If lost, you must re-issue ALL certificates.

```bash
# Stop services
cd app-pki
docker compose stop step-ca

# Backup volume
docker run --rm \
  -v app-pki_step-ca-data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/step-ca-backup-$(date +%Y%m%d).tar.gz -C /data .

# Restart services
docker compose start step-ca
```

### Restore CA

```bash
# Stop services
docker compose stop step-ca

# Restore volume
docker run --rm \
  -v app-pki_step-ca-data:/data \
  -v $(pwd):/backup \
  alpine sh -c "rm -rf /data/* && tar xzf /backup/step-ca-backup-YYYYMMDD.tar.gz -C /data"

# Restart services
docker compose start step-ca
```

### Certificate Renewal

Certificates have limited validity. Before expiry:

1. Generate new certificate in GUI (same CN)
2. Download and deploy to device
3. Restart service on device

Future enhancement: ACME auto-renewal

## Security Best Practices

1. **Keep CA offline** - Only start PKI server when issuing certificates
2. **Short-lived client certs** - 30 days forces regular rotation
3. **Monitor expiry** - Check dashboard regularly for expiring certs
4. **Secure key distribution** - Use secure channels to transfer ZIP bundles
5. **Revoke promptly** - Revoke certificates for decommissioned devices
6. **Backup CA keys** - Store backups in secure, offline location
7. **Network isolation** - PKI GUI should only be accessible from management network

## Ports Reference

| Service | Port | Protocol | Purpose |
|---------|------|----------|---------|
| PKI GUI | 8502 | HTTP | Certificate management web interface |
| step-ca | 9000 | HTTPS | Certificate Authority API |
| HAProxy | 8883 | TCP | MQTT TLS passthrough |
| EMQX | 8883 | MQTTS | MQTT broker with mTLS |
