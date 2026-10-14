# Hand-off from ai-kit (2026-10-13): two defects in the router-env sync into pi and opencode

Source: ai-kit's 2026-10-13 audit, report 11 ("pi model and asset sync"). Read-only findings;
nothing in this repo was changed except this note.

## 1. The router API key is written as a literal

`setup/lib/install-agent-clients.sh` (around lines 200–240) writes the router key as a plain
string into both `~/.pi/agent/models.json` (`apiKey`) and `opencode.jsonc`
(`options.apiKey`). Anything that reads, backs up or diffs those files then carries the secret.

Fix: write a reference instead of the value.
- pi: `"apiKey": "!cat <key-file>"` (pi runs `!`-prefixed values as a command) or `"$ENV_VAR"`.
- opencode: `"{file:<key-file>}"` or `"{env:ENV_VAR}"`.

## 2. `enabledModels` is overwritten on every run

`_iac_stage_pi_settings` (line 109) sets
`.enabledModels = [$models[0][] | "router-env/\(.id)"]`, which drops every non-router model the
user enabled in pi (opencode Zen/Go, openai-codex, xAI).

Fix: merge instead of replace. Keep the entries that do not start with `router-env/`, then
append the current router ids, de-duplicated, for example:
`.enabledModels = (((.enabledModels // []) | map(select(startswith("router-env/") | not))) + [$models[0][] | "router-env/\(.id)"] | unique)`.
Note that `unique` sorts the list; use an order-preserving de-dup if the order matters to pi.

## Ownership

router-env stays with llm-env. The opencode→pi model and MCP sync (non-router providers) goes to
tools-installer, which owns pi's install (its todo `2026-10-13-repair-pi-install-and-sync-models`).
