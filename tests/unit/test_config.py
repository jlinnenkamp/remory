"""Unit tests for ``remory.config``."""

from __future__ import annotations

from pathlib import Path

import pytest

from remory.config import (
    Config,
    ConfigError,
    load_config,
    resolve_data_dir,
)


def test_load_config_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg == Config()


def test_load_config_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[wat]\nx = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_load_config_rejects_invalid_colour_literal(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[ui]\ncolour = "rainbow"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_load_config_env_override_wins_for_backend_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[backend]\nkind = "claude_code"\n', encoding="utf-8")
    monkeypatch.setenv("REMORY_BACKEND", "anthropic_api")
    cfg = load_config(cfg_path)
    assert cfg.backend.kind == "anthropic_api"


def test_resolve_data_dir_env_beats_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMORY_DATA_DIR", str(tmp_path / "from-env"))
    cfg = Config.model_validate({"paths": {"data_dir": str(tmp_path / "from-config")}})
    assert resolve_data_dir(cfg) == tmp_path / "from-env"


def test_resolve_data_dir_config_beats_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REMORY_DATA_DIR", raising=False)
    cfg = Config.model_validate({"paths": {"data_dir": str(tmp_path / "from-config")}})
    assert resolve_data_dir(cfg) == tmp_path / "from-config"
