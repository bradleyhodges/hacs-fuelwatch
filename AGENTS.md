# AGENTS.md

# Repository-Specific Instructions

## Project layout

- `custom_components/fuelwatch_wa`: source code for the Home Assistant integration
- `blueprints`: source code for Home Assistant bluebrints
- `dashboard` source code for the inbuilt dashboard, available from the Home Assistant sidebar
- `api-worker`: source code for the Cloudflare Worker which support the integration with FuelWatch data
- `tools`: source code for repository and deployment tools

## Commands

- Install dependencies with `pnpm install`.
- Run formatting with `pnpm format`.

## Repository-specific rules

- Do not add production dependencies without justification.
- Do not modify database schema without adding or updating the corresponding migration.
- Commit each major coherent change using Conventional Commits in accordance with the below instructions.

## Repository Contribution Instructions

These instructions apply to all automated coding agents working in this repository, including Codex and similar agentic development tools.

## Git Commit Expectations

After each major change, the agent must commit the completed changes to the GitHub repository.

A “major change” includes, but is not limited to:

- Adding, removing, or materially changing application functionality.
- Refactoring a meaningful area of the codebase.
- Updating configuration, build tooling, deployment logic, or package structure.
- Adding or materially changing tests.
- Implementing a requested feature, fix, or task milestone.

Minor exploratory edits, temporary debugging changes, or work-in-progress changes do not need to be committed until they form part of a coherent completed change.

## Commit Message Format

All commits must use the Conventional Commits format:

```text
<type>(<scope>): <brief description>
```

Examples:

```text
feat(integration): add fuel price sorting
fix(worker): resolve HTTP timeout
refactor(blueprints/low_fuel): simplify notification config
chore(repo): update dependency lockfile
```

## Scoping Rules

Where a change only affects a particular app, package, service, or logical area, the commit message must include an appropriate scope.

Preferred scope examples:

```text
integration
dashboard
blueprints/low_fuel
blueprints/net_saving
blueprints/price_increase
tools
infra
repo
docs
```

## Commit Hygiene

Before committing, the agent should make reasonable efforts to ensure that:

- The change is complete and coherent.
- Formatting has been applied where applicable.
- Relevant tests, type checks, or lint checks have been run where practical.
- Temporary files, debug statements, and unrelated edits are not included.
- The commit contains only the files relevant to the completed change.

## Agent Behaviour

Agents should treat committing as part of the normal completion process for substantial repository changes.

Unless explicitly instructed otherwise, agents should not leave completed major changes uncommitted.