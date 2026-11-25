"""The slicer certificate scripts (Settings -> Virtual Printer).

The bash script runs for real against a folder laid out like a slicer
install. The PowerShell part of the Windows script runs the same way when
``pwsh`` is installed (GitHub's Ubuntu runners have it); what needs Windows
itself (administrator rights, the scheduled task) stays out of the test run.
"""

import datetime
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from backend.app.services.virtual_printer.slicer_cert_installer import build_slicer_cert_installer

CA_NAME = "Virtual Printer CA TEST1234"
FINGERPRINT = ":".join(["AB", "CD", "EF", "01"] * 8)


def _make_cert(common_name: str) -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _body(pem: str) -> str:
    return "".join(line for line in pem.strip().splitlines() if "-----" not in line)


def _bodies(path: Path) -> list[str]:
    text = path.read_text().replace("\r", "")
    return [_body(chunk) for chunk in text.split("-----BEGIN CERTIFICATE-----")[1:]]


@pytest.fixture(scope="module")
def certs() -> dict[str, str]:
    return {
        "ours": _make_cert(CA_NAME),
        "ours_before_reset": _make_cert(CA_NAME),
        "stock1": _make_cert("BBL CA"),
        "stock2": _make_cert("BBL CA2 RSA"),
    }


def _slicer(root: Path, name: str, content: str) -> Path:
    path = root / name / "resources" / "cert" / "printer.cer"
    path.parent.mkdir(parents=True)
    path.write_text(content, newline="")
    return path


class TestBuild:
    def test_windows_script_has_the_ca_and_crlf_line_ends(self, certs):
        installer = build_slicer_cert_installer("windows", certs["ours"], FINGERPRINT)

        assert installer.filename == "bambuddy-slicer-cert.cmd"
        assert _body(certs["ours"])[:60] in installer.content
        assert FINGERPRINT in installer.content
        assert "__BAMBUDDY_" not in installer.content
        assert "\n" not in installer.content.replace("\r\n", "")

    def test_unix_script_has_the_ca_and_lf_line_ends(self, certs):
        installer = build_slicer_cert_installer("unix", certs["ours"], FINGERPRINT)

        assert installer.filename == "bambuddy-slicer-cert.sh"
        assert _body(certs["ours"])[:60] in installer.content
        assert "__BAMBUDDY_" not in installer.content
        assert "\r" not in installer.content

    def test_unknown_platform(self, certs):
        with pytest.raises(KeyError):
            build_slicer_cert_installer("amiga", certs["ours"], FINGERPRINT)


def _runner(kind: str, tmp_path: Path, pem: str):
    """Return a function that runs the script once over ``tmp_path / 'apps'``."""
    content = build_slicer_cert_installer(kind, pem, FINGERPRINT).content
    if kind == "unix":
        script = tmp_path / "bambuddy-slicer-cert.sh"
        script.write_text(content)
        cmd = ["bash", str(script)]
        separator = " "
    else:
        # The .cmd wrapper only runs on Windows; the PowerShell part after
        # its marker is what does the work.
        script = tmp_path / "bambuddy-slicer-cert.ps1"
        script.write_text(content[content.index("#BAMBUDDY_POWERSHELL") :], newline="")
        cmd = [shutil.which("pwsh") or "pwsh", "-NoProfile", "-NonInteractive", "-File", str(script)]
        separator = ";"

    def run(*roots: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "HOME": str(tmp_path),
                "BAMBUDDY_CERT_TEST_ROOTS": separator.join(map(str, roots)),
            },
        )

    return run


KINDS = [
    pytest.param(
        "unix",
        marks=pytest.mark.skipif(
            not (shutil.which("bash") and shutil.which("openssl")), reason="needs bash and openssl"
        ),
    ),
    pytest.param("windows", marks=pytest.mark.skipif(not shutil.which("pwsh"), reason="needs pwsh")),
]


@pytest.mark.parametrize("kind", KINDS)
class TestScripts:
    def test_adds_the_ca_keeps_the_rest_and_a_backup(self, kind, tmp_path, certs):
        apps = tmp_path / "apps"
        stock = certs["stock1"] + certs["stock2"]
        studio = _slicer(apps, "Bambu Studio", stock)
        orca = _slicer(apps, "OrcaSlicer", stock)

        result = _runner(kind, tmp_path, certs["ours"])(apps)

        assert result.returncode == 0, result.stdout + result.stderr
        for path in (studio, orca):
            assert _bodies(path) == [_body(certs["stock1"]), _body(certs["stock2"]), _body(certs["ours"])]
            assert Path(f"{path}.before-bambuddy").read_text() == stock
            assert not Path(f"{path}.bambuddy-new").exists()
        assert "added" in result.stdout

    def test_a_second_run_changes_nothing(self, kind, tmp_path, certs):
        apps = tmp_path / "apps"
        studio = _slicer(apps, "Bambu Studio", certs["stock1"])
        run = _runner(kind, tmp_path, certs["ours"])
        run(apps)
        after_first = studio.read_bytes()

        result = run(apps)

        assert result.returncode == 0, result.stdout + result.stderr
        assert studio.read_bytes() == after_first
        assert "already there" in result.stdout

    def test_an_older_certificate_of_this_bambuddy_is_replaced(self, kind, tmp_path, certs):
        # Same name, other key: a slicer would take the first and fail.
        apps = tmp_path / "apps"
        studio = _slicer(apps, "Bambu Studio", certs["stock1"] + certs["ours_before_reset"] + certs["stock2"])

        result = _runner(kind, tmp_path, certs["ours"])(apps)

        assert result.returncode == 0, result.stdout + result.stderr
        assert _bodies(studio) == [_body(certs["stock1"]), _body(certs["stock2"]), _body(certs["ours"])]
        assert "replaced" in result.stdout

    def test_another_install_with_the_bare_legacy_name_is_kept(self, kind, tmp_path, certs):
        # Every Bambuddy before per-install names called its CA just
        # "Virtual Printer CA": that one may be another instance's.
        ours = _make_cert("Virtual Printer CA")
        other = _make_cert("Virtual Printer CA")
        apps = tmp_path / "apps"
        studio = _slicer(apps, "Bambu Studio", certs["stock1"] + other)

        result = _runner(kind, tmp_path, ours)(apps)

        assert result.returncode == 0, result.stdout + result.stderr
        assert _bodies(studio) == [_body(certs["stock1"]), _body(other), _body(ours)]

    def test_a_file_with_crlf_and_no_final_newline(self, kind, tmp_path, certs):
        apps = tmp_path / "apps"
        studio = _slicer(apps, "Bambu Studio", certs["stock1"].strip().replace("\n", "\r\n"))

        result = _runner(kind, tmp_path, certs["ours"])(apps)

        assert result.returncode == 0, result.stdout + result.stderr
        assert _bodies(studio) == [_body(certs["stock1"]), _body(certs["ours"])]
        text = studio.read_text().replace("\r", "")
        assert "-----END CERTIFICATE----------BEGIN" not in text

    def test_no_slicer_found(self, kind, tmp_path, certs):
        (tmp_path / "apps").mkdir()

        result = _runner(kind, tmp_path, certs["ours"])(tmp_path / "apps")

        assert result.returncode == 2
        assert "No Bambu Studio or OrcaSlicer" in result.stdout
