"""Tests for CertificateService.ensure_certificates' CA-rotation guard.

When the shared CA is regenerated (e.g. its expiry crossed
``CA_EXPIRY_THRESHOLD_DAYS``), any per-VP printer certificate that was
signed by the OLD CA becomes orphaned: it still exists on disk and the
old fallback ``cert_path.exists()`` check would happily reuse it. A
slicer that imported the NEW CA then fails the TLS handshake because
the printer cert's issuer doesn't match anything in its trust store.

``_cert_matches_current_ca`` is the guard. It compares the on-disk
printer cert's issuer against the on-disk CA cert's subject; on
mismatch ``ensure_certificates`` regenerates the per-VP cert under the
current CA.
"""

from backend.app.services.virtual_printer.certificate import CertificateService


def test_ensure_certificates_reuses_cert_when_issuer_matches_ca(tmp_path):
    """Happy path: a freshly-generated CA + per-VP cert pair shares
    issuer/subject. ``ensure_certificates`` reads them back without
    regenerating."""
    svc = CertificateService(cert_dir=tmp_path, serial="01P00A391800001")

    # First call: generates the CA + per-VP cert from scratch.
    first_cert, first_key = svc.ensure_certificates()
    first_cert_bytes = first_cert.read_bytes()

    # Second call: cert + CA exist and the issuer matches. Should reuse.
    second_cert, _ = svc.ensure_certificates()
    assert second_cert.read_bytes() == first_cert_bytes


def test_ensure_certificates_regenerates_when_ca_rotated(tmp_path):
    """CA rotation scenario: the CA file is replaced with a different one
    (e.g. previous expired and was regenerated). The per-VP cert on disk
    was signed by the old CA, so its issuer no longer matches the new CA's
    subject. ``ensure_certificates`` must regenerate the per-VP cert."""
    # Build the first pair.
    svc1 = CertificateService(cert_dir=tmp_path, serial="01P00A391800001")
    orig_cert_bytes = svc1.ensure_certificates()[0].read_bytes()
    orig_ca_bytes = svc1.ca_cert_path.read_bytes()

    # Simulate CA rotation: build a SECOND CA in a different dir, then
    # swap that CA's files into the original CA path. The per-VP cert
    # still on disk was signed by the original CA — issuer mismatch now.
    rotated_dir = tmp_path / "rotated"
    rotated_dir.mkdir()
    svc_rotated = CertificateService(cert_dir=rotated_dir, serial="01P00A391800002")
    svc_rotated.ensure_certificates()

    # Overwrite the original CA on disk with the rotated one.
    svc1.ca_cert_path.write_bytes(svc_rotated.ca_cert_path.read_bytes())
    svc1.ca_key_path.write_bytes(svc_rotated.ca_key_path.read_bytes())
    assert svc1.ca_cert_path.read_bytes() != orig_ca_bytes  # confirm rotation

    # Build a fresh service against the rotated CA, then ensure_certificates
    # should detect the mismatch and regenerate the per-VP cert.
    svc2 = CertificateService(cert_dir=tmp_path, serial="01P00A391800001")
    new_cert, _ = svc2.ensure_certificates()
    new_cert_bytes = new_cert.read_bytes()

    # New per-VP cert must differ from the old (signed by a different CA now).
    assert new_cert_bytes != orig_cert_bytes


def test_cert_matches_current_ca_returns_false_when_no_ca(tmp_path):
    """If the CA file is missing entirely, the match check must return
    False so ``ensure_certificates`` falls through to ``generate_certificates``
    instead of returning a per-VP cert that nothing can validate."""
    svc = CertificateService(cert_dir=tmp_path, serial="01P00A391800001")
    # Write a per-VP cert without a CA.
    svc.cert_path.write_bytes(b"fake cert content")
    svc.key_path.write_bytes(b"fake key content")
    # No bbl_ca.crt on disk → match fails safely.
    assert svc._cert_matches_current_ca() is False
