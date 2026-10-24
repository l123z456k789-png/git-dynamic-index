from __future__ import annotations

import sys
from pathlib import Path

import pytest

from git_dynamic_index import hooks
from git_dynamic_index.block import END_MARKER, START_MARKER
from git_dynamic_index.hooks import HookError


def target_with_markers(repo: Path) -> Path:
    target = repo / "README.md"
    target.write_text(
        f"# Title\n\n{START_MARKER}\n{END_MARKER}\n",
        encoding="utf-8",
    )
    return target


def test_missing_hook_state(repo: Path) -> None:
    state = hooks.inspect(repo)

    assert state.status == "missing"
    assert state.path == (repo / ".git" / "hooks" / "post-commit").resolve()


def test_install_writes_managed_hook(repo: Path) -> None:
    target = target_with_markers(repo)

    state = hooks.install(repo, target)

    assert state.status == "managed"
    assert state.path is not None
    content = state.path.read_text(encoding="utf-8")
    assert hooks.MANAGED_MARKER in content
    assert Path(sys.executable).as_posix() in content
    assert repo.as_posix() in content
    assert target.as_posix() in content
    assert "--hook" in content
    assert "exit 0" in content
    assert "\r\n" not in content


def test_install_is_idempotent(repo: Path) -> None:
    target = target_with_markers(repo)
    first = hooks.install(repo, target)
    first_content = first.path.read_text(encoding="utf-8")

    second = hooks.install(repo, target)

    assert second.status == "managed"
    assert second.path.read_text(encoding="utf-8") == first_content


def test_install_refuses_foreign_hook(repo: Path) -> None:
    target = target_with_markers(repo)
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    original = "#!/bin/sh\necho custom hook\n"
    hook.write_text(original, encoding="utf-8")

    with pytest.raises(HookError, match="not managed"):
        hooks.install(repo, target)

    assert hook.read_text(encoding="utf-8") == original
    assert hooks.inspect(repo).status == "foreign"


def test_uninstall_removes_managed_hook(repo: Path) -> None:
    target = target_with_markers(repo)
    state = hooks.install(repo, target)

    removed = hooks.uninstall(repo)

    assert state.path is not None
    assert not state.path.exists()
    assert removed.status == "removed"


def test_uninstall_leaves_foreign_hook(repo: Path) -> None:
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    original = "#!/bin/sh\necho custom hook\n"
    hook.write_text(original, encoding="utf-8")

    state = hooks.uninstall(repo)

    assert state.status == "foreign"
    assert hook.read_text(encoding="utf-8") == original


def test_uninstall_missing_hook_is_noop(repo: Path) -> None:
    state = hooks.uninstall(repo)

    assert state.status == "missing"


def test_core_hooks_path_is_never_touched(repo: Path, run_git) -> None:
    target = target_with_markers(repo)
    custom = repo.parent / "custom-hooks"
    run_git(repo, "config", "core.hooksPath", str(custom))

    state = hooks.inspect(repo)
    assert state.status == "unsupported"

    with pytest.raises(HookError, match="core.hooksPath"):
        hooks.install(repo, target)

    assert not (custom / "post-commit").exists()


def test_install_refuses_when_python_cannot_import(repo: Path, monkeypatch) -> None:
    target = target_with_markers(repo)
    monkeypatch.setattr(hooks, "_python_can_import", lambda: False)

    with pytest.raises(HookError, match="import"):
        hooks.install(repo, target)

    assert not (repo / ".git" / "hooks" / "post-commit").exists()
