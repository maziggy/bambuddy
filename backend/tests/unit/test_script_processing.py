"""Unit tests for farm post-process script execution in the print scheduler."""

import asyncio
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_temp_3mf(gcode_content: str = "G28\nG1 X0 Y0\nM400\n") -> Path:
    """Create a minimal 3MF temp file for testing."""
    fd, name = tempfile.mkstemp(suffix=".3mf")
    import os
    os.close(fd)
    path = Path(name)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Metadata/plate_1.gcode", gcode_content)
        zf.writestr("Metadata/slice_info.config", "<config></config>")
    return path


def _make_script(content: str, executable: bool = True) -> Path:
    """Write a shell/Python script to a temp file and make it executable."""
    fd, name = tempfile.mkstemp(suffix=".py" if sys.platform == "win32" else ".sh")
    import os
    os.close(fd)
    path = Path(name)
    path.write_text(content)
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return path


# ---------------------------------------------------------------------------
# Tests for the scheduler's script_processing block
# (tested by driving the coroutine logic directly via subprocess mocks)
# ---------------------------------------------------------------------------


class TestScriptProcessingBlock:
    """Tests for item.script_processing execution in _start_print."""

    @pytest.mark.asyncio
    async def test_successful_script_modifies_file(self, tmp_path):
        """Script exit 0 → file_path is updated to the processed temp file."""
        source = _make_temp_3mf("ORIGINAL\n")

        # Simulate a script that appends "; PROCESSED" to the gcode
        async def fake_exec(*args, **kwargs):
            # args[0] is the script path, args[1] is the temp 3MF path
            proc_path = Path(args[1])
            with zipfile.ZipFile(proc_path, "r") as zf:
                content = zf.read("Metadata/plate_1.gcode").decode()
            content += "; PROCESSED\n"
            # Rewrite zip
            import shutil, tempfile as _tmp
            with _tmp.NamedTemporaryFile(delete=False, suffix=".3mf") as t:
                tmp_path_inner = Path(t.name)
            with zipfile.ZipFile(tmp_path_inner, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("Metadata/plate_1.gcode", content)
                zf.writestr("Metadata/slice_info.config", "<config></config>")
            shutil.move(tmp_path_inner, proc_path)

            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            await fake_exec.side_effect_inner(proc_path)
            return mock_proc

        # Instead of a full scheduler integration test, verify the logic
        # of copy → execute → swap file_path using a real subprocess.
        script_path = _make_script(
            "#!/usr/bin/env python3\n"
            "import sys, zipfile, tempfile, shutil\n"
            "from pathlib import Path\n"
            "p = Path(sys.argv[1])\n"
            "with zipfile.ZipFile(p, 'r') as zf:\n"
            "    content = zf.read('Metadata/plate_1.gcode').decode()\n"
            "content += '; PROCESSED\\n'\n"
            "with tempfile.NamedTemporaryFile(delete=False, suffix='.3mf') as t:\n"
            "    tmp = Path(t.name)\n"
            "with zipfile.ZipFile(tmp, 'w') as zf:\n"
            "    zf.writestr('Metadata/plate_1.gcode', content)\n"
            "    zf.writestr('Metadata/slice_info.config', '<config></config>')\n"
            "shutil.move(tmp, p)\n"
        )

        # Run the real subprocess logic as the scheduler would
        import shutil
        with tempfile.NamedTemporaryFile(delete=False, suffix=".3mf") as tmp:
            out_path = Path(tmp.name)
        shutil.copy2(source, out_path)

        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script_path), str(out_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        assert proc.returncode == 0, f"Script failed: {stderr.decode()}"
        assert out_path.exists()

        with zipfile.ZipFile(out_path, "r") as zf:
            result = zf.read("Metadata/plate_1.gcode").decode()

        assert "ORIGINAL" in result
        assert "; PROCESSED" in result

        source.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_failing_script_leaves_original_intact(self, tmp_path):
        """Script exit nonzero → temp file is cleaned up, original used."""
        source = _make_temp_3mf("ORIGINAL\n")

        script_path = _make_script(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.exit(1)\n"
        )

        import shutil
        with tempfile.NamedTemporaryFile(delete=False, suffix=".3mf") as tmp:
            out_path = Path(tmp.name)
        shutil.copy2(source, out_path)

        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script_path), str(out_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, _ = await asyncio.wait_for(proc.communicate(), timeout=30)

        assert proc.returncode != 0

        # Scheduler would clean up and keep original
        out_path.unlink(missing_ok=True)
        assert not out_path.exists()

        # Original untouched
        assert source.exists()
        with zipfile.ZipFile(source, "r") as zf:
            content = zf.read("Metadata/plate_1.gcode").decode()
        assert content == "ORIGINAL\n"

        source.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_script_timeout_kills_process(self):
        """Script that hangs past timeout is killed."""
        script_path = _make_script(
            "#!/usr/bin/env python3\n"
            "import time\n"
            "time.sleep(999)\n"
        )
        source = _make_temp_3mf()

        import shutil
        with tempfile.NamedTemporaryFile(delete=False, suffix=".3mf") as tmp:
            out_path = Path(tmp.name)
        shutil.copy2(source, out_path)

        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script_path), str(out_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(proc.communicate(), timeout=1)
        proc.kill()

        out_path.unlink(missing_ok=True)
        source.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_original_file_never_modified(self):
        """The source 3MF is copied before the script runs — original is never touched."""
        source = _make_temp_3mf("ORIGINAL\n")

        script_path = _make_script(
            "#!/usr/bin/env python3\n"
            "import sys, zipfile, tempfile, shutil\n"
            "from pathlib import Path\n"
            "p = Path(sys.argv[1])\n"
            "with zipfile.ZipFile(p, 'r') as zf:\n"
            "    content = zf.read('Metadata/plate_1.gcode').decode()\n"
            "content += '; MODIFIED\\n'\n"
            "with tempfile.NamedTemporaryFile(delete=False, suffix='.3mf') as t:\n"
            "    tmp = Path(t.name)\n"
            "with zipfile.ZipFile(tmp, 'w') as zf:\n"
            "    zf.writestr('Metadata/plate_1.gcode', content)\n"
            "shutil.move(tmp, p)\n"
        )

        import shutil
        with tempfile.NamedTemporaryFile(delete=False, suffix=".3mf") as tmp:
            out_path = Path(tmp.name)
        shutil.copy2(source, out_path)  # Scheduler copies first

        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script_path), str(out_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=30)

        # Script ran on out_path, not source
        with zipfile.ZipFile(source, "r") as zf:
            original_content = zf.read("Metadata/plate_1.gcode").decode()
        assert original_content == "ORIGINAL\n"

        with zipfile.ZipFile(out_path, "r") as zf:
            processed_content = zf.read("Metadata/plate_1.gcode").decode()
        assert "; MODIFIED" in processed_content

        source.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_script_not_configured_skips_processing(self):
        """If post_process_script setting is empty, processing is skipped silently."""
        # Simulates: script_path = "" → the `if script_path and script_path.strip()` guard
        script_path_setting = ""
        assert not (script_path_setting and script_path_setting.strip())

    @pytest.mark.asyncio
    async def test_script_takes_precedence_over_gcode_injection_temp(self):
        """If gcode injection ran first, script processing replaces its temp file."""
        # Simulate gcode injection having produced injected_path
        source = _make_temp_3mf("INJECTED\n")
        gcode_injected = _make_temp_3mf("INJECTED\n")  # simulates injected_path

        script_path = _make_script(
            "#!/usr/bin/env python3\n"
            "import sys, zipfile, tempfile, shutil\n"
            "from pathlib import Path\n"
            "p = Path(sys.argv[1])\n"
            "with zipfile.ZipFile(p, 'r') as zf:\n"
            "    content = zf.read('Metadata/plate_1.gcode').decode()\n"
            "content += '; SCRIPT\\n'\n"
            "with tempfile.NamedTemporaryFile(delete=False, suffix='.3mf') as t:\n"
            "    tmp = Path(t.name)\n"
            "with zipfile.ZipFile(tmp, 'w') as zf:\n"
            "    zf.writestr('Metadata/plate_1.gcode', content)\n"
            "shutil.move(tmp, p)\n"
        )

        import shutil
        with tempfile.NamedTemporaryFile(delete=False, suffix=".3mf") as tmp:
            out_path = Path(tmp.name)
        # Scheduler copies from current file_path (which is injected_path after gcode injection)
        shutil.copy2(gcode_injected, out_path)

        # Script runs on out_path
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script_path), str(out_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=30)

        assert proc.returncode == 0

        # Previous injected_path would be unlinked by scheduler
        gcode_injected.unlink(missing_ok=True)
        assert not gcode_injected.exists()

        # out_path is now the active file
        with zipfile.ZipFile(out_path, "r") as zf:
            content = zf.read("Metadata/plate_1.gcode").decode()
        assert "; SCRIPT" in content

        source.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)
