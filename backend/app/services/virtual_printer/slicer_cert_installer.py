"""Scripts that add the virtual-printer CA to the slicers' printer.cer.

Bambu Studio and OrcaSlicer trust only the certificates in their own
``printer.cer``, and every slicer update or reinstall puts the stock file
back. Settings -> Virtual Printer offers these scripts for download with this
instance's CA written into them, so they need no API key and no connection to
Bambuddy: one for Windows (a ``.cmd`` that elevates itself and installs a task
that re-checks after updates) and one for Linux and macOS.
"""

from dataclasses import dataclass
from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[2] / "data" / "slicer_cert"

# platform -> template file, which is also the download's file name.
_PLATFORMS = {
    "windows": "bambuddy-slicer-cert.cmd",
    "unix": "bambuddy-slicer-cert.sh",
}

INSTALLER_PLATFORMS = tuple(_PLATFORMS)


@dataclass(frozen=True)
class SlicerCertInstaller:
    filename: str
    content: str


def build_slicer_cert_installer(platform: str, pem: str, fingerprint: str) -> SlicerCertInstaller:
    """Return the script for ``platform`` with the CA in ``pem`` written into it.

    Raises ``KeyError`` for an unknown platform.
    """
    filename = _PLATFORMS[platform]
    template = (_TEMPLATES / filename).read_text(encoding="utf-8")
    content = template.replace("__BAMBUDDY_CA_PEM__", pem.strip()).replace("__BAMBUDDY_CA_FINGERPRINT__", fingerprint)
    if filename.endswith(".cmd"):
        # cmd.exe misreads a batch file with bare LF line ends.
        content = content.replace("\r\n", "\n").replace("\n", "\r\n")
    return SlicerCertInstaller(filename=filename, content=content)
