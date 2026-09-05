# Asynq Team CLI

`asynq-team-cli` is the terminal interface for local-first Asynq Team workspaces.

Asynq Team is a local-first operating layer for working with AI agents as a small
software team. It turns agent work into explicit tasks, reviewable artifacts, approval
records, and audit trails instead of leaving important decisions buried in chat history.

The CLI is useful when you want:

- a simple task queue for founder-to-agent work;
- project-local agents, rules, permissions, and model settings under `.team/`;
- a local SQLite runtime instead of a hosted service requirement;
- a clear inbox for questions, blockers, and approvals;
- reviewable Markdown artifacts and audit trails.

The CLI is early and pre-1.0. Command names and output may change while the MVP is still
taking shape.

## Install From Source

The CLI currently depends on a local checkout of `asynq-team-core`.

```bash
git clone git@github.com:asynq-org/asynq-team-core.git
git clone git@github.com:asynq-org/asynq-team-cli.git
cd asynq-team-cli
poetry install
```

Run the CLI through Poetry:

```bash
poetry run team --help
```

## Founder Flow

Create or enter an empty workspace, then initialize and customize it:

```bash
poetry run team onboard --workspace /path/to/workspace
poetry run team workspace use /path/to/workspace
poetry run team doctor
```

Create work for the team:

```bash
poetry run team task create "Prepare the first landing page" \
  --brief "Create a small, reviewable first pass with tests."
```

Start the local worker loop for all configured agents:

```bash
poetry run team worker start
```

The default worker loop runs EA first. EA routes unassigned tasks to the right configured
agent, then each assigned agent can pick up its own queue.

For a background worker:

```bash
poetry run team worker start --daemon
poetry run team worker status
poetry run team worker restart
poetry run team worker stop
```

For smoke tests or manual demos, use one bounded worker pass:

```bash
poetry run team worker run-once
```

Review human attention items:

```bash
poetry run team inbox
poetry run team approve APR-0001
poetry run team deny APR-0002
poetry run team status
```

`team onboard` creates local runtime state under `.team/`, including config, SQLite
state, default agents, default rules, default policy files, and `.gitignore` entries for
local runtime databases and worker files. It can also set the default model and display
names for the generated agents.

Passing `--workspace` always overrides the saved workspace context for that command.

## Agent CLI Primitives

Agents and maintainers that need lower-level task, run, audit, policy, or runner commands
should use [docs/agent-cli-primitives.md](/Users/asynqroot/Work/asynq-team/repos/cli/docs/agent-cli-primitives.md).

## Development

Use Poetry for local development:

```bash
poetry install
poetry run python scripts/check_release_metadata.py
poetry run ruff check .
poetry run pytest
poetry run pip-audit
```

Before committing package-relevant changes, update `project.version` and add a
release-note fragment under `.release-notes/`.
