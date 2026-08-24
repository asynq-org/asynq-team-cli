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


def test_status_reports_initialized_workspace(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["status", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "Team dir: ok" in result.output
    assert "Config: ok" in result.output
    assert "Database: ok" in result.output
    assert "Project: Asynq Team" in result.output


def test_config_show_prints_runtime_config(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["config", "show", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "project:" in result.output
    assert "name: Asynq Team" in result.output
    assert "storage:" in result.output
    assert "adapter: sqlite" in result.output


def test_config_show_reports_missing_config(tmp_path) -> None:
    result = CliRunner().invoke(app, ["config", "show", "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert "Config not found:" in result.output


def test_task_create_writes_task_and_brief(tmp_path) -> None:
    init_result = CliRunner().invoke(app, ["init", "--workspace", str(tmp_path)])
    assert init_result.exit_code == 0

    result = CliRunner().invoke(
        app,
        [
            "task",
            "create",
            "First task",
            "--brief",
            "Build the first task.",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "TASK-0001 First task" in result.output
    assert (tmp_path / ".team" / "tasks" / "TASK-0001" / "brief.md").read_text(
        encoding="utf-8"
    ) == "Build the first task.\n"


def test_task_list_shows_created_tasks(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["task", "create", "First task", "--workspace", str(tmp_path)])
        .exit_code
        == 0
    )

    result = runner.invoke(app, ["task", "list", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "TASK-0001" in result.output
    assert "First task" in result.output


def test_task_show_reports_missing_task(tmp_path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--workspace", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["task", "show", "TASK-9999", "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert "Task not found: TASK-9999" in result.output
