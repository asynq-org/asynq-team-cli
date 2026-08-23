"""Typer entrypoint for the Asynq Team CLI."""

from pathlib import Path
from typing import Annotated, Optional

from asynq_team_core.database import initialize_database
from asynq_team_core.project import initialize_project
import typer

from asynq_team_cli import __version__


app = typer.Typer(no_args_is_help=True)


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
        Optional[Path],
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
