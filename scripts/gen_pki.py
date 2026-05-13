#!/usr/bin/env python3
"""Generate test PKI artifacts for local dev / Palier B end-to-end tests.

Produces under ``var/pki/``:

  ca-root.pem            self-signed root CA (offline equivalent — trust anchor only)
  ca-root.key            (kept; in prod this stays offline)
  ca-intermediate.pem    intermediate signed by root, used to sign device certs
  ca-intermediate.key    intermediate private key, mounted into api + vmq-authz
  ca-chain.pem           ``intermediate || root`` for clients that need the chain
  server.pem             VerneMQ server cert (CN=vernemq, SAN=vernemq,localhost,127.0.0.1)
  server.key             matching server key
"""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def _now() -> datetime:
    return datetime.now(UTC)


def _save_key(path: Path, key: ec.EllipticCurvePrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    # Compose mounts these into non-root containers; world-readable is fine for dev.
    path.chmod(0o644)


def _save_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def main() -> None:
    out = Path("var/pki")
    out.mkdir(parents=True, exist_ok=True)
    now = _now()

    # Root.
    root_subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "FR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ESP RainMaker Self-Hosted"),
            x509.NameAttribute(NameOID.COMMON_NAME, "ESP RainMaker Root CA"),
        ]
    )
    root_key = ec.generate_private_key(ec.SECP256R1())
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
    _save_cert(out / "ca-root.pem", root_cert)
    _save_key(out / "ca-root.key", root_key)

    # Intermediate signed by root.
    inter_subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "FR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ESP RainMaker Self-Hosted"),
            x509.NameAttribute(NameOID.COMMON_NAME, "ESP RainMaker Intermediate CA"),
        ]
    )
    inter_key = ec.generate_private_key(ec.SECP256R1())
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
    _save_cert(out / "ca-intermediate.pem", inter_cert)
    _save_key(out / "ca-intermediate.key", inter_key)

    (out / "ca-chain.pem").write_bytes(
        inter_cert.public_bytes(serialization.Encoding.PEM)
        + root_cert.public_bytes(serialization.Encoding.PEM)
    )

    # VerneMQ server cert.
    server_key = ec.generate_private_key(ec.SECP256R1())
    server_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "vernemq")])
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_subject)
        .issuer_name(inter_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=365 * 5))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
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
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("vernemq"),
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(inter_cert.public_key()),
            critical=False,
        )
        .sign(inter_key, hashes.SHA256())
    )
    _save_cert(out / "server.pem", server_cert)
    _save_key(out / "server.key", server_key)

    print(f"Generated {len(list(out.glob('*')))} PKI artifacts under {out.resolve()}")
    for name in [
        "ca-root.pem",
        "ca-root.key",
        "ca-intermediate.pem",
        "ca-intermediate.key",
        "ca-chain.pem",
        "server.pem",
        "server.key",
    ]:
        path = out / name
        print(f"  {path}  ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
