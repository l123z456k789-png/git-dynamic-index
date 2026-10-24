from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
FIXED_DATE = "2026-01-02T03:04:05+00:00"


@pytest.fixture(autouse=True)
def isolated_git_environment(monkeypatch, tmp_path: Path) -> None:
    """Keep tests away from the user's global Git config and parent repos."""
    empty_config = tmp_path / "empty-gitconfig"
    empty_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")

    existing = os.environ.get("PYTHONPATH")
    pythonpath = str(SRC)
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    monkeypatch.setenv("PYTHONPATH", pythonpath)


@pytest.fixture
def run_git():
    def _run(
        repo: Path,
        *args: str,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        merged = os.environ.copy()
        merged["GIT_PAGER"] = "cat"
        if env:
            merged.update(env)
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=GDI Test",
                "-c",
                "user.email=gdi@example.com",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.autocrlf=false",
                *args,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=merged,
            timeout=60,
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"git {' '.join(args)} failed ({result.returncode}):\n{result.stderr}"
            )
        return result

    return _run


@pytest.fixture
def repo(tmp_path: Path, run_git) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    run_git(path, "init", "-b", "main")
    return path


@pytest.fixture
def write_file():
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))

    return _write


@pytest.fixture
def commit(repo: Path, run_git):
    def _commit(
        message: str,
        *paths: str,
        add_all: bool = False,
        date: str = FIXED_DATE,
    ) -> None:
        if add_all:
            run_git(repo, "add", "-A")
        elif paths:
            run_git(repo, "add", "--", *paths)
        run_git(
            repo,
            "commit",
            "-m",
            message,
            env={
                "GIT_AUTHOR_DATE": date,
                "GIT_COMMITTER_DATE": date,
            },
        )

    return _commit
