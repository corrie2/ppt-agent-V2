from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from click.testing import CliRunner
import pytest
import _pytest.pathlib
import _pytest.tmpdir


ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = ROOT / "tests"
LEGACY_TEST_TEMP = TEST_ROOT / ".tmp"
TEST_TEMP = ROOT / "pytest-cache-files-tests"
_ORIGINAL_ISOLATED_FILESYSTEM = CliRunner.isolated_filesystem


def _safe_rmtree(path: Path, *, attempts: int = 5) -> None:
    for attempt in range(attempts):
        try:
            shutil.rmtree(path, ignore_errors=False)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == attempts - 1:
                shutil.rmtree(path, ignore_errors=True)
                return
            time.sleep(0.1 * (attempt + 1))
        except OSError:
            if attempt == attempts - 1:
                shutil.rmtree(path, ignore_errors=True)
                return
            time.sleep(0.1 * (attempt + 1))


def pytest_configure(config) -> None:
    TEST_TEMP.mkdir(parents=True, exist_ok=True)
    os.environ["TMP"] = str(TEST_TEMP)
    os.environ["TEMP"] = str(TEST_TEMP)
    tempfile.tempdir = str(TEST_TEMP)
    _pytest.pathlib.cleanup_dead_symlinks = lambda root: None
    _pytest.tmpdir.cleanup_dead_symlinks = lambda root: None

    @contextlib.contextmanager
    def isolated_filesystem(self, temp_dir=None):
        base_dir = Path(temp_dir) if temp_dir else TEST_TEMP / "isolated"
        base_dir.mkdir(parents=True, exist_ok=True)
        cwd = Path.cwd()
        path = base_dir / f"tmp-{uuid4().hex}"
        path.mkdir(parents=True, exist_ok=False)
        try:
            os.chdir(path)
            yield str(path)
        finally:
            os.chdir(cwd)
            if temp_dir is None:
                _safe_rmtree(path)

    CliRunner.isolated_filesystem = isolated_filesystem


def pytest_sessionstart(session) -> None:
    tmp_root = ROOT / ".tmp"
    cleanup_targets = [
        ROOT / ".pytest_cache",
        TEST_TEMP,
        LEGACY_TEST_TEMP,
        *ROOT.glob("pytest-cache-files-*"),
    ]
    if tmp_root.exists():
        cleanup_targets.extend(tmp_root.glob("pytest-cache-files-*"))
    for path in cleanup_targets:
        try:
            path.resolve().relative_to(ROOT)
        except ValueError:
            continue
        _safe_rmtree(path)
    TEST_TEMP.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def tmp_path(request) -> Path:
    safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in request.node.name)[:80]
    path = TEST_TEMP / f"{safe_name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        _safe_rmtree(path)
