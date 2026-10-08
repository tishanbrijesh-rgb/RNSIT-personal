#!/usr/bin/env python3
"""
Generate minimal self-signed RSA-2048 and ECDSA P-256 certificates
into test-repo/certs/. Run this once before scanning.
"""

import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

_CERTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "test-repo", "certs",
)
os.makedirs(_CERTS_DIR, exist_ok=True)


def _make_rsa_cert() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ECDAT Demo"),
        x509.NameAttribute(NameOID.COMMON_NAME, "demo-server"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(x509.CertificateBuilder._now())  # type: ignore[attr-defined]
        .not_valid_after(
            x509.CertificateBuilder._now().replace(year=2030)  # type: ignore[attr-defined]
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def _make_ecdsa_cert() -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ECDAT Demo"),
        x509.NameAttribute(NameOID.COMMON_NAME, "demo-ec-client"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(x509.CertificateBuilder._now())  # type: ignore[attr-defined]
        .not_valid_after(
            x509.CertificateBuilder._now().replace(year=2030)  # type: ignore[attr-defined]
        )
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def main() -> None:
    import datetime
    # Override the _now placeholder
    now = datetime.datetime.now(datetime.timezone.utc)

    # RSA cert
    key_rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject_rsa = issuer_rsa = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ECDAT Demo"),
        x509.NameAttribute(NameOID.COMMON_NAME, "demo-server"),
    ])
    cert_rsa = (
        x509.CertificateBuilder()
        .subject_name(subject_rsa)
        .issuer_name(issuer_rsa)
        .public_key(key_rsa.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now.replace(year=now.year + 4))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key_rsa, hashes.SHA256())
    )
    rsa_path = os.path.join(_CERTS_DIR, "server.crt")
    with open(rsa_path, "wb") as fh:
        fh.write(cert_rsa.public_bytes(serialization.Encoding.PEM))
    print(f"Wrote {rsa_path}  (RSA-2048, SHA-256)")

    # ECDSA cert
    key_ec = ec.generate_private_key(ec.SECP256R1())
    subject_ec = issuer_ec = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ECDAT Demo"),
        x509.NameAttribute(NameOID.COMMON_NAME, "demo-ec-client"),
    ])
    cert_ec = (
        x509.CertificateBuilder()
        .subject_name(subject_ec)
        .issuer_name(issuer_ec)
        .public_key(key_ec.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now.replace(year=now.year + 4))
        .sign(key_ec, hashes.SHA256())
    )
    ec_path = os.path.join(_CERTS_DIR, "client.crt")
    with open(ec_path, "wb") as fh:
        fh.write(cert_ec.public_bytes(serialization.Encoding.PEM))
    print(f"Wrote {ec_path}  (ECDSA P-256, SHA-256)")


if __name__ == "__main__":
    main()
