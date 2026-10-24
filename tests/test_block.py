from __future__ import annotations

import codecs
import os
import stat

import pytest

import git_dynamic_index.block as block
from git_dynamic_index.block import (
    BlockError,
    ChangeItem,
    FileInspection,
    StatusData,
    ensure_file,
    inspect_file,
    render_body,
    render_document,
    update_file,
)

START = "<!-- AUTO-STATUS-START -->"
END = "<!-- AUTO-STATUS-END -->"


def doc(
    body: str = "old",
    before: str = "# Title\n\n",
    after: str = "\n\nfooter\n",
) -> str:
    return f"{before}{START}\n{body}\n{END}{after}"


def status(**overrides: object) -> StatusData:
    values: dict[str, object] = {
        "branch": "main",
        "commit_sha": "abc1234",
        "commit_subject": "feat: add thing",
        "commit_date": "2026-10-24",
        "upstream": "origin/main",
        "has_commits": True,
    }
    values.update(overrides)
    return StatusData(**values)  # type: ignore[arg-type]


def test_render_body_has_four_status_lines() -> None:
    body = render_body(status())

    assert "## Project status" in body
    assert "- **Branch**: `main`" in body
    assert "- **Working tree (excl. this file)**: clean" in body
    assert (
        "- **Last commit (excl. this file)**: `abc1234` feat: add thing (2026-10-24)"
        in body
    )
    assert "- **Upstream**: `origin/main`" in body
    assert body.endswith("\n")


def test_render_body_uses_requested_newline() -> None:
    body = render_body(status(), newline="\r\n")

    assert "\r\n" in body
    assert "\n" not in body.replace("\r\n", "")


def test_render_body_escapes_commit_subject_and_controls() -> None:
    body = render_body(status(commit_subject="<b>x</b>\nnext\x00"))

    assert "&lt;b&gt;x&lt;/b&gt; next" in body
    assert "<b>" not in body
    assert "\x00" not in body


def test_render_body_marks_no_commit_yet() -> None:
    body = render_body(
        status(
            commit_sha=None,
            commit_subject=None,
            commit_date=None,
            has_commits=False,
        )
    )

    assert "none yet" in body


def test_render_body_marks_only_index_commits() -> None:
    body = render_body(
        status(
            commit_sha=None,
            commit_subject=None,
            commit_date=None,
            has_commits=True,
        )
    )

    assert "none (only index updates)" in body


def test_render_body_marks_upstream_not_set() -> None:
    body = render_body(status(upstream=None))

    assert "- **Upstream**: not set" in body


def test_render_body_keeps_branch_and_upstream_in_code_spans() -> None:
    body = render_body(status(branch="feature/x", upstream="origin/feature/x"))

    assert "- **Branch**: `feature/x`" in body
    assert "- **Upstream**: `origin/feature/x`" in body


def test_render_body_shows_working_tree_counts_and_files() -> None:
    body = render_body(
        status(
            changed=3,
            staged=1,
            unstaged=1,
            untracked=1,
            changes=(
                ChangeItem(status="M", path="src/app.py"),
                ChangeItem(status="??", path="notes/new file.md"),
            ),
        )
    )

    assert (
        "- **Working tree (excl. this file)**: "
        "3 changed (1 staged, 1 unstaged, 1 untracked, 0 conflicts)"
    ) in body
    assert (
        "- **Changed files**: `M` `src/app.py`, `??` `notes/new file.md`"
    ) in body


def test_render_body_shows_conflicts() -> None:
    body = render_body(
        status(
            changed=1,
            conflicts=1,
            changes=(ChangeItem(status="UU", path="file.txt"),),
        )
    )

    assert "1 changed (0 staged, 0 unstaged, 0 untracked, 1 conflicts)" in body


def test_render_body_marks_truncated_file_list() -> None:
    body = render_body(
        status(
            changed=7,
            untracked=7,
            changes=(ChangeItem(status="??", path="a.txt"),),
            omitted=6,
        )
    )

    assert "+6 more; counts are complete" in body


def test_render_body_omits_file_line_when_clean() -> None:
    body = render_body(status())

    assert "**Changed files**" not in body


def test_render_body_escapes_newlines_and_tabs_in_paths() -> None:
    body = render_body(
        status(
            changed=1,
            untracked=1,
            changes=(ChangeItem(status="??", path="line\nbreak\tfile.txt"),),
        )
    )

    assert "`??` `line\\nbreak\\tfile.txt`" in body
    assert "\t" not in body


def test_render_body_uses_wide_code_fence_for_backticks() -> None:
    body = render_body(
        status(
            branch="a`b",
            changed=1,
            untracked=1,
            changes=(ChangeItem(status="??", path="a`b.txt"),),
        )
    )

    assert "``a`b``" in body
    assert "``a`b.txt``" in body


def test_render_document_replaces_only_inner_block() -> None:
    text = doc()

    result = render_document(text, status())

    assert result.startswith("# Title\n\n" + START + "\n")
    assert result.endswith("\n" + END + "\n\nfooter\n")
    assert "old" not in result
    assert "- **Branch**: `main`" in result


def test_render_document_preserves_text_outside_markers_exactly() -> None:
    before = "# Title\n\nkeep trailing spaces  \n"
    after = "\n\nfooter without newline"
    text = doc(before=before, after=after)

    result = render_document(text, status())

    assert result.startswith(before + START)
    assert result.endswith(END + after)


def test_render_document_preserves_crlf() -> None:
    text = doc().replace("\n", "\r\n")

    result = render_document(text, status())

    assert "\r\n" in result
    assert "\n" not in result.replace("\r\n", "")
    assert result.startswith("# Title\r\n\r\n" + START + "\r\n")


def test_render_document_handles_empty_block() -> None:
    text = f"{START}\n{END}\n"

    result = render_document(text, status())

    assert result.startswith(START + "\n")
    assert "## Project status" in result
    assert result.endswith("\n" + END + "\n")


def test_render_document_does_not_add_trailing_newline() -> None:
    text = f"# Title\n\n{START}\nold\n{END}"

    result = render_document(text, status())

    assert result.endswith(END)


def test_render_document_rejects_missing_start_marker() -> None:
    with pytest.raises(BlockError, match="start marker"):
        render_document(f"# Title\n\n{END}\n", status())


def test_render_document_rejects_missing_end_marker() -> None:
    with pytest.raises(BlockError, match="end marker"):
        render_document(f"# Title\n\n{START}\n", status())


def test_render_document_rejects_duplicate_start_markers() -> None:
    text = f"{START}\nold\n{START}\n{END}\n"

    with pytest.raises(BlockError, match="start marker"):
        render_document(text, status())


def test_render_document_rejects_duplicate_end_markers() -> None:
    text = f"{START}\nold\n{END}\n{END}\n"

    with pytest.raises(BlockError, match="end marker"):
        render_document(text, status())


def test_render_document_rejects_reversed_markers() -> None:
    text = f"{END}\nold\n{START}\n"

    with pytest.raises(BlockError, match="before"):
        render_document(text, status())


def test_render_document_ignores_markers_that_are_not_alone_on_a_line() -> None:
    text = f"text {START}\nold\n{END}\n"

    with pytest.raises(BlockError, match="start marker"):
        render_document(text, status())


def test_update_file_writes_and_preserves_bom(tmp_path) -> None:
    path = tmp_path / "README.md"
    path.write_bytes(codecs.BOM_UTF8 + doc().encode("utf-8"))

    result = update_file(path, status())

    assert result.changed is True
    assert result.path == path
    raw = path.read_bytes()
    assert raw.startswith(codecs.BOM_UTF8)
    text = raw.decode("utf-8-sig")
    assert "- **Branch**: `main`" in text
    assert "# Title\n\n" in text
    assert not list(tmp_path.glob(".README.md.*.tmp"))


def test_update_file_is_noop_when_block_is_current(tmp_path, monkeypatch) -> None:
    path = tmp_path / "README.md"
    path.write_text(render_document(doc(), status()), encoding="utf-8")
    before = path.stat().st_mtime_ns

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise AssertionError("update_file wrote an unchanged file")

    monkeypatch.setattr(block.os, "replace", fail_replace)

    result = update_file(path, status())

    assert result.changed is False
    assert path.stat().st_mtime_ns == before
    assert path.read_text(encoding="utf-8") == render_document(doc(), status())


def test_update_file_rejects_invalid_utf8(tmp_path) -> None:
    path = tmp_path / "README.md"
    original = b"\xff\xfe\x00not utf-8"
    path.write_bytes(original)

    with pytest.raises(BlockError, match="UTF-8"):
        update_file(path, status())

    assert path.read_bytes() == original


def test_update_file_rejects_directory(tmp_path) -> None:
    with pytest.raises(BlockError, match="regular file"):
        update_file(tmp_path, status())


def test_update_file_rejects_missing_file(tmp_path) -> None:
    with pytest.raises(BlockError, match="does not exist"):
        update_file(tmp_path / "README.md", status())


def test_update_file_leaves_original_on_marker_error(tmp_path) -> None:
    path = tmp_path / "README.md"
    original = f"# Title\n\n{START}\nold\n"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(BlockError, match="end marker"):
        update_file(path, status())

    assert path.read_text(encoding="utf-8") == original


def test_update_file_keeps_original_when_replace_fails(tmp_path, monkeypatch) -> None:
    path = tmp_path / "README.md"
    original = doc().encode("utf-8")
    path.write_bytes(original)
    calls: list[tuple[object, object]] = []

    def locked_replace(src: object, dst: object) -> None:
        calls.append((src, dst))
        raise PermissionError("locked")

    monkeypatch.setattr(block.os, "replace", locked_replace)
    monkeypatch.setattr(block.time, "sleep", lambda _: None)

    with pytest.raises(BlockError, match="close"):
        update_file(path, status())

    assert path.read_bytes() == original
    assert len(calls) == 3
    assert not list(tmp_path.glob(".README.md.*.tmp"))


def test_update_file_preserves_file_mode(tmp_path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX file modes are not meaningful on Windows")

    path = tmp_path / "README.md"
    path.write_text(doc(), encoding="utf-8")
    path.chmod(0o640)

    update_file(path, status())

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_update_file_preserves_crlf(tmp_path) -> None:
    path = tmp_path / "README.md"
    path.write_bytes(doc().replace("\n", "\r\n").encode("utf-8"))

    update_file(path, status())

    raw = path.read_bytes()
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert b"# Title\r\n\r\n" in raw


def test_update_file_rejects_symlink(tmp_path) -> None:
    target = tmp_path / "target.md"
    target.write_text(doc(), encoding="utf-8")
    link = tmp_path / "README.md"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")

    with pytest.raises(BlockError, match="symbolic link"):
        update_file(link, status())

    assert target.read_text(encoding="utf-8") == doc()


def test_ensure_file_creates_missing_file_with_title_and_block(tmp_path) -> None:
    path = tmp_path / "README.md"

    result = ensure_file(path, status(), title="demo")

    assert result.changed is True
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# demo\n\n")
    assert f"{START}\n" in text
    assert f"{END}\n" in text
    assert "- **Branch**: `main`" in text


def test_ensure_file_appends_markers_preserving_existing_content(tmp_path) -> None:
    path = tmp_path / "README.md"
    original = "# Existing\n\nKeep me.\n"
    path.write_text(original, encoding="utf-8")

    ensure_file(path, status(), title="demo")

    text = path.read_text(encoding="utf-8")
    assert text.startswith(original)
    assert text.count(START) == 1
    assert text.count(END) == 1
    assert "Keep me." in text


def test_ensure_file_is_idempotent(tmp_path) -> None:
    path = tmp_path / "README.md"
    first = ensure_file(path, status(), title="demo")
    before = path.stat().st_mtime_ns

    second = ensure_file(path, status(), title="demo")

    assert first.changed is True
    assert second.changed is False
    assert path.stat().st_mtime_ns == before


def test_ensure_file_preserves_bom_and_crlf(tmp_path) -> None:
    path = tmp_path / "README.md"
    original = "# Existing\r\n\r\nKeep\r\n"
    path.write_bytes(codecs.BOM_UTF8 + original.encode("utf-8"))

    ensure_file(path, status(), title="demo")

    raw = path.read_bytes()
    assert raw.startswith(codecs.BOM_UTF8)
    text = raw.decode("utf-8-sig")
    assert text.startswith(original)
    assert "\n" not in text.replace("\r\n", "")
    assert f"{START}\r\n" in text


def test_ensure_file_rejects_malformed_markers(tmp_path) -> None:
    path = tmp_path / "README.md"
    original = f"# Title\n\n{START}\nold\n"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(BlockError, match="end marker"):
        ensure_file(path, status(), title="demo")

    assert path.read_text(encoding="utf-8") == original


def test_ensure_file_rejects_missing_parent(tmp_path) -> None:
    path = tmp_path / "docs" / "README.md"

    with pytest.raises(BlockError, match="parent"):
        ensure_file(path, status(), title="demo")

    assert not path.exists()
    assert not path.parent.exists()


def test_ensure_file_rejects_directory(tmp_path) -> None:
    with pytest.raises(BlockError, match="regular file"):
        ensure_file(tmp_path, status(), title="demo")


def test_inspect_file_missing(tmp_path) -> None:
    inspection = inspect_file(tmp_path / "README.md", status())

    assert isinstance(inspection, FileInspection)
    assert inspection.exists is False
    assert inspection.valid is False
    assert "init" in inspection.detail


def test_inspect_file_not_initialized(tmp_path) -> None:
    path = tmp_path / "README.md"
    path.write_text("# Title\n", encoding="utf-8")

    inspection = inspect_file(path, status())

    assert inspection.exists is True
    assert inspection.valid is False
    assert "init" in inspection.detail


def test_inspect_file_out_of_date(tmp_path) -> None:
    path = tmp_path / "README.md"
    path.write_text(doc(), encoding="utf-8")

    inspection = inspect_file(path, status())

    assert inspection.valid is True
    assert inspection.current is False
    assert "update" in inspection.detail


def test_inspect_file_up_to_date_does_not_write(tmp_path) -> None:
    path = tmp_path / "README.md"
    path.write_text(render_document(doc(), status()), encoding="utf-8")
    before = path.stat().st_mtime_ns

    inspection = inspect_file(path, status())

    assert inspection.valid is True
    assert inspection.current is True
    assert path.stat().st_mtime_ns == before


def test_inspect_file_invalid_markers(tmp_path) -> None:
    path = tmp_path / "README.md"
    path.write_text(f"{START}\nold\n{START}\n{END}\n", encoding="utf-8")

    inspection = inspect_file(path, status())

    assert inspection.valid is False
    assert "start marker" in inspection.detail


def test_inspect_file_invalid_utf8(tmp_path) -> None:
    path = tmp_path / "README.md"
    path.write_bytes(b"\xff\xfe\x00bad")

    inspection = inspect_file(path, status())

    assert inspection.valid is False
    assert "UTF-8" in inspection.detail


def test_inspect_file_directory(tmp_path) -> None:
    inspection = inspect_file(tmp_path, status())

    assert inspection.exists is True
    assert inspection.valid is False
    assert "regular file" in inspection.detail


def test_inspect_file_symlink(tmp_path) -> None:
    target = tmp_path / "target.md"
    target.write_text(doc(), encoding="utf-8")
    link = tmp_path / "README.md"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")

    inspection = inspect_file(link, status())

    assert inspection.valid is False
    assert "symbolic link" in inspection.detail
