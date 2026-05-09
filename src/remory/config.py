"""Configuration loading and resolution for Remory.

The on-disk config lives at ``$XDG_CONFIG_HOME/remory/config.toml`` (or
``$REMORY_CONFIG_FILE`` when set). It is parsed with stdlib :mod:`tomllib`,
validated with Pydantic v2, and then env overrides are applied.

The single resolver for the data directory is :func:`resolve_data_dir`,
which composes env > config > XDG-default precedence. :func:`remory.paths.data_dir`
intentionally does *not* know about the config file.
"""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from remory import paths

__all__ = [
    "BackendConfig",
    "Config",
    "ConfigError",
    "PathsConfig",
    "SleepConfig",
    "UIConfig",
    "load_config",
    "resolve_data_dir",
]

_log = logging.getLogger("remory.config")


class BackendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["claude_code", "anthropic_api"] = "claude_code"


class UIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emoji: bool = False
    colour: Literal["auto", "always", "never"] = "auto"


class SleepConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_suggest_at_session_end: bool = True


class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Empty string means "use the XDG default"; we keep the sentinel rather
    # than ``Optional[Path]`` so the on-disk TOML round-trips cleanly.
    data_dir: str = ""


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: BackendConfig = BackendConfig()
    ui: UIConfig = UIConfig()
    sleep: SleepConfig = SleepConfig()
    paths: PathsConfig = PathsConfig()


class ConfigError(Exception):
    """Wraps a Pydantic ``ValidationError`` with the source path for context."""

    def __init__(self, source: Path | None, error: ValidationError) -> None:
        self.source = source
        self.validation_error = error
        suffix = f" (from {source})" if source is not None else ""
        super().__init__(f"invalid Remory config{suffix}: {error}")


def _resolve_config_path(path: Path | None) -> Path:
    """Pick the config path to read from, honouring ``REMORY_CONFIG_FILE``."""
    if path is not None:
        return path
    env = os.environ.get("REMORY_CONFIG_FILE")
    if env:
        return Path(env)
    return paths.config_dir() / "config.toml"


def _apply_env_overrides(cfg: Config) -> Config:
    """Apply ``REMORY_*`` env overrides on top of ``cfg``.

    Currently only ``REMORY_BACKEND`` -> ``backend.kind`` is supported. We
    re-validate via ``model_copy(update=...)`` so the literal constraint
    surfaces as a ``ConfigError`` rather than silently accepting garbage.
    """
    backend_env = os.environ.get("REMORY_BACKEND")
    if backend_env:
        try:
            new_backend = cfg.backend.model_copy(update={"kind": backend_env})
            BackendConfig.model_validate(new_backend.model_dump())
        except ValidationError as exc:
            raise ConfigError(None, exc) from exc
        cfg = cfg.model_copy(update={"backend": new_backend})
    return cfg


def load_config(path: Path | None = None) -> Config:
    """Read the on-disk config and apply env overrides.

    Args:
        path: explicit override; otherwise ``$REMORY_CONFIG_FILE`` or
            ``paths.config_dir() / "config.toml"``.

    Returns:
        A validated :class:`Config`. If the file does not exist, returns
        :class:`Config` defaults (with env overrides applied).

    Raises:
        ConfigError: if the file is present but invalid.
    """
    cfg_path = _resolve_config_path(path)
    if not cfg_path.exists():
        _log.debug("no config file at %s; using defaults", cfg_path)
        return _apply_env_overrides(Config())

    raw = cfg_path.read_bytes()
    parsed = tomllib.loads(raw.decode("utf-8"))
    try:
        cfg = Config.model_validate(parsed)
    except ValidationError as exc:
        raise ConfigError(cfg_path, exc) from exc
    return _apply_env_overrides(cfg)


def resolve_data_dir(cfg: Config) -> Path:
    """Resolve the effective data directory.

    Precedence: ``$REMORY_DATA_DIR`` env > ``cfg.paths.data_dir`` (if non-empty)
    > ``paths.data_dir()`` (the XDG default).
    """
    env = os.environ.get("REMORY_DATA_DIR")
    if env:
        return Path(env)
    if cfg.paths.data_dir:
        return Path(cfg.paths.data_dir)
    return paths.data_dir()
