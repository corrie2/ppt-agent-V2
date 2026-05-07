import shutil
import subprocess

import pytest

from ppt_agent.storage.memory_scope import resolve_project_scope


def test_resolve_project_scope_falls_back_for_non_git_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent.resolve()))

    scope = resolve_project_scope(tmp_path)

    assert scope.root_path == tmp_path.resolve()
    assert scope.name == tmp_path.name
    assert scope.git_remote is None


def test_resolve_project_scope_uses_git_root_for_workspace_subdirectory(tmp_path):
    _require_git()
    repo = tmp_path / "repo"
    nested = repo / "sub" / "dir"
    nested.mkdir(parents=True)
    _git(repo, "init")

    scope = resolve_project_scope(nested)

    assert scope.root_path == repo.resolve()
    assert scope.name == repo.name


def test_resolve_project_scope_reads_origin_remote(tmp_path):
    _require_git()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "remote", "add", "origin", "git@example.com:test/repo.git")

    scope = resolve_project_scope(repo)

    assert scope.git_remote == "git@example.com:test/repo.git"


def test_resolve_project_scope_returns_none_without_origin_remote(tmp_path):
    _require_git()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    scope = resolve_project_scope(repo)

    assert scope.git_remote is None


def _require_git() -> None:
    if shutil.which("git") is None:
        pytest.skip("git command is not available")


def _git(repo, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=True,
        text=True,
    )
