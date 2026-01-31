"""
Bambuddy Windows exe launcher.
PyInstaller entry point - must be at project root.

This script runs BEFORE any backend imports to configure
environment variables that config.py reads at import time.
"""
import multiprocessing
import os
import sys
import threading
import webbrowser


def get_exe_dir() -> str:
    """Get the directory containing the actual .exe file."""
    if getattr(sys, "frozen", False):
        # PyInstaller --onefile: sys.executable is the .exe path
        return os.path.dirname(os.path.abspath(sys.executable))
    else:
        # Development: use script directory
        return os.path.dirname(os.path.abspath(__file__))


def get_app_dir() -> str:
    """Get the application directory (where bundled files are extracted)."""
    if getattr(sys, "frozen", False):
        # PyInstaller sets sys._MEIPASS to the temp extraction directory
        return sys._MEIPASS
    else:
        return os.path.dirname(os.path.abspath(__file__))


def open_browser(port: int):
    """Open browser after a short delay."""
    import time

    time.sleep(3)
    webbrowser.open(f"http://localhost:{port}")


def main():
    # Required for Windows + PyInstaller
    multiprocessing.freeze_support()

    exe_dir = get_exe_dir()
    app_dir = get_app_dir()
    port = int(os.environ.get("PORT", "8000"))

    # Set environment variables BEFORE importing backend modules.
    # config.py reads DATA_DIR and LOG_DIR at import time.
    os.environ.setdefault("DATA_DIR", exe_dir)
    os.environ.setdefault("LOG_DIR", os.path.join(exe_dir, "logs"))

    # Set BAMBUDDY_APP_DIR for config.py to find static files in _MEIPASS
    os.environ["BAMBUDDY_APP_DIR"] = app_dir

    # Ensure paths are on sys.path for module resolution
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    print()
    print("  ____                  ____            _     _       ")
    print(" | __ )  __ _ _ __ ___ | __ ) _   _  __| | __| |_   _")
    print(" |  _ \\ / _` | '_ ` _ \\|  _ \\| | | |/ _` |/ _` | | | |")
    print(" | |_) | (_| | | | | | | |_) | |_| | (_| | (_| | |_| |")
    print(" |____/ \\__,_|_| |_| |_|____/ \\__,_|\\__,_|\\__,_|\\__, |")
    print("                                                 |___/ ")
    print()
    print(f"  Data directory: {exe_dir}")
    print(f"  Starting on port {port}...")
    print(f"  Open: http://localhost:{port}")
    print()

    # Open browser in background thread
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    # Import and start uvicorn AFTER env vars are set
    import uvicorn

    uvicorn.run(
        "backend.app.main:app",
        host="0.0.0.0",
        port=port,
        loop="asyncio",
        log_level="info",
    )


if __name__ == "__main__":
    main()
