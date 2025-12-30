# PKI Management System - Technical Reference

Private PKI infrastructure for IoT MQTT mTLS authentication using step-ca.

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          PKI SERVER (This Application)                        │
│                                                                              │
│  ┌─────────────────────┐         ┌─────────────────────────────────────┐    │
│  │   step-ca (CA)      │         │      Streamlit GUI                  │    │
│  │   Port: 9000        │◄────────│      Port: 8502                     │    │
│  │                     │  REST   │                                     │    │
│  │  - Issues certs     │   +     │  - Dashboard                        │    │
│  │  - Revokes certs    │  CLI    │  - Issue Certificate                │    │
│  │  - Stores CA keys   │         │  - View Certificates                │    │
│  └─────────────────────┘         │  - Revoke Certificate               │    │
│           │                      │  - CA Settings                      │    │
│           │                      └─────────────────────────────────────┘    │
│           │                                                                  │
│  ┌────────▼────────┐                                                        │
│  │  Docker Volumes │                                                        │
│  │  - step-ca-data │  CA private key, config, secrets                       │
│  │  - ./certs      │  Generated certificates                                │
│  └─────────────────┘                                                        │
└──────────────────────────────────────────────────────────────────────────────┘
                    │
                    │ Issues certificates to
                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              EMQX BROKER                                      │
│                           (10.0.80.50:8883)                                   │
│                                                                              │
│  Server Certificate:                                                         │
│  - CN: mqtt.ak-sg.com                                                        │
│  - SANs: mqtt.ak-sg.com, 10.0.80.50                                         │
│  - Validates client certificates                                             │
│  - mTLS on port 8883                                                         │
└──────────────────────────────────────────────────────────────────────────────┘
                    ▲
                    │ mTLS connection (TCP passthrough)
                    │
┌──────────────────────────────────────────────────────────────────────────────┐
│                              HAProxy                                          │
│                           (10.0.60.2:8883)                                    │
│                                                                              │
│  - TCP mode (mode tcp)                                                       │
│  - No TLS termination                                                        │
│  - Passes encrypted traffic to EMQX                                          │
└──────────────────────────────────────────────────────────────────────────────┘
                    ▲
                    │ TLS connection with client cert
                    │
┌──────────────────────────────────────────────────────────────────────────────┐
│                          REMOTE SITES (Mosquitto)                             │
│                                                                              │
│  Site-001, Site-002, ... Site-N                                              │
│                                                                              │
│  Each site has:                                                              │
│  ├── ca.crt          (CA root certificate - to verify EMQX)                  │
│  ├── site-XXX.crt    (Client certificate - identity)                         │
│  └── site-XXX.key    (Private key - keep secure)                             │
│                                                                              │
│  Mosquitto bridge connects to HAProxy:8883 with mTLS                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

## How mTLS Works

### Certificate Chain of Trust

```
┌─────────────────────────────────┐
│     AK-SG IoT CA (Root)         │  ◄── Self-signed, stored in step-ca
│     Fingerprint: abc123...      │
└───────────────┬─────────────────┘
                │ Signs
        ┌───────┴───────┐
        ▼               ▼
┌───────────────┐ ┌───────────────┐
│ Server Cert   │ │ Client Cert   │
│ mqtt.ak-sg.com│ │ site-001      │
│ (for EMQX)    │ │ (for device)  │
└───────────────┘ └───────────────┘
```

### mTLS Handshake Flow

```
Remote Site (Mosquitto)                    EMQX Broker
        │                                       │
        │  1. ClientHello                       │
        │──────────────────────────────────────►│
        │                                       │
        │  2. ServerHello + Server Certificate  │
        │◄──────────────────────────────────────│
        │                                       │
        │  3. Client verifies server cert       │
        │     using ca.crt                      │
        │                                       │
        │  4. Client Certificate                │
        │──────────────────────────────────────►│
        │                                       │
        │  5. Server verifies client cert       │
        │     using ca.crt                      │
        │                                       │
        │  6. Encrypted MQTT session            │
        │◄─────────────────────────────────────►│
        │                                       │
```

## File Structure

```
app-pki/
├── docker-compose.yml          # Orchestrates step-ca + Streamlit GUI
├── streamlit/
│   ├── Dockerfile              # Python 3.11 + step CLI installation
│   ├── app.py                  # PKI management GUI (Streamlit)
│   └── requirements.txt        # Python dependencies
├── scripts/
│   └── enroll-site.sh          # Automated enrollment for remote sites
├── certs/                      # Generated certificates (bind mount)
├── CLAUDE.md                   # This file
└── README.md                   # User documentation
```

## Key Components

### 1. step-ca (Certificate Authority)

**Image:** `smallstep/step-ca:latest`

**Auto-initialization on first run:**
- Creates CA with name "AK-SG IoT CA"
- Generates root CA key pair
- Creates JWK provisioner "iot-devices"
- Saves password to `/home/step/secrets/password`

**Environment variables:**
```yaml
DOCKER_STEPCA_INIT_NAME: "AK-SG IoT CA"
DOCKER_STEPCA_INIT_DNS_NAMES: "localhost,step-ca"
DOCKER_STEPCA_INIT_REMOTE_MANAGEMENT: "true"
DOCKER_STEPCA_INIT_PROVISIONER_NAME: "iot-devices"
```

### 2. Streamlit GUI (pki-gui)

**How it interacts with step-ca:**

The GUI uses `subprocess.run()` to execute step CLI commands:

```python
def run_step_command(args: list) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["STEPPATH"] = "/home/step"
    cmd = ["step"] + args
    return subprocess.run(cmd, capture_output=True, text=True, env=env)
```

**Key operations:**

| Operation | step CLI Command |
|-----------|-----------------|
| Check CA health | `step ca health --ca-url URL --root CA_CERT` |
| Issue certificate | `step ca certificate CN cert.crt key.key --provisioner iot-devices` |
| Revoke certificate | `step ca revoke --cert cert.crt --key key.key` |
| Get fingerprint | `step certificate fingerprint root_ca.crt` |
| Inspect certificate | `step certificate inspect cert.crt` |

### 3. Certificate Storage

**Volumes:**
- `step-ca-data` - CA configuration, keys, secrets (Docker named volume)
- `./certs` - Generated certificates (bind mount, shared with GUI)

**Certificate directory structure:**
```
certs/
├── site-001/
│   ├── site-001.crt    # Client certificate
│   └── site-001.key    # Private key
├── site-002/
│   ├── site-002.crt
│   └── site-002.key
├── mqtt.ak-sg.com/
│   ├── mqtt.ak-sg.com.crt   # Server certificate
│   └── mqtt.ak-sg.com.key
└── site-001.revoked/        # Revoked certificates renamed
    ├── site-001.crt
    └── site-001.key
```

## GUI Pages

### Dashboard
- Calls `step ca health` to check CA status
- Counts certificates in `./certs` directory
- Uses `cryptography` library to parse cert expiry dates
- Shows certificates expiring within 7 days

### Issue Certificate
- Form collects: CN, type (Client/Server), validity, SANs
- Calls `step ca certificate` with provisioner password
- Saves cert + key to `./certs/{CN}/`
- Creates ZIP bundle with ca.crt + cert + key + README

### View Certificates
- Scans `./certs` directory for certificate folders
- Parses each `.crt` file using `cryptography.x509`
- Displays table with CN, issued date, expiry, status

### Revoke Certificate
- Calls `step ca revoke` with cert and key
- Renames directory to `{CN}.revoked` to mark as revoked

### CA Settings
- Shows CA fingerprint (needed for bootstrap)
- Provides CA certificate download
- Shows bootstrap command for remote sites

## Provisioner Authentication

step-ca uses provisioners to authorize certificate requests.

**Provisioner:** `iot-devices` (JWK type)
**Password file:** `/home/step/secrets/password` (auto-generated)

The GUI reads this password file to authenticate certificate operations:
```python
PROVISIONER_PASSWORD_FILE = "/home/step/secrets/password"

with open(PROVISIONER_PASSWORD_FILE, "r") as f:
    password = f.read().strip()
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `STEP_CA_URL` | `https://step-ca:9000` | step-ca server URL (internal Docker network) |
| `STEP_CA_ROOT` | `/home/step/certs/root_ca.crt` | Path to CA root certificate |

## Development Commands

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs -f
docker compose logs -f step-ca
docker compose logs -f pki-gui

# Rebuild GUI after code changes
docker compose build pki-gui && docker compose up -d pki-gui

# Restart step-ca (if stuck)
docker compose restart step-ca

# Stop all services
docker compose down

# Stop and remove volumes (DESTROYS CA!)
docker compose down -v
```

## Debugging

### Check step-ca initialization
```bash
docker compose exec step-ca ls -la /home/step/
docker compose exec step-ca cat /home/step/config/ca.json
```

### Test step CLI from GUI container
```bash
docker compose exec pki-gui step ca health --ca-url https://step-ca:9000 --root /home/step/certs/root_ca.crt
```

### View provisioner password
```bash
docker compose exec step-ca cat /home/step/secrets/password
```

### Inspect a certificate
```bash
docker compose exec pki-gui step certificate inspect /app/certs/site-001/site-001.crt
```

## Security Considerations

| Concern | Mitigation |
|---------|------------|
| CA private key exposure | Stored in Docker volume, never leaves step-ca container |
| Provisioner password | Read from file, never hardcoded |
| GUI access | Internal network only (no auth by default) |
| Private keys in bundles | User downloads once, keys stay on remote site |
| Revoked certs | Renamed but not deleted (for audit trail) |

## Future Enhancements

1. **GUI Authentication** - Add `PKI_PASSWORD` env var for password protection
2. **ACME Protocol** - Enable automatic certificate renewal
3. **CRL/OCSP** - Certificate revocation status checking
4. **Audit Logging** - Track all certificate operations
5. **Expiry Alerts** - Email notifications for expiring certs

## Related Systems

| System | IP | Port | Role |
|--------|-----|------|------|
| PKI Server | TBD | 8502, 9000 | Certificate Authority + GUI |
| HAProxy | 10.0.60.2 | 8883 | TCP passthrough to EMQX |
| EMQX | 10.0.80.50 | 8883 | MQTT broker with mTLS |
| Remote Sites | Various | - | Mosquitto bridges with client certs |
