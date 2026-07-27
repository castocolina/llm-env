# Agent Inference Checks and Client Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify deterministic API inference for every enabled model, add opt-in live agent checks, and document only tested local-client integrations.

**Architecture:** `check-server.sh` remains a deterministic local curl contract test. A new `check-with-agents.sh` owns opt-in Internet-dependent agent checks, obtains its model list from the running API, and creates a mode-700 workspace per run. Client research is an explicit gate: its evidence determines which precise client snippets appear in `QUICK_START.md`.

**Tech Stack:** Bash, shellcheck, curl, jq, Mike Farah yq v4, pytest, llama.cpp OpenAI-compatible API, Pi, OpenCode, Open-Meteo, ExchangeRate-API.

## Global Constraints

- Support Bazzite/Fedora only; all output is English.
- Invoke Python only as `uv run llmenv.py <subcommand>`.
- The local API key must never appear in a command line, prompt, log, test artifact, generated client config, or documentation example.
- `make check-server` must remain local and deterministic. Only `make check-with-agents` may require outbound Internet.
- The agent matrix is every detected client × every API-listed model × weather and FX prompts. A tested row failure fails the target; missing agents are reported as skipped.
- Read and test Antigravity, VS Code extensions, Codex, and Claude Code in that order before publishing their setup instructions. Publish only verified integrations.
- Every shell change requires `make validate`; Python changes require `make validate && make test`.
- Makefile targets longer than three lines delegate to a shell script.

---

## File Structure

| File | Responsibility |
|---|---|
| `check-server.sh` | Local health, authentication, model-list, and normalized deterministic inference contract |
| `check-with-agents.sh` | Opt-in, redacted live-source agent matrix and source comparison |
| `Makefile` | `check-with-agents` target and help entry |
| `tests/test_shell.py` | Stubbed shell-contract tests for both checks; no real API key, agent, or Internet call |
| `docs/client-compatibility.md` | Dated evidence and results for required client research order |
| `README.md` | Project orientation, lifecycle, command list, compatibility summary, Quick Start link |
| `QUICK_START.md` | Secure curl, DNS, and verified client examples |

## Task 1: Deterministic authenticated curl inference

**Files:**
- Modify: `check-server.sh`, `tests/test_shell.py`

**Interfaces:**
- `normalize_ready(value: string)` is a Bash pipeline that lowercases and strips leading/trailing whitespace and ASCII punctuation.
- Consumes enabled aliases from `.models[] | select(.enabled) | .alias` and an auth curl config file.
- Produces one PASS/FAIL result per alias, where only normalized `ready` passes.

- [ ] **Step 1: Add a failing check-server harness test**

Add `run_check_server(tmp_path, completion_body)` that stubs curl, jq, and yq, writes a no-secret config, and records curl calls. Add:

```python
def test_check_server_requires_normalized_ready_for_every_enabled_model(tmp_path):
    result, calls = run_check_server(
        tmp_path,
        {"gemma4": " Ready!\n", "ornith": "not ready"},
    )
    assert result.returncode != 0
    assert "gemma4: returned ready" in result.stdout
    assert "ornith: expected ready" in result.stderr
    assert calls.read_text().count("/v1/chat/completions") == 3
```

The three completion calls are one invalid-key auth probe plus one deterministic inference per enabled alias.

- [ ] **Step 2: Verify the test fails for the current nonempty-content behavior**

Run: `uv run --with pytest pytest tests/test_shell.py::test_check_server_requires_normalized_ready_for_every_enabled_model -v`

Expected: FAIL because `not ready` is currently accepted as nonempty assistant content.

- [ ] **Step 3: Implement strict response extraction and normalization**

Replace the `content/reasoning/finish` acceptance branch in `check-server.sh` with this behavior:

```bash
content="$(jq -r '.choices[0].message.content // empty' <<<"$response")"
normalized="$(printf '%s' "$content" | tr '[:upper:]' '[:lower:]' | \
    sed -E 's/^[[:space:][:punct:]]+//; s/[[:space:][:punct:]]+$//')"
if [ "$normalized" = ready ]; then
    ok "${alias}: returned ready"
elif [ -z "$content" ]; then
    bad "${alias}: empty assistant content"
else
    bad "${alias}: expected ready, got $(printf '%.80s' "$content")"
fi
```

Set `max_tokens: 16` and use the exact prompt `Reply with exactly: ready`. Preserve `stream: false`, curl config-file authentication, and bounded request timeout.

- [ ] **Step 4: Add and run the success case**

Add a second invocation using `{"gemma4": "READY.", "ornith": " ready "}` and assert exit 0. Run:

`uv run --with pytest pytest tests/test_shell.py -k check_server -v`

Expected: PASS.

- [ ] **Step 5: Run required verification and commit**

Run: `make validate && make test`

```bash
git add check-server.sh tests/test_shell.py
git commit -m "test: require deterministic server inference responses"
```

## Task 2: Agent check helpers and safe matrix shell contract

**Files:**
- Create: `check-with-agents.sh`
- Modify: `Makefile`, `tests/test_shell.py`

**Interfaces:**
- `check-with-agents.sh` accepts no secret arguments.
- `fetch_models(auth_conf, base) -> newline-delimited aliases` reads `/v1/models`.
- `source_weather() -> JSON` and `source_fx() -> JSON` emit validated public-source snapshots.
- `run_agent(client, alias, check_name, prompt, snapshot) -> JSON` is the one client-specific dispatch point; Task 3 implements its Pi/OpenCode cases.
- Emits redacted rows: `PASS|FAIL|SKIP client=<name> model=<alias> check=<weather|fx> reason=<text>`.

- [ ] **Step 1: Add failing target and no-client tests**

Add tests:

```python
def test_make_help_lists_check_with_agents():
    assert "make check-with-agents" in (ROOT / "Makefile").read_text()


def test_agent_check_fails_when_no_supported_client_is_installed(tmp_path):
    result, _, _ = run_agent_check(tmp_path, clients={})
    assert result.returncode != 0
    assert "fail no supported agent is installed" in result.stderr
```

`run_agent_check` must use an isolated PATH of supplied executable stubs, a temporary config with `server.port`, a fake models endpoint, and fake public-source responses.

- [ ] **Step 2: Verify the tests fail**

Run: `uv run --with pytest pytest tests/test_shell.py -k 'agent_check or check_with_agents' -v`

Expected: FAIL because the target and script do not exist.

- [ ] **Step 3: Implement safe initialization and source fetches**

In `check-with-agents.sh`, source `lib.sh`, require `curl jq yq`, create `workspace="$(mktemp -d)"`, then `chmod 700 "$workspace"` and `trap 'rm -rf "$workspace"' EXIT`. Create mode-600 curl config files exactly as `check-server.sh` does.

Use:

```bash
weather_url='https://api.open-meteo.com/v1/forecast?latitude=-33.4489&longitude=-70.6693&current=temperature_2m,weather_code&timezone=America%2FSantiago'
fx_url='https://open.er-api.com/v6/latest/USD'
```

Validate and reduce source data with:

```bash
curl -fsS --max-time 20 "$weather_url" | jq -ce \
  '{source_url: $url, source_timestamp: .current.time,
    temperature_2m: .current.temperature_2m, weather_code: .current.weather_code}' \
  --arg url "$weather_url"
```

```bash
curl -fsS --max-time 20 "$fx_url" | jq -ce \
  '{source_url: $url, source_timestamp: .time_last_update_utc, usd_to_clp: .rates.CLP}' \
  --arg url "$fx_url"
```

Fetch models using the auth config and fail if the resulting alias list is empty.

- [ ] **Step 4: Implement skip policy and matrix construction**

Detect clients with `command -v pi` and `command -v opencode`. Print one `skip` row for each missing executable. Exit nonzero if neither exists.

For present clients, for every alias and each named snapshot, construct a prompt that requires one JSON object with `source_url`, `source_timestamp`, plus `temperature_2m`/`weather_code` or `usd_to_clp`, and instructs the agent to fetch the source with a shell network command. Call `run_agent`; until Task 3 adds a client case, its default must return nonzero with `unsupported client`.

- [ ] **Step 5: Add the Makefile target and verify shell tests**

Add `check-with-agents` to `.PHONY`, `make help`, and a one-line body:

```make
check-with-agents:
	@bash check-with-agents.sh
```

Run: `uv run --with pytest pytest tests/test_shell.py -k 'agent_check or check_with_agents' -v`

Expected: PASS for no-client/target behavior.

- [ ] **Step 6: Verify and commit**

Run: `make validate && make test`

```bash
git add check-with-agents.sh Makefile tests/test_shell.py
git commit -m "feat: add opt-in agent inference check scaffold"
```

## Task 3: Pi and OpenCode non-interactive adapters

**Files:**
- Modify: `check-with-agents.sh`, `tests/test_shell.py`

**Interfaces:**
- `run_agent pi <alias> <check> <prompt> <snapshot>` writes only `${workspace}/pi/models.json` and invokes Pi JSON mode.
- `run_agent opencode <alias> <check> <prompt> <snapshot>` writes only `${workspace}/opencode/opencode.jsonc` and invokes OpenCode JSON mode.
- Both produce a single parsed agent JSON object on stdout or a bounded redacted error on stderr.

- [ ] **Step 1: Add failing Pi and OpenCode command-construction tests**

Add tests that provide executable stubs which return valid JSON evidence and record arguments:

```python
def test_agent_check_runs_pi_for_each_model_and_live_check(tmp_path):
    result, calls, workspaces = run_agent_check(tmp_path, clients={"pi": valid_pi_stub})
    assert result.returncode == 0
    assert count_rows(result.stdout, "PASS client=pi") == 4
    assert "-p" in calls.read_text() and "--mode json" in calls.read_text()
    assert "llm-env/gemma4" in calls.read_text()
    assert all("api_key" not in path.read_text().lower() for path in workspaces)


def test_agent_check_runs_opencode_for_each_model_and_live_check(tmp_path):
    result, calls, _ = run_agent_check(tmp_path, clients={"opencode": valid_opencode_stub})
    assert result.returncode == 0
    assert count_rows(result.stdout, "PASS client=opencode") == 4
    assert "run --format json --model llm-env/gemma4" in calls.read_text()
```

Use two aliases in the fake `/v1/models` response, so four rows per client prove two checks per model.

- [ ] **Step 2: Verify the tests fail**

Run: `uv run --with pytest pytest tests/test_shell.py -k 'runs_pi or runs_opencode' -v`

Expected: FAIL because client adapters return unsupported.

- [ ] **Step 3: Implement the Pi adapter**

Write `${workspace}/pi/models.json` with no literal key:

```json
{
  "providers": {
    "llm-env": {
      "baseUrl": "http://llm.local:<port>/v1",
      "api": "openai-completions",
      "apiKey": "!yq -r '.server.api_key' <config-path>",
      "compat": {"supportsDeveloperRole": false, "supportsReasoningEffort": false},
      "models": [{"id": "<alias>"}]
    }
  }
}
```

Use a shell-escaped config path in the command value. Run Pi with a fresh `PI_CODING_AGENT_DIR`, `--no-session`, `--no-extensions`, `--no-skills`, `--no-prompt-templates`, `--no-context-files`, `--tools bash`, `-p`, `--mode json`, and `--model "llm-env/${alias}"`. Extract the assistant final text from Pi’s JSON output, then require it to be one JSON object.

- [ ] **Step 4: Implement the OpenCode adapter**

Write `${workspace}/opencode/opencode.jsonc` with provider metadata but no secret:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "llm-env": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "llm-env",
      "options": { "baseURL": "http://llm.local:<port>/v1" },
      "models": { "<alias>": { "name": "<alias>" } }
    }
  }
}
```

Pass the local key only as `OPENCODE_API_KEY` in the command environment, run from `${workspace}`, and invoke `opencode run --format json --model "llm-env/${alias}" "$prompt"`. Before finalizing this path, run a real safe experiment with no auto-approval. If it cannot invoke Bash non-interactively without `--auto`, report `SKIP client=opencode ... reason=noninteractive shell approval unsupported` and document the restriction; do not add `--auto`.

- [ ] **Step 5: Implement source comparison and negative tests**

Parse the agent object with `jq -ce`. For weather require exact timestamps, exact weather code, and numeric temperature equality. For FX require exact timestamp and CLP equality. Add:

```python
def test_agent_check_rejects_stale_or_hardcoded_source_evidence(tmp_path):
    result, _, _ = run_agent_check(tmp_path, clients={"pi": stale_pi_stub})
    assert result.returncode != 0
    assert "fail client=pi model=gemma4 check=weather source evidence differs" in result.stderr
```

Also assert output, call logs, and generated config contents do not contain the fixture secret.

- [ ] **Step 6: Verify and commit**

Run: `make validate && make test`

```bash
git add check-with-agents.sh tests/test_shell.py
git commit -m "feat: run live inference checks through local agents"
```

## Task 4: Required client research and evidence

**Files:**
- Create: `docs/client-compatibility.md`

**Interfaces:**
- Each client section records version, official source URL, config location, base URL field, secret mechanism, non-interactive command, test date, result, and exact limitation.
- A `verified` result requires a real alias-specific `llm.local` inference without a visible secret; all other results are `supported with limits` or `not compatible`.

- [ ] **Step 1: Create the evidence template before research**

Create one heading for each required client in order: Antigravity, VS Code extension, Codex, Claude Code. Under each heading add this fixed table:

```markdown
| Field | Evidence |
|---|---|
| Version | `<command output>` |
| Official documentation | `<URL>` |
| OpenAI-compatible endpoint setting | `<exact field or not available>` |
| Secret storage | `<exact mechanism>` |
| Non-interactive invocation | `<exact command or not available>` |
| `llm.local` alias test | `verified / supported with limits / not compatible` |
| Limitation | `<specific observed limitation>` |
```

- [ ] **Step 2: Research Antigravity and record a tested result**

Identify the installed product with `command -v`, package metadata, and its About/version command. Read its official provider documentation. Test a single enabled alias through `llm.local` only if the product documents a custom OpenAI-compatible provider. Record the result and never add a speculative configuration.

- [ ] **Step 3: Research one VS Code extension and record a tested result**

List installed extensions with `code --list-extensions --show-versions`. Select a maintained extension that explicitly supports custom OpenAI-compatible endpoints. Read its official configuration schema, configure it in a temporary VS Code profile or documented settings file, and run its documented command/task smoke test. Record its result; state that VS Code itself is not a model client.

- [ ] **Step 4: Research Codex and Claude Code in order**

For Codex, read `codex --help`, `codex exec --help`, its active config schema, and official local-provider documentation without invoking model inference. Determine whether arbitrary OpenAI-compatible URLs are supported beyond LM Studio/Ollama. Then repeat the process for Claude Code using `claude --help`, `claude --print --help`, active settings schema, and official provider documentation. Record test evidence or exact unsupported limitations.

- [ ] **Step 5: Review evidence and commit**

Confirm every instruction is evidence-backed and no table contains a secret or LAN IP. Run `git diff --check`.

```bash
git add docs/client-compatibility.md
git commit -m "docs: record local client compatibility research"
```

## Task 5: README and Quick Start rewrite

**Files:**
- Modify: `README.md`, `QUICK_START.md`

**Interfaces:**
- `README.md` links to `QUICK_START.md` and `docs/client-compatibility.md`.
- Quick Start uses `<alias>` plus port read from configuration and never has an API key literal.

- [ ] **Step 1: Add failing documentation contract tests**

Add a lightweight pytest test:

```python
def test_docs_describe_all_online_check_targets():
    readme = (ROOT / "README.md").read_text()
    quick_start = (ROOT / "QUICK_START.md").read_text()
    assert "QUICK_START.md" in readme
    assert "make check-with-agents" in readme
    assert "http://llm.local" in quick_start
    assert "make check-server" in quick_start
    assert "make check-with-agents" in quick_start
    assert "<api_key>" not in quick_start
```

- [ ] **Step 2: Verify the documentation test fails**

Run: `uv run --with pytest pytest tests/test_docs.py -v`

Expected: FAIL because the current documentation has no agent target or compatibility evidence link.

- [ ] **Step 3: Rewrite README.md for first-time local users**

Write sections for project purpose, lifecycle, what llm-env manages, command table, and compatibility matrix. Link to Quick Start for commands and client setup. Link to the research evidence. State that live data requires agent tooling and `make check-with-agents`; it is not llama.cpp Internet access.

- [ ] **Step 4: Rewrite QUICK_START.md with safe, verified examples**

Include `make prerequisites → setup → check-setup → benchmark → start → check-server`. Show mDNS health, secure in-memory key retrieval, deterministic curl request, and `unset LLM_ENV_KEY`. Add Pi/OpenCode commands only if Task 4 marked them verified. For each non-verified client, link to its evidence section and state its limitation. Add live weather/FX quick checks matching the target’s JSON contract.

- [ ] **Step 5: Verify and commit**

Run: `make validate && make test && git diff --check`

```bash
git add README.md QUICK_START.md tests/test_docs.py
git commit -m "docs: explain verified local inference workflows"
```

## Task 6: Live acceptance and final documentation review

**Files:**
- Modify only if acceptance reveals a reproducible bug: the responsible script, test, or documentation file.

- [ ] **Step 1: Test each model individually through the server contract**

For Gemma4 alone, run `make setup`, select `1`, then run `make check-setup`, `make benchmark`, `make start`, `make check-server`, and `make stop`. Repeat with Ornith alone, selecting `2`. Record only model alias and PASS/FAIL; never capture the API key.

- [ ] **Step 2: Run live agent matrix**

Start one model, run `make check-with-agents`, and verify each installed agent runs both checks. Repeat with the other model. Confirm missing clients say `skip`, present clients have four PASS rows per two models, and a source mismatch produces a nonzero exit in the mocked suite.

- [ ] **Step 3: Verify documentation commands**

Run the documented DNS health command, deterministic curl command, Pi command, and OpenCode command only when their Task 4 status is `verified`. Verify no command outputs the key.

- [ ] **Step 4: Final verification and commit any acceptance fix**

Run: `make validate && make test`

If a reproducible fault was fixed during acceptance:

```bash
git add <changed-files>
git commit -m "fix: address live agent check acceptance failure"
```

Otherwise make no empty commit.

## Self-Review

- **Spec coverage:** Task 1 implements deterministic server inference. Tasks 2–3 implement the opt-in agent matrix, secret isolation, JSON evidence, source comparison, and missing-client policy. Task 4 implements ordered client research. Task 5 implements README/Quick Start requirements. Task 6 covers two single-model acceptance runs.
- **Placeholder scan:** The plan contains no unfinished implementation markers or unspecified error handling. External client research has explicit evidence fields and both verified/unsupported decision paths.
- **Interface consistency:** Task 2 defines `run_agent`; Task 3 supplies Pi/OpenCode implementations. Task 4 produces the compatibility evidence consumed by Task 5. All API aliases come from `/v1/models`, never static model definitions.
