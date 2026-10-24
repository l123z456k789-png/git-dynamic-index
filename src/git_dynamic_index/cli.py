"""Command-line interface for git-dynamic-index."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import __version__, gitstate, hooks
from .block import (
    BlockError,
    StatusData,
    ensure_file,
    inspect_file,
    update_file,
    working_tree_summary,
)
from .gitstate import GitError, RepoState


class CliError(Exception):
    """Raised for invalid CLI paths or arguments."""


def _relative(root: Path, target: Path) -> str:
    return target.relative_to(root).as_posix()


def _resolve_target(root: Path, file_value: str) -> Path:
    candidate = Path(file_value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate

    if candidate.is_symlink():
        raise CliError(f"{candidate} is a symbolic link; refusing to touch it")

    target = candidate.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise CliError(f"{target} is outside the repository {root}") from exc

    if target.suffix.lower() not in {".md", ".markdown"}:
        raise CliError("target file must be Markdown (.md or .markdown)")
    return target


def _format_commit(sha: str | None, subject: str | None) -> str:
    if not sha:
        return "none yet"
    return f"{sha} {subject or '(no message)'}"


def _format_last(state: RepoState) -> str:
    if state.last_sha:
        return _format_commit(state.last_sha, state.last_subject)
    if state.has_commits:
        return "none (only index updates)"
    return "none yet"


def _cmd_init(args: argparse.Namespace) -> int:
    root = gitstate.resolve_repo(args.repo)
    target = _resolve_target(root, args.file)
    state = gitstate.collect(root, exclude=target)
    existed = target.exists()
    result = ensure_file(target, state.to_status_data(), title=root.name)
    relative = _relative(root, target)

    if not result.changed:
        print(f"{relative}: already up to date")
    elif not existed:
        print(f"Created {relative}")
    else:
        print(f"Initialized {relative}")
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    try:
        root = gitstate.resolve_repo(args.repo)
        target = _resolve_target(root, args.file)
        if not target.exists():
            raise CliError(f"{target} does not exist; run gdi init first")
        state = gitstate.collect(root, exclude=target)
        result = update_file(target, state.to_status_data())
    except (GitError, BlockError, CliError, OSError) as exc:
        if args.hook:
            print(f"gdi: {exc}", file=sys.stderr)
            return 0
        raise

    if not args.hook:
        relative = _relative(root, target)
        status = "updated" if result.changed else "already up to date"
        print(f"{relative}: {status}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    root = gitstate.resolve_repo(args.repo)
    target = _resolve_target(root, args.file)
    state = gitstate.collect(root, exclude=target)
    data = state.to_status_data()
    inspection = inspect_file(target, data)
    relative = _relative(root, target)

    print(f"Repo:   {root}")
    print(f"File:   {relative}")
    print(f"Index:  {inspection.detail}")
    print(f"Branch: {state.branch}")
    print(f"HEAD:   {_format_commit(state.head_sha, state.head_subject)}")
    if state.has_commits and state.last_sha != state.head_sha:
        print(f"Last:   {_format_last(state)} (excludes this file)")
    print(f"Working tree: {working_tree_summary(data)}")
    print(f"Hooks:  post-commit: {_hook_label(hooks.inspect(root))}")
    return 0


def _hook_label(state: hooks.HookState) -> str:
    labels = {
        "managed": "managed",
        "missing": "not installed",
        "foreign": "foreign (left untouched)",
        "unsupported": "unsupported",
    }
    label = labels.get(state.status, state.status)
    if state.status == "unsupported":
        return f"{label} ({state.detail})"
    return label


def _cmd_install_hooks(args: argparse.Namespace) -> int:
    root = gitstate.resolve_repo(args.repo)
    target = _resolve_target(root, args.file)
    if not target.exists():
        raise CliError(f"{target} does not exist; run gdi init first")

    data = gitstate.collect(root, exclude=target).to_status_data()
    inspection = inspect_file(target, data)
    if not inspection.valid:
        raise CliError(f"{_relative(root, target)}: {inspection.detail}")

    state = hooks.install(root, target)
    print(f"post-commit hook installed: {state.path}")
    return 0


def _cmd_uninstall_hooks(args: argparse.Namespace) -> int:
    root = gitstate.resolve_repo(args.repo)
    state = hooks.uninstall(root)

    if state.status == "missing":
        print("post-commit hook: not installed")
    elif state.status == "foreign":
        print(f"post-commit hook: foreign; left untouched: {state.path}")
    else:
        print("post-commit hook removed")
    return 0


def _add_repo_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo",
        default=".",
        metavar="PATH",
        help="repository path (default: current directory)",
    )


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    _add_repo_argument(parser)
    parser.add_argument(
        "--file",
        default="README.md",
        metavar="PATH",
        help="Markdown file, relative to the repository root (default: README.md)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gdi",
        description="Update a dynamic Markdown status block from local Git state.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init",
        help="create or extend a Markdown status block",
    )
    _add_common_arguments(init_parser)
    init_parser.set_defaults(func=_cmd_init)

    update_parser = subparsers.add_parser(
        "update",
        help="update the status block from current Git state",
    )
    _add_common_arguments(update_parser)
    update_parser.add_argument(
        "--hook",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    update_parser.set_defaults(func=_cmd_update)

    status_parser = subparsers.add_parser(
        "status",
        help="show repository, file, and index freshness without writing",
    )
    _add_common_arguments(status_parser)
    status_parser.set_defaults(func=_cmd_status)

    install_parser = subparsers.add_parser(
        "install-hooks",
        help="install the managed post-commit hook",
    )
    _add_common_arguments(install_parser)
    install_parser.set_defaults(func=_cmd_install_hooks)

    uninstall_parser = subparsers.add_parser(
        "uninstall-hooks",
        help="remove the managed post-commit hook",
    )
    _add_repo_argument(uninstall_parser)
    uninstall_parser.set_defaults(func=_cmd_uninstall_hooks)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except (GitError, BlockError, CliError, OSError, hooks.HookError) as exc:
        print(f"gdi: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("gdi: interrupted", file=sys.stderr)
        return 130
