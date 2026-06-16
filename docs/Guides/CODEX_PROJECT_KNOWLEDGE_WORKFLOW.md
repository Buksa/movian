# Codex Project Knowledge Workflow

This guide explains the local recovery and knowledge system used for this
Movian public checkout and for related plugin projects. It is meant to be the
first document to read after a Codex, editor, terminal, or WSL restart.

The system has two goals:

- make a new Codex chat recover the current project state quickly;
- keep durable research, decisions, experiments, and handoffs outside public
  source repositories unless they are deliberately rewritten as public docs.

## Pieces

The setup is split into small layers.

| Piece | Purpose | Stored In Git? |
|---|---|---|
| `AGENTS.md` | Project rules that Codex should read first. | Yes |
| `support/codex/context.sh` | Portable recovery helper for `check`, `refresh`, and `doctor`. | Yes |
| `.codex/` | Local state, handoff, and machine-specific MCP config. | No |
| `.codegraph/` | Local CodeGraph index. | No |
| `movian-knowledge` | Movian-specific knowledge CLI wrapper. | Tool install |
| `project-knowledge` | Generic project knowledge CLI and engine. | Tool install |
| knowledge vaults | Obsidian-compatible Markdown knowledge stores. | Local Git only |
| aggregate backup | Private backup of all registered vaults. | Private Git only |

The public source repository should not contain machine paths, Obsidian
workspace state, vault contents, credentials, LAN addresses, or local research
drafts. Public docs may describe the workflow, but the actual operational state
lives in the ignored local layer and in the private aggregate backup.

## Repository Types

### Movian Public

This checkout uses the `movian-public` profile. It is registered in the local
project-knowledge config and uses the Movian-specific wrapper:

```sh
movian-knowledge status
movian-knowledge query "flatpak smoke"
movian-knowledge lint
```

The checkout also has the tracked recovery helper:

```sh
./support/codex/context.sh check
./support/codex/context.sh refresh
./support/codex/context.sh doctor
```

`movian-public` may not have a tracked `project-knowledge.toml`; the profile is
kept in the local registry for compatibility with the current public fork
layout.

### Generic Projects And Plugins

Newer projects use a tracked `project-knowledge.toml` manifest and the generic
CLI:

```sh
project-knowledge status
project-knowledge query "ES5 runtime"
project-knowledge lint
```

A Movian plugin project can also use the shared Movian public knowledge as a
read-only provider. The plugin vault records project-specific decisions and
experiments; the shared Movian vault remains the library for reusable Movian,
GLW, build, and smoke-test knowledge.

## Start A New Chat

Open the new Codex chat in the project checkout root. On Windows, prefer the
WSL UNC form for the workspace, for example:

```text
\\wsl$\Ubuntu\path\to\checkout
```

Then ask Codex to do the recovery steps before coding.

For this Movian public checkout:

```sh
./support/codex/context.sh check
movian-knowledge status
project-knowledge backup status
```

`./support/codex/context.sh check` prints a `Knowledge Registry` block. Treat
that block as the source of truth for the working vault, Obsidian vault, and
aggregate backup repository paths. Do not infer the working vault from
`project-knowledge-vaults/vaults/<project-id>/`; that directory is the aggregate
backup copy.

For a project that has `project-knowledge.toml`:

```sh
./support/codex/context.sh check
project-knowledge status
project-knowledge backup status
```

If `support/codex/context.sh` does not exist yet in a project, start with:

```sh
git status --short --branch
project-knowledge status
```

Good first prompt for a fresh chat:

```text
Read AGENTS.md first. Then run the project context check and knowledge status.
Use CodeGraph before broad code exploration. Do not push unless I explicitly
ask.
```

## Daily Commands

### Check Current State

Use this at the start of work or after switching branches:

```sh
./support/codex/context.sh check
```

It prints:

- repository root;
- current branch and HEAD;
- upstream and origin;
- working tree status;
- whether `.codex/STATE.md` matches the current HEAD;
- CodeGraph status when an index exists.

### Refresh Local Handoff

Run this after a merge, branch switch, or important local state change:

```sh
./support/codex/context.sh refresh
```

It updates ignored local files:

- `.codex/STATE.md`;
- `.codex/HANDOFF.md`.

These files are for recovery only. They are not public documentation.

### Doctor

Run this after a Codex update, tool reinstall, or MCP/CodeGraph change:

```sh
./support/codex/context.sh doctor
```

For knowledge tooling:

```sh
movian-knowledge doctor
project-knowledge doctor
```

`doctor` should be treated as an environment check. If it fails, fix the local
tooling before starting a risky branch.

## Query Knowledge

Use queries before broad source exploration, especially when the topic has been
researched before.

Movian public:

```sh
movian-knowledge query "RTMP FFmpeg"
movian-knowledge query "SMB Avahi"
movian-knowledge query "GLW list_x touch scroll"
```

Generic project or plugin:

```sh
project-knowledge query "watch history duration"
project-knowledge query "plugin ES5 Duktape"
```

Query results are starting points. Read the linked wiki pages and their sources
before treating a claim as settled.

## After A Pull Request Merge

The source repository and the knowledge vault are separate. A merge is not
fully finished until the checkout, handoff, and backup are current.

### Movian Public

```sh
git fetch origin --prune
git switch movian6
git pull --ff-only origin movian6

movian-knowledge capture-merge
./support/codex/context.sh refresh
movian-knowledge lint

project-knowledge backup sync
project-knowledge backup status
```

Use `project-knowledge backup sync` only when the user explicitly wants to push
the private aggregate backup. Normal capture and refresh are local.

### Generic Project Or Plugin

```sh
git fetch origin --prune
git switch <default-branch>
git pull --ff-only origin <default-branch>

project-knowledge capture-merge
project-knowledge context refresh
project-knowledge lint

project-knowledge backup sync
project-knowledge backup status
```

Delete merged local and remote topic branches only after the merge is visible
on the default branch and knowledge capture has completed.

## Aggregate Backup

There are two private repositories:

- `project-knowledge`: the CLI, engine, tests, templates, and skill source;
- `project-knowledge-vaults`: aggregate backup for registered knowledge vaults.

Working vaults remain local Git repositories without their own remotes. The
aggregate backup is the only remote backup path for normal vault content.

Check backup status:

```sh
project-knowledge backup status
```

Synchronize all registered vaults:

```sh
project-knowledge backup sync
```

`backup sync` checks vault cleanliness and lint first, copies each vault under
its `project_id`, excludes `.git` and Obsidian machine state, writes a manifest,
commits, and pushes only the aggregate backup repository.

## Restore From Backup

Restore only into an empty vault directory:

```sh
project-knowledge backup restore movian-public \
  --vault <empty-vault-path> \
  --obsidian-vault MovianPublicKnowledge
```

For another project:

```sh
project-knowledge backup restore <project-id> \
  --vault <empty-vault-path> \
  --obsidian-vault <Obsidian-vault-name>
```

Restore initializes local Git for the vault and registers the path in the local
project-knowledge config. It does not create a per-vault remote.

After restore:

```sh
project-knowledge status
project-knowledge lint
project-knowledge query "handoff"
```

## Add Knowledge To Another Project

Use this pattern for a new public-safe project or plugin.

1. Add a tracked manifest:

```toml
schema_version = 1
project_id = "example-project"
title = "Example Project"
kind = "movian-plugin"
visibility = "public-safe"
repository = "Owner/example-project"
default_branch = "master"
shared_knowledge = ["movian-public"]
source_paths = ["AGENTS.md", "README.md", "plugin.json", "docs"]
```

2. Add a short `AGENTS.md` recovery section:

```text
Start with:
./support/codex/context.sh check
project-knowledge status
```

3. Add or copy a portable `support/codex/context.sh`.

4. Bootstrap a local vault:

```sh
project-knowledge bootstrap \
  --vault <vault-path> \
  --obsidian-vault <Obsidian-vault-name>
```

5. Validate:

```sh
project-knowledge status
project-knowledge lint
project-knowledge backup status
```

6. Push project source changes only after normal code review rules. Push vault
backup only with:

```sh
project-knowledge backup sync
```

## CodeGraph

CodeGraph is for source navigation, not durable memory.

Use it when the codebase is too large for simple file reads:

```sh
./support/codex/context.sh check
```

If a CodeGraph index exists, the context check prints whether it is current.
Use CodeGraph before broad searches or cross-file reasoning. Still verify final
answers with actual files and tests.

## Obsidian

The knowledge vaults are Markdown folders that Obsidian can open. Obsidian is
not required for capture or lint; the CLI writes Markdown directly when
Obsidian is closed.

Use Obsidian for:

- browsing decisions and experiments;
- reading handoffs;
- checking backlinks, orphans, and Bases views;
- editing durable notes when the CLI generated content needs human cleanup.

Do not store credentials, sensitive source locations, machine-specific workspace
state, or temporary smoke artifacts in a vault.

## What Not To Do

- Do not commit `.codex/`, `.codegraph/`, Obsidian workspace files, or vault
  directories into a public source repository.
- Do not create separate remote backup repositories per plugin unless the
  workflow is deliberately changed.
- Do not run `project-knowledge sync` for normal project vault backup; it is a
  compatibility command for old profiles.
- Do not push any source branch just because knowledge capture succeeded.
- Do not merge a PR if Codex review did not actually run for the current head
  SHA.
- Do not resolve review threads without either a code fix or a clear written
  explanation.

## Quick Recovery Checklist

When everything feels stale after an update:

```sh
git status --short --branch
./support/codex/context.sh check
movian-knowledge status        # Movian public checkout
project-knowledge status       # project-knowledge.toml checkout
project-knowledge backup status
```

Use the `Knowledge Registry` block from `./support/codex/context.sh check`
before opening vault files. The working vault comes from the local
`project-knowledge` registry; `project-knowledge-vaults/vaults/<project-id>/` is
only the aggregate backup copy.

If the local handoff is stale:

```sh
./support/codex/context.sh refresh
```

If tooling looks broken:

```sh
./support/codex/context.sh doctor
movian-knowledge doctor
project-knowledge doctor
```

If the vault backup is stale and the user explicitly wants to push the private
backup:

```sh
project-knowledge backup sync
```
