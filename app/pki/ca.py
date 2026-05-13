"""Intermediate CA loading and CSR signing.

In production, the intermediate CA cert + key are mounted from the
``rainmaker-pki-ca-intermediate`` Kubernetes Secret as files; the env
vars ``RM_PKI_CA_CERT_PATH`` and ``RM_PKI_CA_KEY_PATH`` point at them.
The user's *root* CA never lives on the cluster — only its public cert
is provided via ``RM_PKI_CA_ROOT_CERT_PATH`` so we can present the full
chain to clients.

In dev / test, if those paths are unset, a one-off CA chain is generated
in memory at first access and cached for the process lifetime. Tests
use this path to assert that issued device certs chain back to our
intermediate.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.core.config import get_settings


@lru_cache(maxsize=1)
def _ephemeral_chain() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey, x509.Certificate]:
    """Generate (intermediate cert, intermediate key, root cert) once.

    Used when the cluster Secret isn't configured (test/dev only).
    """
    s = get_settings()
    now = datetime.now(UTC)

    root_key = ec.generate_private_key(ec.SECP256R1())
    root_subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, s.pki_country),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, s.pki_org),
            x509.NameAttribute(NameOID.COMMON_NAME, s.pki_ca_cn),
        ]
    )
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(root_subject)
        .issuer_name(root_subject)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=365 * 20))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(root_key, hashes.SHA256())
    )

    inter_key = ec.generate_private_key(ec.SECP256R1())
    inter_subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, s.pki_country),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, s.pki_org),
            x509.NameAttribute(NameOID.COMMON_NAME, s.pki_ca_intermediate_cn),
        ]
    )
    inter_cert = (
        x509.CertificateBuilder()
        .subject_name(inter_subject)
        .issuer_name(root_subject)
        .public_key(inter_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=365 * 10))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(root_key, hashes.SHA256())
    )

    return inter_cert, inter_key, root_cert


def load_intermediate_ca() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    """Return the intermediate (cert, key) used to sign device CSRs."""
    s = get_settings()
    if s.pki_ca_cert_path and s.pki_ca_key_path:
        cert = x509.load_pem_x509_certificate(Path(s.pki_ca_cert_path).read_bytes())
        key_bytes = Path(s.pki_ca_key_path).read_bytes()
        key = serialization.load_pem_private_key(key_bytes, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise RuntimeError("Only EC P-256 intermediate keys are supported for now")
        return cert, key
    inter_cert, inter_key, _ = _ephemeral_chain()
    return inter_cert, inter_key


def load_root_cert() -> x509.Certificate:
    """Return the root CA cert (for chain assembly)."""
    s = get_settings()
    if s.pki_ca_root_cert_path:
        return x509.load_pem_x509_certificate(Path(s.pki_ca_root_cert_path).read_bytes())
    _, _, root_cert = _ephemeral_chain()
    return root_cert


def sign_device_csr(
    csr_pem: bytes,
    *,
    node_id: str,
    validity_days: int | None = None,
) -> tuple[bytes, str, datetime, datetime]:
    """Sign a device CSR with the intermediate CA.

    Returns ``(cert_pem_with_chain, serial_hex, not_before, not_after)``.
    The PEM payload includes both the issued cert and the intermediate
    so the device can present the full chain.
    """
    csr = x509.load_pem_x509_csr(csr_pem)
    if not csr.is_signature_valid:
        raise ValueError("CSR signature invalid")

    inter_cert, inter_key = load_intermediate_ca()
    s = get_settings()
    now = datetime.now(UTC)
    serial = secrets.randbits(128) | 1  # ensure positive, non-zero
    days = validity_days if validity_days is not None else s.cert_validity_days

    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)]))
        .issuer_name(inter_cert.subject)
        .public_key(csr.public_key())
        .serial_number(serial)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=True,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(node_id)]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(csr.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(inter_cert.public_key()),  # type: ignore[arg-type]
            critical=False,
        )
        .sign(inter_key, hashes.SHA256())
    )

    chain_pem = cert.public_bytes(serialization.Encoding.PEM) + inter_cert.public_bytes(
        serialization.Encoding.PEM
    )
    return chain_pem, f"{serial:x}", cert.not_valid_before_utc, cert.not_valid_after_utc
