"""Typer entrypoint for the Asynq Team CLI."""

from pathlib import Path
from typing import Annotated

import typer
import yaml
from asynq_team_core.config import load_config
from asynq_team_core.database import connect_database, initialize_database
from asynq_team_core.paths import get_project_layout
from asynq_team_core.project import initialize_project
from asynq_team_core.task_service import create_task_with_brief
from asynq_team_core.tasks import get_task, list_tasks

from asynq_team_cli import __version__

app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)
config_app = typer.Typer(no_args_is_help=True)
app.add_typer(task_app, name="task")
app.add_typer(config_app, name="config")


def print_version(value: bool) -> None:
    """Print the CLI version and exit when requested."""
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=print_version, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Run an AI-first company from your terminal."""


@app.command("init")
def init_command(
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory to initialize.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    project_name: Annotated[
        str,
        typer.Option("--project-name", help="Project name written to .team/config.yaml."),
    ] = "Asynq Team",
    overwrite_config: Annotated[
        bool,
        typer.Option("--overwrite-config", help="Replace an existing .team/config.yaml."),
    ] = False,
) -> None:
    """Initialize local Asynq Team runtime state."""
    target_workspace = workspace or Path.cwd()
    initialization = initialize_project(
        target_workspace,
        project_name=project_name,
        overwrite_config=overwrite_config,
    )
    initialize_database(initialization.layout.database_path)

    typer.echo(f"Initialized Asynq Team in {initialization.layout.team_dir}")
    if initialization.created_config:
        typer.echo(f"Created config: {initialization.layout.config_path}")
    else:
        typer.echo(f"Kept existing config: {initialization.layout.config_path}")
    typer.echo(f"Database ready: {initialization.layout.database_path}")


@app.command("status")
def status_command(
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Show local runtime status."""
    layout = get_project_layout(workspace or Path.cwd())

    typer.echo(f"Workspace: {layout.workspace}")
    typer.echo(f"Team dir: {_format_state(layout.team_dir.is_dir())} {layout.team_dir}")
    typer.echo(f"Config: {_format_state(layout.config_path.is_file())} {layout.config_path}")
    typer.echo(f"Database: {_format_state(layout.database_path.is_file())} {layout.database_path}")

    if not layout.config_path.is_file():
        typer.echo("Project: not initialized")
        return

    config = load_config(layout.config_path)
    typer.echo(f"Project: {config.project.name}")
    typer.echo(f"Storage adapter: {config.storage.adapter}")


@config_app.command("show")
def config_show_command(
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Show project-local runtime config."""
    layout = get_project_layout(workspace or Path.cwd())
    if not layout.config_path.is_file():
        typer.echo(f"Config not found: {layout.config_path}", err=True)
        raise typer.Exit(1)

    config = load_config(layout.config_path)
    typer.echo(yaml.safe_dump(config.to_mapping(), sort_keys=False).rstrip())


@task_app.command("create")
def task_create_command(
    title: Annotated[str, typer.Argument(help="Task title.")],
    brief: Annotated[
        str | None,
        typer.Option("--brief", help="Task brief Markdown. Defaults to the title."),
    ] = None,
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    priority: Annotated[str, typer.Option("--priority", help="Task priority.")] = "normal",
    actor_id: Annotated[str, typer.Option("--actor-id", help="Human actor id.")] = "founder",
) -> None:
    """Create a task and its brief artifact."""
    layout = get_project_layout(workspace or Path.cwd())
    created = create_task_with_brief(
        database_path=layout.database_path,
        layout=layout,
        title=title,
        brief_md=brief or title,
        actor_type="human",
        actor_id=actor_id,
        priority=priority,
    )

    typer.echo(f"{created.task.id} {created.task.title}")
    typer.echo(f"Brief: {created.brief.relative_path}")


@task_app.command("list")
def task_list_command(
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, help="Maximum tasks to show.")] = 50,
) -> None:
    """List tasks."""
    layout = get_project_layout(workspace or Path.cwd())
    with connect_database(layout.database_path) as connection:
        tasks = list_tasks(connection, limit=limit)

    if not tasks:
        typer.echo("No tasks.")
        return

    for task in tasks:
        typer.echo(f"{task.id}  {task.status.value}  {task.priority}  {task.title}")


@task_app.command("show")
def task_show_command(
    task_id: Annotated[str, typer.Argument(help="Task id, such as TASK-0001.")],
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace",
            "-w",
            help="Workspace directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Show a task."""
    layout = get_project_layout(workspace or Path.cwd())
    with connect_database(layout.database_path) as connection:
        task = get_task(connection, task_id)

    if task is None:
        typer.echo(f"Task not found: {task_id}", err=True)
        raise typer.Exit(1)

    typer.echo(f"ID: {task.id}")
    typer.echo(f"Title: {task.title}")
    typer.echo(f"Status: {task.status.value}")
    typer.echo(f"Priority: {task.priority}")
    if task.assignee_id:
        typer.echo(f"Assignee: {task.assignee_id}")
    if task.brief_artifact_path:
        typer.echo(f"Brief: {task.brief_artifact_path}")


def _format_state(value: bool) -> str:
    return "ok" if value else "missing"
