"""Typer entrypoint for the Asynq Team CLI."""

from typing import Annotated

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

