"""
PKI Management GUI for step-ca
Manages certificates for IoT MQTT mTLS authentication
"""

import streamlit as st
import subprocess
import os
import json
import io
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.backends import default_backend

# Configuration
STEP_CA_URL = os.environ.get("STEP_CA_URL", "https://step-ca:9000")
STEP_CA_ROOT = os.environ.get("STEP_CA_ROOT", "/home/step/certs/root_ca.crt")
# CA bundle includes intermediate + root for mTLS client verification
CA_BUNDLE = "/home/step/certs/ca-bundle.crt"
CERTS_DIR = Path("/app/certs")
PROVISIONER_PASSWORD_FILE = "/home/step/secrets/password"

# Ensure certs directory exists
CERTS_DIR.mkdir(parents=True, exist_ok=True)

st.set_page_config(
    page_title="PKI Management",
    page_icon="🔐",
    layout="wide"
)

st.title("PKI Management Console")


def run_step_command(args: list, capture_output: bool = True) -> subprocess.CompletedProcess:
    """Execute a step CLI command."""
    env = os.environ.copy()
    env["STEPPATH"] = "/home/step"

    cmd = ["step"] + args
    result = subprocess.run(
        cmd,
        capture_output=capture_output,
        text=True,
        env=env
    )
    return result


def get_ca_health() -> dict:
    """Check CA health status."""
    try:
        result = run_step_command([
            "ca", "health",
            "--ca-url", STEP_CA_URL,
            "--root", STEP_CA_ROOT
        ])
        if result.returncode == 0:
            return {"status": "healthy", "message": "CA is running"}
        else:
            return {"status": "unhealthy", "message": result.stderr}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_ca_fingerprint() -> str:
    """Get CA root certificate fingerprint."""
    try:
        result = run_step_command([
            "certificate", "fingerprint",
            STEP_CA_ROOT
        ])
        if result.returncode == 0:
            return result.stdout.strip()
        return "Unable to get fingerprint"
    except Exception as e:
        return f"Error: {e}"


def get_provisioner_password() -> str:
    """Read provisioner password from secrets file."""
    try:
        with open(PROVISIONER_PASSWORD_FILE, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def issue_certificate(
    common_name: str,
    cert_type: str,
    validity_days: int,
    sans: list = None
) -> tuple[bool, str, dict]:
    """
    Issue a new certificate.
    Returns: (success, message, files_dict)
    """
    cert_dir = CERTS_DIR / common_name
    cert_dir.mkdir(parents=True, exist_ok=True)

    cert_file = cert_dir / f"{common_name}.crt"
    key_file = cert_dir / f"{common_name}.key"

    # Build command
    cmd = [
        "ca", "certificate",
        common_name,
        str(cert_file),
        str(key_file),
        "--ca-url", STEP_CA_URL,
        "--root", STEP_CA_ROOT,
        "--not-after", f"{validity_days * 24}h",
        "--provisioner", "iot-devices",
        "--provisioner-password-file", PROVISIONER_PASSWORD_FILE,
        "--force"
    ]

    # Add SANs for server certificates
    if sans:
        for san in sans:
            if san.strip():
                cmd.extend(["--san", san.strip()])

    result = run_step_command(cmd)

    if result.returncode == 0:
        # Read generated files
        files = {}
        try:
            with open(cert_file, "r") as f:
                files["cert"] = f.read()
            with open(key_file, "r") as f:
                files["key"] = f.read()
            with open(CA_BUNDLE, "r") as f:
                files["ca"] = f.read()  # Includes intermediate + root for mTLS
            return True, "Certificate issued successfully", files
        except Exception as e:
            return False, f"Error reading files: {e}", {}
    else:
        return False, f"Error: {result.stderr}", {}


def list_certificates() -> list:
    """List all issued certificates from certs directory."""
    certs = []

    for cert_dir in CERTS_DIR.iterdir():
        if cert_dir.is_dir():
            cert_file = cert_dir / f"{cert_dir.name}.crt"
            if cert_file.exists():
                try:
                    with open(cert_file, "rb") as f:
                        cert_data = f.read()
                    cert = x509.load_pem_x509_certificate(cert_data, default_backend())

                    certs.append({
                        "cn": cert_dir.name,
                        "issued": cert.not_valid_before_utc.strftime("%Y-%m-%d %H:%M"),
                        "expires": cert.not_valid_after_utc.strftime("%Y-%m-%d %H:%M"),
                        "serial": str(cert.serial_number),  # Decimal format for step CLI
                        "status": "Active" if cert.not_valid_after_utc > datetime.now(cert.not_valid_after_utc.tzinfo) else "Expired"
                    })
                except Exception as e:
                    certs.append({
                        "cn": cert_dir.name,
                        "issued": "Unknown",
                        "expires": "Unknown",
                        "serial": "Unknown",
                        "status": f"Error: {e}"
                    })

    return sorted(certs, key=lambda x: x["cn"])


def revoke_certificate(common_name: str, serial: str) -> tuple[bool, str]:
    """Revoke a certificate by serial number using token-based auth."""
    cert_file = CERTS_DIR / common_name / f"{common_name}.crt"

    if not cert_file.exists():
        return False, "Certificate file not found"

    # Step 1: Generate a revocation token using provisioner
    # Note: subject (serial) must come before flags
    token_cmd = [
        "ca", "token",
        serial,
        "--revoke",
        "--ca-url", STEP_CA_URL,
        "--root", STEP_CA_ROOT,
        "--provisioner", "iot-devices",
        "--provisioner-password-file", PROVISIONER_PASSWORD_FILE
    ]

    token_result = run_step_command(token_cmd)
    if token_result.returncode != 0:
        return False, f"Failed to generate revocation token: {token_result.stderr}"

    token = token_result.stdout.strip()

    # Step 2: Revoke using the token
    revoke_cmd = [
        "ca", "revoke",
        serial,
        "--ca-url", STEP_CA_URL,
        "--root", STEP_CA_ROOT,
        "--token", token
    ]

    result = run_step_command(revoke_cmd)

    # Check if already revoked (still counts as success for cleanup)
    already_revoked = "already revoked" in result.stderr.lower()

    if result.returncode == 0 or already_revoked:
        # Mark as revoked by renaming directory
        revoked_dir = CERTS_DIR / f"{common_name}.revoked"
        # Handle case where .revoked already exists
        if revoked_dir.exists():
            import shutil
            shutil.rmtree(revoked_dir)
        try:
            (CERTS_DIR / common_name).rename(revoked_dir)
        except Exception as e:
            pass  # Directory rename failed but cert is revoked
        if already_revoked:
            return True, "Certificate was already revoked. Cleaned up local files."
        return True, "Certificate revoked successfully"
    else:
        return False, f"Error: {result.stderr}"


def create_cert_bundle(common_name: str, files: dict) -> bytes:
    """Create a ZIP bundle with all certificate files."""
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{common_name}/ca.crt", files["ca"])
        zf.writestr(f"{common_name}/{common_name}.crt", files["cert"])
        zf.writestr(f"{common_name}/{common_name}.key", files["key"])

        # Add a README for the bundle
        readme = f"""Certificate Bundle for {common_name}
========================================

Files included:
- ca.crt          : CA root certificate (install on all devices)
- {common_name}.crt : Client/Server certificate
- {common_name}.key : Private key (keep secure!)

For Mosquitto bridge configuration:
  bridge_cafile /path/to/ca.crt
  bridge_certfile /path/to/{common_name}.crt
  bridge_keyfile /path/to/{common_name}.key

Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""
        zf.writestr(f"{common_name}/README.txt", readme)

    buffer.seek(0)
    return buffer.getvalue()


# Sidebar navigation
page = st.sidebar.radio(
    "Navigation",
    ["Dashboard", "Issue Certificate", "View Certificates", "Revoke Certificate", "CA Settings"]
)

# Dashboard page
if page == "Dashboard":
    st.header("Dashboard")

    col1, col2, col3 = st.columns(3)

    # CA Health
    with col1:
        st.subheader("CA Status")
        health = get_ca_health()
        if health["status"] == "healthy":
            st.success("Healthy")
        else:
            st.error(f"Unhealthy: {health['message']}")

    # Certificate count
    with col2:
        st.subheader("Certificates")
        certs = list_certificates()
        active = len([c for c in certs if c["status"] == "Active"])
        st.metric("Active", active)

    # Expiring soon
    with col3:
        st.subheader("Expiring Soon")
        expiring = 0
        for cert in certs:
            if cert["status"] == "Active" and cert["expires"] != "Unknown":
                try:
                    exp_date = datetime.strptime(cert["expires"], "%Y-%m-%d %H:%M")
                    if exp_date < datetime.now() + timedelta(days=7):
                        expiring += 1
                except:
                    pass
        if expiring > 0:
            st.warning(f"{expiring} expiring in 7 days")
        else:
            st.success("None")

    # Recent certificates
    st.subheader("Recent Certificates")
    if certs:
        st.dataframe(
            certs[:10],
            width='stretch',
            hide_index=True
        )
    else:
        st.info("No certificates issued yet")

# Issue Certificate page
elif page == "Issue Certificate":
    st.header("Issue New Certificate")

    # Initialize session state for certificate data
    if "cert_bundle" not in st.session_state:
        st.session_state.cert_bundle = None
        st.session_state.cert_name = None
        st.session_state.cert_files = None

    with st.form("issue_cert_form"):
        common_name = st.text_input(
            "Site/Device Name (Common Name)",
            placeholder="site-001",
            help="This will be the CN of the certificate"
        )

        cert_type = st.selectbox(
            "Certificate Type",
            ["Client", "Server"],
            help="Client for MQTT clients, Server for EMQX/brokers"
        )

        validity = st.selectbox(
            "Validity Period",
            [30, 90, 180, 365],
            format_func=lambda x: f"{x} days",
            help="Certificate validity in days"
        )

        sans_input = st.text_input(
            "Subject Alternative Names (Server only)",
            placeholder="mqtt.example.com, 10.0.0.1",
            help="Comma-separated list of DNS names or IPs. Ignored for Client certificates."
        )

        submitted = st.form_submit_button("Generate Certificate", type="primary")

        if submitted:
            if not common_name:
                st.error("Please enter a site/device name")
            elif not common_name.replace("-", "").replace("_", "").replace(".", "").isalnum():
                st.error("Name should only contain letters, numbers, hyphens, underscores, and dots")
            else:
                with st.spinner("Generating certificate..."):
                    sans = []
                    if cert_type == "Server" and sans_input:
                        sans = [s.strip() for s in sans_input.split(",")]

                    success, message, files = issue_certificate(
                        common_name, cert_type, validity, sans
                    )

                    if success:
                        st.success(message)
                        # Store in session state for download button outside form
                        st.session_state.cert_bundle = create_cert_bundle(common_name, files)
                        st.session_state.cert_name = common_name
                        st.session_state.cert_files = files
                    else:
                        st.error(message)
                        st.session_state.cert_bundle = None

    # Download button outside the form
    if st.session_state.cert_bundle is not None:
        st.divider()
        st.subheader("Download Certificate")

        st.download_button(
            label=f"Download {st.session_state.cert_name} Bundle (ZIP)",
            data=st.session_state.cert_bundle,
            file_name=f"{st.session_state.cert_name}-certs.zip",
            mime="application/zip",
            type="primary"
        )

        # Show individual files
        with st.expander("View Certificate Files"):
            st.code(st.session_state.cert_files["cert"], language="text")
            st.caption("Certificate")

            st.code(st.session_state.cert_files["ca"], language="text")
            st.caption("CA Certificate")

        if st.button("Clear"):
            st.session_state.cert_bundle = None
            st.session_state.cert_name = None
            st.session_state.cert_files = None
            st.rerun()

# View Certificates page
elif page == "View Certificates":
    st.header("Issued Certificates")

    certs = list_certificates()

    if certs:
        # Filter options
        status_filter = st.selectbox(
            "Filter by Status",
            ["All", "Active", "Expired"]
        )

        if status_filter != "All":
            certs = [c for c in certs if c["status"] == status_filter]

        st.dataframe(
            certs,
            width='stretch',
            hide_index=True,
            column_config={
                "cn": st.column_config.TextColumn("Common Name", width="medium"),
                "issued": st.column_config.TextColumn("Issued", width="medium"),
                "expires": st.column_config.TextColumn("Expires", width="medium"),
                "serial": st.column_config.TextColumn("Serial", width="large"),
                "status": st.column_config.TextColumn("Status", width="small")
            }
        )

        st.caption(f"Total: {len(certs)} certificate(s)")
    else:
        st.info("No certificates issued yet")

# Revoke Certificate page
elif page == "Revoke Certificate":
    st.header("Revoke Certificate")

    st.warning("Revoking a certificate is irreversible. The device will no longer be able to connect.")

    certs = list_certificates()
    active_certs = [c for c in certs if c["status"] == "Active"]

    if active_certs:
        cert_options = {c["cn"]: c for c in active_certs}

        selected_cn = st.selectbox(
            "Select Certificate to Revoke",
            options=list(cert_options.keys())
        )

        if selected_cn:
            cert = cert_options[selected_cn]
            st.info(f"Serial: {cert['serial']}\nExpires: {cert['expires']}")

            confirm = st.checkbox("I understand this action cannot be undone")

            if st.button("Revoke Certificate", type="primary", disabled=not confirm):
                with st.spinner("Revoking certificate..."):
                    success, message = revoke_certificate(selected_cn, cert["serial"])
                    if success:
                        st.success(message)
                        st.rerun()
                    else:
                        st.error(message)
    else:
        st.info("No active certificates to revoke")

# CA Settings page
elif page == "CA Settings":
    st.header("CA Settings")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("CA Information")

        # Fingerprint
        fingerprint = get_ca_fingerprint()
        st.text_input("CA Fingerprint", value=fingerprint, disabled=True)

        st.text_input("CA URL", value=STEP_CA_URL, disabled=True)

        st.text_input("Provisioner", value="iot-devices", disabled=True)

    with col2:
        st.subheader("Download CA Certificate Bundle")

        try:
            with open(CA_BUNDLE, "r") as f:
                ca_cert = f.read()

            st.download_button(
                label="Download CA Bundle (Intermediate + Root)",
                data=ca_cert,
                file_name="ca.crt",
                mime="application/x-pem-file",
                type="primary"
            )

            st.caption("This bundle includes Intermediate CA + Root CA. Required for mTLS client verification.")

            with st.expander("View CA Certificate"):
                st.code(ca_cert, language="text")
        except FileNotFoundError:
            st.error("CA certificate not found. Is step-ca running?")

    st.divider()

    st.subheader("Bootstrap Command")
    st.caption("Run this on remote sites to trust this CA:")

    bootstrap_cmd = f"step ca bootstrap --ca-url {STEP_CA_URL} --fingerprint {fingerprint}"
    st.code(bootstrap_cmd, language="bash")
