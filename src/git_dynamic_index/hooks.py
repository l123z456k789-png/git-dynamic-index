"""Install and remove the lightweight post-commit hook."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import gitstate

HOOK_NAME = "post-commit"
MANAGED_MARKER = "# Managed by git-dynamic-index. Do not edit."


class HookError(Exception):
    """Raised when a hook cannot be installed or removed safely."""


@dataclass(frozen=True)
class HookState:
    path: Path | None
    status: str
    detail: str


def _hooks_dir(repo: Path) -> Path:
    config = gitstate.run_git(repo, "config", "--get", "core.hooksPath")
    if config.returncode == 0 and config.stdout.strip():
        raise HookError(
            "core.hooksPath is set; gdi will not modify a shared or global "
            "hooks directory"
        )
    if config.returncode not in (0, 1):
        detail = config.stderr.strip() or "unknown error"
        raise HookError(f"could not read core.hooksPath: {detail}")

    result = gitstate.run_git(repo, "rev-parse", "--git-path", "hooks")
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        raise HookError(f"could not resolve the hooks directory: {detail}")

    raw = result.stdout.strip()
    if not raw:
        raise HookError("git returned an empty hooks directory")

    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


def _python_can_import() -> bool:
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import git_dynamic_index"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _command(repo: Path, target: Path) -> str:
    python = shlex.quote(Path(sys.executable).as_posix())
    repo_text = shlex.quote(Path(repo).as_posix())
    target_text = shlex.quote(Path(target).as_posix())
    return (
        f"{python} -m git_dynamic_index update "
        f"--repo {repo_text} --file {target_text} --hook"
    )


def _script(repo: Path, target: Path) -> str:
    return (
        "#!/bin/sh\n"
        f"{MANAGED_MARKER}\n"
        f"{_command(repo, target)} || "
        "echo 'gdi: automatic status update failed; run gdi update manually' >&2\n"
        "exit 0\n"
    )


def _write(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HookError(f"could not create hooks directory {path.parent}: {exc}") from exc

    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".post-commit.",
            suffix=".tmp",
            dir=path.parent,
        )
    except OSError as exc:
        raise HookError(f"could not create a hook temp file: {exc}") from exc

    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())

        try:
            os.chmod(temp_path, 0o755)
        except OSError:
            pass

        try:
            os.replace(temp_path, path)
        except OSError as exc:
            raise HookError(f"could not install hook {path}: {exc}") from exc
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def inspect(repo: Path) -> HookState:
    repo = Path(repo)
    try:
        hooks_dir = _hooks_dir(repo)
    except HookError as exc:
        return HookState(None, "unsupported", str(exc))

    path = hooks_dir / HOOK_NAME
    if not path.exists():
        return HookState(path, "missing", "hook is not installed")
    if not path.is_file():
        return HookState(path, "foreign", "hook path is not a regular file")

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return HookState(path, "unsupported", str(exc))

    if MANAGED_MARKER in content:
        return HookState(path, "managed", "managed by git-dynamic-index")
    return HookState(
        path,
        "foreign",
        "existing hook is not managed by git-dynamic-index",
    )


def install(repo: Path, target: Path) -> HookState:
    repo = Path(repo).resolve()
    target = Path(target).resolve()
    state = inspect(repo)

    if state.status == "unsupported":
        raise HookError(state.detail)
    if state.status == "foreign":
        raise HookError(
            f"{state.path} already exists and is not managed by "
            "git-dynamic-index; refusing to overwrite it.\n"
            f"Add this line manually: {_command(repo, target)}"
        )
    if not _python_can_import():
        raise HookError(
            f"{sys.executable} cannot import git_dynamic_index; "
            "install the package in that Python environment first"
        )

    hooks_dir = _hooks_dir(repo)
    _write(hooks_dir / HOOK_NAME, _script(repo, target))
    return inspect(repo)


def uninstall(repo: Path) -> HookState:
    repo = Path(repo).resolve()
    state = inspect(repo)

    if state.status == "unsupported":
        raise HookError(state.detail)
    if state.status in {"missing", "foreign"}:
        return state

    try:
        state.path.unlink()
    except OSError as exc:
        raise HookError(f"could not remove hook {state.path}: {exc}") from exc
    return HookState(state.path, "removed", "managed hook removed")
