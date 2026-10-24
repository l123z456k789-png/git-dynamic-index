from __future__ import annotations

import time
from pathlib import Path

import pytest

from git_dynamic_index import gitstate
from git_dynamic_index.gitstate import (
    GitError,
    NotARepositoryError,
    collect,
    parse_status,
    resolve_repo,
)


def test_resolve_repo_from_subdirectory(repo: Path) -> None:
    nested = repo / "nested"
    nested.mkdir()

    assert resolve_repo(nested) == repo.resolve()


def test_resolve_repo_rejects_non_repository(tmp_path: Path) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()

    with pytest.raises(NotARepositoryError, match="Git"):
        resolve_repo(outside)


def test_collect_no_commits(repo: Path) -> None:
    state = collect(repo)

    assert state.has_commits is False
    assert state.branch == "main"
    assert state.head_sha is None
    assert state.head_subject is None
    assert state.last_sha is None
    assert state.changed == 0
    assert state.upstream is None


def test_collect_head_and_branch(repo: Path, write_file, commit, run_git) -> None:
    write_file(repo / "app.py", "print(1)\n")
    commit("feat: app", "app.py")

    state = collect(repo)
    head = run_git(repo, "rev-parse", "--short", "HEAD").stdout.strip()

    assert state.has_commits is True
    assert state.branch == "main"
    assert state.head_sha == head
    assert state.head_subject == "feat: app"
    assert state.last_sha == head
    assert state.last_subject == "feat: app"
    assert state.changed == 0


def test_collect_excludes_target_from_last_commit(
    repo: Path, write_file, commit, run_git
) -> None:
    write_file(repo / "app.py", "1\n")
    commit("feat: app", "app.py")
    write_file(repo / "README.md", "# Title\n")
    commit("docs: readme", "README.md")

    state = collect(repo, exclude=repo / "README.md")
    head = run_git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    previous = run_git(repo, "rev-parse", "--short", "HEAD~1").stdout.strip()

    assert state.head_sha == head
    assert state.head_subject == "docs: readme"
    assert state.last_sha == previous
    assert state.last_subject == "feat: app"
    assert state.to_status_data().commit_sha == previous


def test_collect_excludes_target_from_status(repo: Path, write_file, commit) -> None:
    write_file(repo / "app.py", "1\n")
    commit("feat: app", "app.py")
    write_file(repo / "README.md", "# Title\n")
    write_file(repo / "app.py", "2\n")

    state = collect(repo, exclude=repo / "README.md")

    assert state.changed == 1
    assert state.unstaged == 1
    assert state.untracked == 0
    assert [change.path for change in state.changes] == ["app.py"]


def test_collect_counts_staged_unstaged_and_untracked(
    repo: Path, write_file, commit, run_git
) -> None:
    write_file(repo / "staged.txt", "a\n")
    write_file(repo / "modified.txt", "b\n")
    commit("base", "staged.txt", "modified.txt")
    write_file(repo / "staged.txt", "a2\n")
    write_file(repo / "modified.txt", "b2\n")
    run_git(repo, "add", "staged.txt")
    write_file(repo / "untracked file.txt", "c\n")

    state = collect(repo)

    assert state.changed == 3
    assert state.staged == 1
    assert state.unstaged == 1
    assert state.untracked == 1
    assert state.conflicts == 0
    statuses = {change.path: change.status for change in state.changes}
    assert statuses["staged.txt"] == "M "
    assert statuses["modified.txt"] == " M"
    assert statuses["untracked file.txt"] == "??"


def test_collect_file_names_with_spaces_and_chinese(repo: Path, write_file) -> None:
    write_file(repo / "中文 文件.txt", "x\n")

    state = collect(repo)

    assert state.untracked == 1
    assert state.changes[0].path == "中文 文件.txt"


def test_collect_file_name_with_newline(repo: Path, write_file) -> None:
    name = "line\nbreak.txt"
    try:
        write_file(repo / name, "x\n")
    except OSError as exc:
        pytest.skip(f"newline filename unavailable: {exc}")

    state = collect(repo)

    assert state.untracked == 1
    assert state.changes[0].path == name


def test_collect_parses_rename(repo: Path, write_file, commit, run_git) -> None:
    write_file(repo / "old name.txt", "x\n")
    commit("base", "old name.txt")
    run_git(repo, "mv", "old name.txt", "new name.txt")

    state = collect(repo)

    assert state.changed == 1
    assert state.staged == 1
    assert state.unstaged == 0
    assert state.changes[0].status == "R "
    assert state.changes[0].path == "new name.txt"
    assert state.changes[0].original_path == "old name.txt"


def test_collect_parses_conflict(repo: Path, write_file, commit, run_git) -> None:
    write_file(repo / "file.txt", "base\n")
    commit("base", "file.txt")
    run_git(repo, "checkout", "-b", "other")
    write_file(repo / "file.txt", "other\n")
    commit("other", "file.txt")
    run_git(repo, "checkout", "main")
    write_file(repo / "file.txt", "main\n")
    commit("main", "file.txt")
    result = run_git(repo, "merge", "--no-edit", "other", check=False)
    assert result.returncode != 0

    state = collect(repo)

    assert state.changed == 1
    assert state.conflicts == 1
    assert state.staged == 0
    assert state.unstaged == 0
    assert state.changes[0].status == "UU"


def test_collect_limits_displayed_files_but_keeps_counts(repo: Path, write_file) -> None:
    for index in range(7):
        write_file(repo / f"file{index}.txt", "x\n")

    state = collect(repo)
    data = state.to_status_data()

    assert state.changed == 7
    assert state.untracked == 7
    assert len(state.changes) == 7
    assert len(data.changes) == 5
    assert data.omitted == 2
    assert data.changed == 7


def test_collect_detached_head(repo: Path, write_file, commit, run_git) -> None:
    write_file(repo / "app.py", "1\n")
    commit("feat: app", "app.py")
    run_git(repo, "checkout", "--detach", "HEAD")

    state = collect(repo)

    assert state.branch == "detached"
    assert state.has_commits is True
    assert state.head_sha is not None


def test_collect_upstream_name(
    repo: Path, tmp_path: Path, write_file, commit, run_git
) -> None:
    write_file(repo / "app.py", "1\n")
    commit("feat: app", "app.py")
    bare = tmp_path / "remote.git"
    run_git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    run_git(repo, "remote", "add", "origin", str(bare))
    run_git(repo, "push", "-u", "origin", "main")

    state = collect(repo)

    assert state.upstream == "origin/main"


def test_collect_raises_when_status_fails(repo: Path, monkeypatch) -> None:
    real_run = gitstate._run_git

    def fake_run(root: Path, *args: str) -> gitstate.GitResult:
        if args and args[0] == "status":
            return gitstate.GitResult(1, "", "boom")
        return real_run(root, *args)

    monkeypatch.setattr(gitstate, "_run_git", fake_run)

    with pytest.raises(GitError, match="status"):
        collect(repo)


def test_collect_does_not_refresh_index(repo: Path, write_file, commit) -> None:
    write_file(repo / "app.py", "1\n")
    commit("feat: app", "app.py")
    write_file(repo / "app.py", "2\n")
    index = repo / ".git" / "index"
    before = index.stat().st_mtime_ns
    time.sleep(0.02)

    collect(repo)

    assert index.stat().st_mtime_ns == before


def test_parse_status_handles_spaces_and_newlines() -> None:
    changes = parse_status(" M 中文 文件.txt\0?? line\nbreak.txt\0")

    assert len(changes) == 2
    assert changes[0].status == " M"
    assert changes[0].path == "中文 文件.txt"
    assert changes[1].status == "??"
    assert changes[1].path == "line\nbreak.txt"


def test_parse_status_handles_rename() -> None:
    changes = parse_status("R  new name.txt\0old name.txt\0")

    assert len(changes) == 1
    assert changes[0].status == "R "
    assert changes[0].path == "new name.txt"
    assert changes[0].original_path == "old name.txt"


def test_parse_status_rejects_malformed_record() -> None:
    with pytest.raises(GitError):
        parse_status("bad\0")
