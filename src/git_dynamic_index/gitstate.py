"""Read-only Git state collection for the generated status block."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .block import ChangeItem, StatusData

MAX_DISPLAYED_CHANGES = 5
CONFLICT_CODES = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})


class GitError(Exception):
    """Raised when Git state cannot be collected reliably."""


class NotARepositoryError(GitError):
    """Raised when the target is not a Git working tree."""


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class FileChange:
    status: str
    path: str
    original_path: str | None = None


@dataclass(frozen=True)
class RepoState:
    root: Path
    branch: str
    has_commits: bool
    head_sha: str | None
    head_subject: str | None
    head_date: str | None
    last_sha: str | None
    last_subject: str | None
    last_date: str | None
    upstream: str | None
    changed: int
    staged: int
    unstaged: int
    untracked: int
    conflicts: int
    changes: tuple[FileChange, ...]

    def to_status_data(self) -> StatusData:
        displayed = tuple(
            ChangeItem(status=change.status, path=change.path)
            for change in self.changes[:MAX_DISPLAYED_CHANGES]
        )
        return StatusData(
            branch=self.branch,
            commit_sha=self.last_sha,
            commit_subject=self.last_subject,
            commit_date=self.last_date,
            upstream=self.upstream,
            has_commits=self.has_commits,
            changed=self.changed,
            staged=self.staged,
            unstaged=self.unstaged,
            untracked=self.untracked,
            conflicts=self.conflicts,
            changes=displayed,
            omitted=self.changed - len(displayed),
        )


def _run_git(root: Path, *args: str) -> GitResult:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0] if args else ''} timed out") from exc
    return GitResult(result.returncode, result.stdout, result.stderr)


def run_git(root: Path | str, *args: str) -> GitResult:
    """Run a read-only Git command in a specific repository."""
    return _run_git(Path(root), *args)


def _require_ok(result: GitResult, action: str) -> str:
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise GitError(f"git {action} failed: {detail}")
    return result.stdout


def resolve_repo(start: Path | str) -> Path:
    """Resolve a directory inside a Git worktree to the worktree root."""
    start = Path(start).expanduser()
    if not start.exists():
        raise NotARepositoryError(f"{start} does not exist")
    if not start.is_dir():
        raise NotARepositoryError(f"{start} is not a directory")

    result = _run_git(start, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise NotARepositoryError(f"{start} is not inside a Git working tree")
    root_text = result.stdout.strip()
    if not root_text:
        raise NotARepositoryError(f"{start} is not inside a Git working tree")

    return Path(root_text).resolve()


def parse_status(raw: str) -> tuple[FileChange, ...]:
    """Parse `git status --porcelain=v1 -z` output without path quoting."""
    fields = raw.split("\0")
    if fields and fields[-1] == "":
        fields.pop()

    changes: list[FileChange] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        if len(field) < 3 or field[2] != " ":
            raise GitError(f"unexpected git status record: {field!r}")
        status = field[:2]
        path = field[3:]
        if not path:
            raise GitError("git status returned an empty path")

        original_path: str | None = None
        if "R" in status or "C" in status:
            index += 1
            if index >= len(fields):
                raise GitError("truncated rename/copy record in git status")
            original_path = fields[index]

        changes.append(
            FileChange(status=status, path=path, original_path=original_path)
        )
        index += 1

    return tuple(changes)


def _commit_info(
    root: Path,
    rev: str,
    exclude_rel: str | None,
) -> tuple[str, str, str] | None:
    args = ["log", "-1", "--format=%h%x00%s%x00%cs", rev]
    if exclude_rel is not None:
        args += ["--", ".", f":(exclude,literal){exclude_rel}"]

    result = _run_git(root, *args)
    text = _require_ok(result, "log").strip()
    if not text:
        return None

    parts = text.split("\x00", 2)
    if len(parts) != 3:
        raise GitError("unexpected git log output")
    return parts[0], parts[1], parts[2]


def _upstream(root: Path, branch: str) -> str | None:
    if branch == "detached":
        return None
    result = _run_git(
        root,
        "for-each-ref",
        "--format=%(upstream:short)",
        f"refs/heads/{branch}",
    )
    text = _require_ok(result, "for-each-ref").strip()
    return text or None


def collect(root: Path | str, exclude: Path | str | None = None) -> RepoState:
    """Collect all status fields, excluding one target file when requested."""
    root_path = Path(root).expanduser().resolve()
    if not (root_path / ".git").exists():
        top = _require_ok(
            _run_git(root_path, "rev-parse", "--show-toplevel"),
            "rev-parse --show-toplevel",
        ).strip()
        if not top:
            raise NotARepositoryError(f"{root_path} is not inside a Git working tree")
        root_path = Path(top).resolve()

    exclude_rel: str | None = None
    if exclude is not None:
        exclude_path = Path(exclude).expanduser().resolve()
        try:
            exclude_rel = exclude_path.relative_to(root_path).as_posix()
        except ValueError as exc:
            raise GitError(
                f"target {exclude_path} is outside repository {root_path}"
            ) from exc

    status_args = [
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--",
        ".",
    ]
    if exclude_rel is not None:
        status_args.append(f":(exclude,literal){exclude_rel}")
    status_result = _run_git(root_path, *status_args)
    changes = parse_status(_require_ok(status_result, "status"))
    ordered_changes = tuple(sorted(changes, key=lambda item: (item.path, item.status)))

    branch = _require_ok(
        _run_git(root_path, "branch", "--show-current"),
        "branch --show-current",
    ).strip() or "detached"

    head_check = _run_git(root_path, "rev-parse", "--verify", "--quiet", "HEAD")
    if head_check.returncode == 0:
        has_commits = True
        head_info = _commit_info(root_path, "HEAD", None)
        if head_info is None:
            raise GitError("git log returned no HEAD commit")
        if exclude_rel is None:
            last_info = head_info
        else:
            last_info = _commit_info(root_path, "HEAD", exclude_rel)
    elif head_check.returncode == 1:
        has_commits = False
        head_info = None
        last_info = None
    else:
        detail = head_check.stderr.strip() or "unknown error"
        raise GitError(f"git rev-parse HEAD failed: {detail}")

    head_sha, head_subject, head_date = head_info or (None, None, None)
    last_sha, last_subject, last_date = last_info or (None, None, None)

    untracked = sum(1 for item in ordered_changes if item.status == "??")
    conflicts = sum(1 for item in ordered_changes if item.status in CONFLICT_CODES)
    staged = sum(
        1
        for item in ordered_changes
        if item.status not in CONFLICT_CODES and item.status[0] not in " ?!"
    )
    unstaged = sum(
        1
        for item in ordered_changes
        if item.status not in CONFLICT_CODES and item.status[1] not in " ?!"
    )

    return RepoState(
        root=root_path,
        branch=branch,
        has_commits=has_commits,
        head_sha=head_sha,
        head_subject=head_subject,
        head_date=head_date,
        last_sha=last_sha,
        last_subject=last_subject,
        last_date=last_date,
        upstream=_upstream(root_path, branch),
        changed=len(ordered_changes),
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        conflicts=conflicts,
        changes=ordered_changes,
    )
