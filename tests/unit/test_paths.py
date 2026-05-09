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
