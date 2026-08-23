from typer.testing import CliRunner

from asynq_team_cli.main import app


def test_cli_prints_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"


def test_init_creates_runtime_state(tmp_path) -> None:
    result = CliRunner().invoke(app, ["init", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert (tmp_path / ".team" / "config.yaml").is_file()
    assert (tmp_path / ".team" / "team.db").is_file()
    assert "Initialized Asynq Team" in result.output


def test_init_preserves_existing_config(tmp_path) -> None:
    config_path = tmp_path / ".team" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text("project:\n  name: Existing\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["init", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert config_path.read_text(encoding="utf-8") == "project:\n  name: Existing\n"
    assert "Kept existing config" in result.output
