"""
Build script for Bambuddy Windows executable.

Usage:
    python build_exe.py             Build the exe
    python build_exe.py --clean     Clean build artifacts first
    python build_exe.py --check-only  Only check prerequisites

Prerequisites:
    - Python 3.11+
    - pip install pyinstaller
    - Frontend must be built (static/ directory must exist)
    - pip install -r requirements.txt
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()


def check_prerequisites():
    """Verify all build prerequisites are met."""
    errors = []

    # Check Python version
    if sys.version_info < (3, 11):
        errors.append(f"Python 3.11+ required, found {sys.version}")
    else:
        print(f"  Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    # Check PyInstaller
    try:
        import PyInstaller

        print(f"  PyInstaller {PyInstaller.__version__}")
    except ImportError:
        errors.append("PyInstaller not installed. Run: pip install pyinstaller")

    # Check static directory (frontend build output)
    static_dir = ROOT / "static"
    index_html = static_dir / "index.html"
    if not index_html.exists():
        errors.append(
            "Frontend not built. Run 'npm run build' in frontend/ first.\n"
            "  Expected: static/index.html"
        )
    else:
        file_count = sum(1 for _ in static_dir.rglob("*") if _.is_file())
        print(f"  Static files: {file_count} files")

    # Check backend data files
    data_dir = ROOT / "backend" / "app" / "data"
    json_files = list(data_dir.glob("*.json"))
    if not json_files:
        errors.append("Backend data files missing: backend/app/data/*.json")
    else:
        print(f"  Data files: {', '.join(f.name for f in json_files)}")

    # Check launcher.py
    if not (ROOT / "launcher.py").exists():
        errors.append("launcher.py not found in project root")

    # Check bambuddy.spec
    if not (ROOT / "bambuddy.spec").exists():
        errors.append("bambuddy.spec not found in project root")

    # Check key dependencies are installed
    try:
        import fastapi
        import uvicorn

        print(f"  FastAPI {fastapi.__version__}, uvicorn {uvicorn.__version__}")
    except ImportError as e:
        errors.append(f"Required package missing: {e}. Run: pip install -r requirements.txt")

    return errors


def clean():
    """Remove build artifacts."""
    dirs_to_clean = ["build", "dist"]
    for d in dirs_to_clean:
        path = ROOT / d
        if path.exists():
            print(f"  Removing {path}")
            shutil.rmtree(path)

    cache = ROOT / "__pycache__"
    if cache.exists():
        shutil.rmtree(cache)


def build():
    """Run PyInstaller build."""
    spec_file = ROOT / "bambuddy.spec"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        str(spec_file),
    ]

    print(f"  Command: {' '.join(cmd)}")
    print()
    result = subprocess.run(cmd, cwd=str(ROOT))

    if result.returncode != 0:
        print()
        print("BUILD FAILED")
        sys.exit(1)

    # Verify output
    exe_path = ROOT / "dist" / "bambuddy.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\n  Output: {exe_path}")
        print(f"  Size: {size_mb:.1f} MB")
    else:
        print("\nERROR: Expected output not found at dist/bambuddy.exe")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Build Bambuddy Windows executable")
    parser.add_argument("--clean", action="store_true", help="Clean build artifacts before building")
    parser.add_argument("--check-only", action="store_true", help="Only check prerequisites")
    args = parser.parse_args()

    print("=" * 50)
    print("  Bambuddy Executable Builder")
    print("=" * 50)
    print()

    if args.clean:
        print("[1] Cleaning build artifacts...")
        clean()
        print()

    print("[2] Checking prerequisites...")
    errors = check_prerequisites()
    if errors:
        print()
        for e in errors:
            print(f"  ERROR: {e}")
        print()
        print("Fix the above errors and try again.")
        sys.exit(1)
    print("  All prerequisites OK")
    print()

    if args.check_only:
        print("Prerequisites check passed.")
        return

    print("[3] Building executable...")
    build()

    print()
    print("=" * 50)
    print("  BUILD SUCCESSFUL")
    print("=" * 50)
    print()
    print("  The executable is at: dist\\bambuddy.exe")
    print()
    print("  To use:")
    print("  1. Copy bambuddy.exe to any directory")
    print("  2. Double-click to run")
    print("  3. Open http://localhost:8000 in your browser")
    print()
    print("  Data files (database, archives, logs) will be")
    print("  created in the same directory as the exe.")
    print()


if __name__ == "__main__":
    main()
