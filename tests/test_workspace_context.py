from pathlib import Path

import pytest

from asynq_team_cli.workspace_context import (
    clear_workspace_context,
    load_workspace_context,
    save_workspace_context,
)


def test_workspace_context_round_trips_through_file(tmp_path: Path) -> None:
    context_path = tmp_path / "context.yaml"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    saved = save_workspace_context(workspace, path=context_path)
    loaded = load_workspace_context(context_path)
    removed = clear_workspace_context(context_path)

    assert saved.workspace == workspace.resolve()
    assert loaded == saved
    assert removed is True
    assert load_workspace_context(context_path) is None


def test_workspace_context_rejects_missing_workspace(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Workspace is not a directory"):
        save_workspace_context(tmp_path / "missing", path=tmp_path / "context.yaml")
