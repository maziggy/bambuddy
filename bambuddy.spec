# -*- mode: python ; coding: utf-8 -*-
"""
Bambuddy PyInstaller spec file.
Build with: pyinstaller bambuddy.spec
"""
import os

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Project root
ROOT = os.path.dirname(os.path.abspath(SPEC))

# Collect all hidden imports
# These are modules that PyInstaller cannot detect via static analysis
# because they are imported dynamically (lazy imports, string-based imports, etc.)
hiddenimports = [
    # === FastAPI / Starlette / Uvicorn ===
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "starlette.responses",
    "starlette.routing",
    "starlette.middleware",
    "starlette.middleware.cors",
    "starlette.staticfiles",
    # === SQLAlchemy (dialect loaded by connection string) ===
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
    "aiosqlite",
    # === Pydantic (v2 uses Rust core with dynamic loading) ===
    "pydantic",
    "pydantic_core",
    "pydantic_settings",
    # === Greenlet (required by SQLAlchemy async) ===
    "greenlet",
    # === Cryptography (C extensions with backend loading) ===
    "cryptography",
    "cryptography.hazmat",
    "cryptography.hazmat.backends",
    "cryptography.hazmat.backends.openssl",
    "cryptography.hazmat.primitives",
    "cryptography.hazmat.primitives.asymmetric",
    "cryptography.hazmat.primitives.asymmetric.rsa",
    "cryptography.hazmat.primitives.hashes",
    "cryptography.hazmat.primitives.serialization",
    "cryptography.x509",
    "cryptography.x509.oid",
    "_cffi_backend",
    # === Auth ===
    "jwt",
    "jwt.algorithms",
    "jwt.exceptions",
    "passlib",
    "passlib.context",
    "passlib.handlers",
    "passlib.handlers.pbkdf2",
    # === MQTT ===
    "paho",
    "paho.mqtt",
    "paho.mqtt.client",
    "paho.mqtt.properties",
    "paho.mqtt.subscribeoptions",
    # === FTP ===
    "aioftp",
    "pyftpdlib",
    "pyftpdlib.handlers",
    "pyftpdlib.servers",
    "pyftpdlib.authorizers",
    "pyftpdlib.filesystems",
    # === HTTP client ===
    "httpx",
    "httpcore",
    "h11",
    "anyio",
    "anyio._backends",
    "anyio._backends._asyncio",
    "sniffio",
    # === Excel export ===
    "openpyxl",
    "openpyxl.workbook",
    "openpyxl.styles",
    "openpyxl.utils",
    # === QR Code / Image ===
    "qrcode",
    "qrcode.constants",
    "PIL",
    "PIL.Image",
    # === System monitoring ===
    "psutil",
    # === Async files ===
    "aiofiles",
    "aiofiles.os",
    # === Multipart (file uploads) ===
    "multipart",
    "python_multipart",
    # === Web push notifications ===
    "pywebpush",
    # === Standard library modules sometimes missed ===
    "email.mime.multipart",
    "email.mime.text",
    "smtplib",
    "ssl",
    "sqlite3",
    # === The entire backend package ===
    *collect_submodules("backend.app"),
]

# Data files to bundle (non-Python files needed at runtime)
datas = [
    # Frontend static files (React build output)
    (os.path.join(ROOT, "static"), "static"),
    # Backend JSON data files (field definitions for cloud presets)
    (os.path.join(ROOT, "backend", "app", "data"), os.path.join("backend", "app", "data")),
]

a = Analysis(
    [os.path.join(ROOT, "launcher.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude test/dev packages
        "pytest",
        "pytest_asyncio",
        "ruff",
        # Exclude unused heavy packages
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Determine icon path (use favicon if available)
icon_path = os.path.join(ROOT, "static", "img", "favicon.png")
if not os.path.exists(icon_path):
    icon_path = None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="bambuddy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Keep console for server log output
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)
