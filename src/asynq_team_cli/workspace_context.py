"""User-local CLI workspace context."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR_ENV = "ASYNQ_TEAM_CLI_CONFIG_DIR"


@dataclass(frozen=True)
class WorkspaceContext:
    """Persisted CLI workspace context."""

    workspace: Path


def load_workspace_context(path: Path | None = None) -> WorkspaceContext | None:
    """Load the user-local workspace context when configured."""
    context_path = path or get_workspace_context_path()
    if not context_path.is_file():
        return None

    data = yaml.safe_load(context_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise TypeError("Workspace context root must be a mapping.")

    workspace = data.get("workspace")
    if not isinstance(workspace, str) or not workspace.strip():
        raise ValueError("Workspace context workspace must be a non-empty string.")

    return WorkspaceContext(workspace=Path(workspace).expanduser().resolve(strict=False))


def save_workspace_context(workspace: Path, path: Path | None = None) -> WorkspaceContext:
    """Persist the user-local workspace context."""
    resolved_workspace = workspace.expanduser().resolve(strict=False)
    if not resolved_workspace.is_dir():
        raise ValueError(f"Workspace is not a directory: {resolved_workspace}")

    context = WorkspaceContext(workspace=resolved_workspace)
    context_path = path or get_workspace_context_path()
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(
        yaml.safe_dump(_context_to_mapping(context), sort_keys=False),
        encoding="utf-8",
    )
    return context


def clear_workspace_context(path: Path | None = None) -> bool:
    """Remove the user-local workspace context file."""
    context_path = path or get_workspace_context_path()
    if not context_path.exists():
        return False
    context_path.unlink()
    return True


def get_workspace_context_path() -> Path:
    """Return the default context file path."""
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser() / "context.yaml"

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / "asynq-team" / "context.yaml"

    return Path.home() / ".config" / "asynq-team" / "context.yaml"


def _context_to_mapping(context: WorkspaceContext) -> dict[str, Any]:
    return {"workspace": context.workspace.as_posix()}
