"""Unit tests for ``remory.paths``."""

from __future__ import annotations

from pathlib import Path

import pytest

from remory import paths


def test_data_dir_honours_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "elsewhere"))
    assert paths.data_dir() == tmp_path / "elsewhere"


def test_data_dir_falls_back_to_platformdirs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REMORY_DATA_DIR", raising=False)
    result = paths.data_dir()
    assert isinstance(result, Path)
    # Don't pin OS-specific shape, but at least confirm "remory" is in the path.
    assert "remory" in str(result)


def test_topic_dir_rejects_path_traversal() -> None:
    with pytest.raises(ValueError, match=r"\.\."):
        paths.topic_dir("..")


def test_topic_dir_rejects_separators() -> None:
    with pytest.raises(ValueError, match="separator"):
        paths.topic_dir("a/b")
    with pytest.raises(ValueError, match="separator"):
        paths.topic_dir("a\\b")


def test_topic_dir_rejects_uppercase_and_empty() -> None:
    with pytest.raises(ValueError):
        paths.topic_dir("Foo")
    with pytest.raises(ValueError):
        paths.topic_dir("")


def test_topic_dir_accepts_lowercase_kebab_and_snake(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path))
    assert paths.topic_dir("job-profile") == tmp_path / "topics" / "job-profile"
    assert paths.topic_dir("foo_bar") == tmp_path / "topics" / "foo_bar"
    assert paths.topic_dir("a1") == tmp_path / "topics" / "a1"


def test_data_dir_refuses_env_override_pointing_inside_source_tree_when_running_from_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REMORY_DATA_DIR pointing inside the in-tree checkout raises loudly (ADR 0012)."""
    repo_root = paths._repo_root_if_in_tree()
    if repo_root is None:
        pytest.skip("test only applies to src/-layout checkouts (editable install)")
    inside_repo = repo_root / "tests" / "_tmp_data_dir_guard_check"
    monkeypatch.setenv("REMORY_DATA_DIR", str(inside_repo))
    with pytest.raises(paths.DataDirInsideSourceTreeError) as excinfo:
        paths.data_dir()
    assert excinfo.value.candidate == inside_repo
    assert excinfo.value.repo_root == repo_root


def test_refuse_if_inside_source_tree_is_no_op_for_installed_copies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the running copy is not src/-layout, the guard never fires."""
    monkeypatch.setattr(paths, "_repo_root_if_in_tree", lambda: None)
    paths.refuse_if_inside_source_tree(tmp_path)


def test_refuse_if_inside_source_tree_allows_paths_outside_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tmp_path is outside any repo; the guard returns without raising even when in-tree."""
    monkeypatch.setattr(paths, "_repo_root_if_in_tree", lambda: tmp_path / "fake-repo-root")
    paths.refuse_if_inside_source_tree(tmp_path / "elsewhere")
