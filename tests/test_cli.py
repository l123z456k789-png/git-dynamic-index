from __future__ import annotations

from pathlib import Path

import pytest

from git_dynamic_index import cli, hooks
from git_dynamic_index.block import END_MARKER, START_MARKER
from git_dynamic_index.cli import main
from git_dynamic_index.gitstate import GitError


def run_cli(capsys, *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_help_exits_zero(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--help"])

    assert error.value.code == 0
    assert "init" in capsys.readouterr().out


def test_missing_command_exits_two(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main([])

    assert error.value.code == 2


def test_version(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--version"])

    assert error.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_init_creates_missing_file(repo: Path, capsys) -> None:
    code, out, err = run_cli(
        capsys, "init", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 0
    assert err == ""
    assert "Created" in out
    text = (repo / "README.md").read_text(encoding="utf-8")
    assert START_MARKER in text
    assert END_MARKER in text
    assert "- **Branch**: `main`" in text


def test_init_preserves_existing_content(repo: Path, capsys) -> None:
    original = "# Existing\n\nKeep this.\n"
    (repo / "README.md").write_text(original, encoding="utf-8")

    code, _, _ = run_cli(
        capsys, "init", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 0
    text = (repo / "README.md").read_text(encoding="utf-8")
    assert text.startswith(original)
    assert "Keep this." in text


def test_init_is_idempotent(repo: Path, capsys) -> None:
    first, _, _ = run_cli(
        capsys, "init", "--repo", str(repo), "--file", "README.md"
    )
    before = (repo / "README.md").stat().st_mtime_ns

    second, out, _ = run_cli(
        capsys, "init", "--repo", str(repo), "--file", "README.md"
    )

    assert first == 0
    assert second == 0
    assert "up to date" in out
    assert (repo / "README.md").stat().st_mtime_ns == before


def test_update_updates_block(repo: Path, write_file, commit, capsys) -> None:
    run_cli(capsys, "init", "--repo", str(repo), "--file", "README.md")
    write_file(repo / "app.py", "print(1)\n")
    commit("feat: app", "app.py")
    write_file(repo / "untracked file.txt", "x\n")

    code, out, err = run_cli(
        capsys, "update", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 0
    assert err == ""
    assert "updated" in out.lower()
    text = (repo / "README.md").read_text(encoding="utf-8")
    assert "feat: app" in text
    assert "1 changed (0 staged, 0 unstaged, 1 untracked, 0 conflicts)" in text
    assert "untracked file.txt" in text


def test_update_is_noop_when_current(repo: Path, capsys) -> None:
    run_cli(capsys, "init", "--repo", str(repo), "--file", "README.md")
    before = (repo / "README.md").stat().st_mtime_ns

    code, out, err = run_cli(
        capsys, "update", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 0
    assert err == ""
    assert "up to date" in out
    assert (repo / "README.md").stat().st_mtime_ns == before


def test_update_missing_file_errors_without_creating(repo: Path, capsys) -> None:
    code, _, err = run_cli(
        capsys, "update", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 1
    assert "init" in err
    assert not (repo / "README.md").exists()


def test_update_invalid_markers_does_not_write(repo: Path, capsys) -> None:
    path = repo / "README.md"
    original = f"# Title\n\n{START_MARKER}\nold\n"
    path.write_text(original, encoding="utf-8")

    code, _, err = run_cli(
        capsys, "update", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 1
    assert "end marker" in err
    assert path.read_text(encoding="utf-8") == original


def test_update_git_failure_does_not_write(repo: Path, monkeypatch, capsys) -> None:
    path = repo / "README.md"
    run_cli(capsys, "init", "--repo", str(repo), "--file", "README.md")
    original = path.read_bytes()

    def boom(*args: object, **kwargs: object) -> None:
        raise GitError("simulated git failure")

    monkeypatch.setattr(cli.gitstate, "collect", boom)
    code, _, err = run_cli(
        capsys, "update", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 1
    assert "simulated git failure" in err
    assert path.read_bytes() == original


def test_status_is_read_only(repo: Path, capsys) -> None:
    run_cli(capsys, "init", "--repo", str(repo), "--file", "README.md")
    path = repo / "README.md"
    before = path.stat().st_mtime_ns

    code, out, err = run_cli(
        capsys, "status", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 0
    assert err == ""
    assert "Repo:" in out
    assert "up to date" in out
    assert path.stat().st_mtime_ns == before


def test_status_reports_out_of_date(repo: Path, write_file, capsys) -> None:
    run_cli(capsys, "init", "--repo", str(repo), "--file", "README.md")
    write_file(repo / "new.txt", "x\n")

    code, out, _ = run_cli(
        capsys, "status", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 0
    assert "out of date" in out


def test_status_shows_head_and_last_non_index_commit(
    repo: Path, write_file, commit, capsys
) -> None:
    run_cli(capsys, "init", "--repo", str(repo), "--file", "README.md")
    write_file(repo / "app.py", "1\n")
    commit("feat: app", "app.py")
    commit("docs: readme", "README.md")

    code, out, _ = run_cli(
        capsys, "status", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 0
    assert "docs: readme" in out
    assert "feat: app" in out
    assert "HEAD:" in out
    assert "Last:" in out


def test_repo_option_works_from_outside_repository(repo: Path, tmp_path, monkeypatch, capsys) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    run_cli(capsys, "init", "--repo", str(repo), "--file", "README.md")

    code, out, _ = run_cli(
        capsys, "status", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 0
    assert str(repo) in out


def test_file_is_resolved_relative_to_repository(
    repo: Path, tmp_path, monkeypatch, capsys
) -> None:
    (repo / "docs").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    code, _, _ = run_cli(
        capsys, "init", "--repo", str(repo), "--file", "docs/STATUS.md"
    )

    assert code == 0
    assert (repo / "docs" / "STATUS.md").exists()
    assert not (elsewhere / "docs" / "STATUS.md").exists()


def test_file_outside_repository_is_rejected(repo: Path, tmp_path, capsys) -> None:
    outside = tmp_path / "outside.md"

    code, _, err = run_cli(
        capsys, "init", "--repo", str(repo), "--file", str(outside)
    )

    assert code == 1
    assert "outside" in err.lower()
    assert not outside.exists()


def test_non_markdown_file_is_rejected(repo: Path, capsys) -> None:
    code, _, err = run_cli(
        capsys, "init", "--repo", str(repo), "--file", "README.txt"
    )

    assert code == 1
    assert "markdown" in err.lower()


def test_not_a_repository_returns_nonzero(tmp_path: Path, capsys) -> None:
    code, _, err = run_cli(
        capsys, "update", "--repo", str(tmp_path / "not-repo")
    )

    assert code == 1
    assert "does not exist" in err or "Git" in err


def test_hook_mode_swallows_handled_errors(repo: Path, capsys) -> None:
    code, out, err = run_cli(
        capsys, "update", "--repo", str(repo), "--file", "README.md", "--hook"
    )

    assert code == 0
    assert out == ""
    assert "gdi:" in err
    assert not (repo / "README.md").exists()


def test_hook_mode_is_quiet_on_success(repo: Path, capsys) -> None:
    run_cli(capsys, "init", "--repo", str(repo), "--file", "README.md")

    code, out, err = run_cli(
        capsys, "update", "--repo", str(repo), "--file", "README.md", "--hook"
    )

    assert code == 0
    assert out == ""
    assert err == ""


def test_update_excludes_target_from_working_tree(repo: Path, capsys) -> None:
    run_cli(capsys, "init", "--repo", str(repo), "--file", "README.md")

    code, _, _ = run_cli(
        capsys, "update", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 0
    text = (repo / "README.md").read_text(encoding="utf-8")
    assert "- **Working tree (excl. this file)**: clean" in text


def test_status_shows_hook_state(repo: Path, capsys) -> None:
    run_cli(capsys, "init", "--repo", str(repo), "--file", "README.md")

    code, out, _ = run_cli(
        capsys, "status", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 0
    assert "Hooks:" in out
    assert "post-commit: not installed" in out


def test_install_hooks_requires_initialized_file(repo: Path, capsys) -> None:
    code, _, err = run_cli(
        capsys, "install-hooks", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 1
    assert "init" in err
    assert hooks.inspect(repo).status == "missing"


def test_install_and_uninstall_hooks(repo: Path, capsys) -> None:
    run_cli(capsys, "init", "--repo", str(repo), "--file", "README.md")

    code, out, err = run_cli(
        capsys, "install-hooks", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 0
    assert err == ""
    assert "post-commit" in out
    assert hooks.inspect(repo).status == "managed"

    code, out, _ = run_cli(
        capsys, "status", "--repo", str(repo), "--file", "README.md"
    )
    assert code == 0
    assert "post-commit: managed" in out

    code, out, err = run_cli(capsys, "uninstall-hooks", "--repo", str(repo))
    assert code == 0
    assert err == ""
    assert "removed" in out.lower()
    assert hooks.inspect(repo).status == "missing"


def test_install_hooks_refuses_foreign_hook(repo: Path, capsys) -> None:
    run_cli(capsys, "init", "--repo", str(repo), "--file", "README.md")
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    original = "#!/bin/sh\necho custom hook\n"
    hook.write_text(original, encoding="utf-8")

    code, _, err = run_cli(
        capsys, "install-hooks", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 1
    assert "not managed" in err
    assert hook.read_text(encoding="utf-8") == original

    code, out, err = run_cli(capsys, "uninstall-hooks", "--repo", str(repo))
    assert code == 0
    assert err == ""
    assert "foreign" in out
    assert hook.read_text(encoding="utf-8") == original


def test_install_hooks_refuses_core_hooks_path(repo: Path, run_git, capsys) -> None:
    run_cli(capsys, "init", "--repo", str(repo), "--file", "README.md")
    custom = repo.parent / "custom-hooks"
    run_git(repo, "config", "core.hooksPath", str(custom))

    code, _, err = run_cli(
        capsys, "install-hooks", "--repo", str(repo), "--file", "README.md"
    )

    assert code == 1
    assert "core.hooksPath" in err
    assert not (custom / "post-commit").exists()


def test_install_hooks_with_custom_file(repo: Path, capsys) -> None:
    (repo / "docs").mkdir()
    run_cli(
        capsys, "init", "--repo", str(repo), "--file", "docs/STATUS.md"
    )

    code, _, _ = run_cli(
        capsys, "install-hooks", "--repo", str(repo), "--file", "docs/STATUS.md"
    )

    assert code == 0
    state = hooks.inspect(repo)
    assert state.status == "managed"
    content = state.path.read_text(encoding="utf-8")
    assert "docs/STATUS.md" in content
