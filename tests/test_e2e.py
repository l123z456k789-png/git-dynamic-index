from __future__ import annotations

from pathlib import Path

from git_dynamic_index import hooks
from git_dynamic_index.block import END_MARKER, START_MARKER
from git_dynamic_index.cli import main

DATE = "2026-01-02T03:04:05+00:00"


def cli_ok(capsys, *args: str) -> str:
    code = main(list(args))
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert captured.err == ""
    return captured.out


def run_commit(run_git, repo: Path, message: str, *paths: str) -> None:
    run_git(repo, "add", "--", *paths)
    run_git(
        repo,
        "commit",
        "-m",
        message,
        env={"GIT_AUTHOR_DATE": DATE, "GIT_COMMITTER_DATE": DATE},
    )


def prepare_repo(repo: Path, capsys, run_git) -> None:
    cli_ok(capsys, "init", "--repo", str(repo), "--file", "README.md")
    run_commit(run_git, repo, "docs: initial index", "README.md")
    cli_ok(capsys, "install-hooks", "--repo", str(repo), "--file", "README.md")


def test_post_commit_hook_updates_and_does_not_loop(
    repo: Path, write_file, run_git, capsys
) -> None:
    prepare_repo(repo, capsys, run_git)
    assert hooks.inspect(repo).status == "managed"

    write_file(repo / "app.py", "print(1)\n")
    run_commit(run_git, repo, "feat: app", "app.py")

    readme = (repo / "README.md").read_text(encoding="utf-8")
    short = run_git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    assert "feat: app" in readme
    assert short in readme
    status = run_git(repo, "status", "--porcelain").stdout
    assert " M README.md" in status

    snapshot = (repo / "README.md").read_bytes()
    run_commit(run_git, repo, "docs: refresh index", "README.md")

    assert (repo / "README.md").read_bytes() == snapshot
    assert run_git(repo, "status", "--porcelain").stdout == ""


def test_hook_failure_does_not_block_commit(
    repo: Path, write_file, run_git, capsys
) -> None:
    prepare_repo(repo, capsys, run_git)
    broken = f"# Title\n\n{START_MARKER}\nold\n"
    (repo / "README.md").write_text(broken, encoding="utf-8")

    write_file(repo / "app.py", "print(2)\n")
    run_git(repo, "add", "app.py")
    result = run_git(
        repo,
        "commit",
        "-m",
        "feat: app",
        env={"GIT_AUTHOR_DATE": DATE, "GIT_COMMITTER_DATE": DATE},
        check=False,
    )

    assert result.returncode == 0
    assert "gdi:" in result.stderr
    assert (repo / "README.md").read_text(encoding="utf-8") == broken


def test_hook_works_with_chinese_and_space_repo_path(
    tmp_path: Path, run_git, capsys
) -> None:
    repo = tmp_path / "仓库 with space"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")

    cli_ok(capsys, "init", "--repo", str(repo), "--file", "README.md")
    run_commit(run_git, repo, "docs: initial index", "README.md")
    cli_ok(capsys, "install-hooks", "--repo", str(repo), "--file", "README.md")

    (repo / "app.py").write_bytes(b"print(3)\n")
    run_commit(run_git, repo, "feat: app", "app.py")

    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert "feat: app" in readme
    assert hooks.inspect(repo).status == "managed"


def test_update_does_not_touch_files_outside_repository(
    repo: Path, tmp_path: Path, capsys
) -> None:
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_bytes(b"do not touch\n")
    before = sentinel.stat().st_mtime_ns

    cli_ok(capsys, "init", "--repo", str(repo), "--file", "README.md")
    cli_ok(capsys, "update", "--repo", str(repo), "--file", "README.md")

    assert sentinel.read_bytes() == b"do not touch\n"
    assert sentinel.stat().st_mtime_ns == before


def test_invalid_markers_are_not_rewritten_by_real_update(
    repo: Path, capsys
) -> None:
    path = repo / "README.md"
    original = f"# Title\n\n{START_MARKER}\nold\n{END_MARKER}\n{START_MARKER}\n{END_MARKER}\n"
    path.write_text(original, encoding="utf-8")

    code = main(["update", "--repo", str(repo), "--file", "README.md"])
    captured = capsys.readouterr()

    assert code == 1
    assert "start marker" in captured.err
    assert path.read_text(encoding="utf-8") == original
