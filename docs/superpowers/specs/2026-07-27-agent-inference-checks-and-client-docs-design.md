# Agent Inference Checks and Client Documentation Design

## Goal

Document llm-env clearly, strengthen the deterministic online server check, and add an opt-in agent integration check that proves configured agents can use the LAN mDNS endpoint, the protected local API key, and fresh Internet data.

## Scope

This work has three parts:

1. Make `check-server` verify a known response from every enabled model through the running OpenAI-compatible API.
2. Add `make check-with-agents` for live data checks through installed Pi and OpenCode clients.
3. Rewrite `README.md` and `QUICK_START.md` around the actual lifecycle and verified client integrations.

The target platform remains Bazzite/Fedora. All output remains English.

## Non-goals

- Do not add Internet access to llama.cpp. llama.cpp serves inference only.
- Do not claim that a model knows live information without an agent tool fetching it.
- Do not configure or document a client integration until its current client version has been researched and tested.
- Do not print, pass on a command line, persist in a temporary client config, or otherwise expose the local API key.
- Do not turn `make check-server` into a network-dependent test.

## API Contract Check

`make check-server` remains a local, deterministic API contract test. It must perform these checks in order:

1. `GET /health` responds.
2. An invalid bearer key on an inference endpoint returns HTTP 401.
3. `GET /v1/models` lists exactly the enabled aliases.
4. For every enabled alias, an authenticated `POST /v1/chat/completions` sends the same request:

   ```json
   {
     "model": "<alias>",
     "messages": [{"role": "user", "content": "Reply with exactly: ready"}],
     "max_tokens": 16,
     "stream": false
   }
   ```

5. The response has nonempty assistant content that normalizes to `ready`. Normalization trims surrounding whitespace and punctuation and compares case-insensitively.

A failed model request or mismatched response fails the target and identifies its alias. The request uses curl configuration files for bearer headers so the key is not visible in process listings.

## Agent Integration Check

### Command and presence policy

Add `make check-with-agents`, delegated to `scripts/check-with-agents.sh`. The script detects Pi and OpenCode independently:

- A present client enters the test matrix.
- A missing client is reported as `skip` with its installation/configuration prerequisite.
- At least one present client is required. If neither is present, the target fails rather than claiming success from only skips.
- Every failure in the tested matrix fails the target.

The target is opt-in and requires outbound Internet access. It must not be called by `make check-server`, setup, benchmark, start, or boot lifecycle commands.

### Model and prompt matrix

The script obtains the aliases from the authenticated local `/v1/models` response. For each installed agent, each returned alias, and each prompt below, it runs one isolated non-interactive invocation:

| Check | Prompt requirement | Authoritative source |
|---|---|---|
| Santiago weather | Return current Santiago, Chile conditions | Open-Meteo forecast endpoint with Santiago coordinates (`-33.4489`, `-70.6693`) and current time, temperature, and weather code |
| USD to CLP | Return the current USD-to-CLP conversion rate | ExchangeRate-API public USD feed and its `rates.CLP` value |

Both prompts require one JSON object with `source_url`, `source_timestamp`, and the requested value. They must state that the agent should use a shell network command and cite the fetched source URL.

Before each matrix row, the harness fetches and validates the relevant source with curl and jq. It compares the agent JSON to that fresh response:

- The source URL must identify the expected source host and request.
- The weather response must include the fetched `current.time`, `temperature_2m`, and `weather_code`.
- The FX response must include the fetched provider update time and `rates.CLP`.
- Numeric comparisons use a documented tolerance only where the source represents a rounded number. Timestamps must exactly match the fetched value.

This detects hardcoded or stale answers. A model may use a different prose explanation, but its JSON evidence must match the fresh source.

### Isolation and secrets

Each client invocation uses a new `mktemp -d` workspace with a cleanup trap. The harness provides no project source tree and runs clients from that workspace. It allows agent shell tools and outbound HTTPS because those are the behavior under test.

The harness reads the local API key only inside the process that configures or invokes the client. It must use command-based secret resolution when the client supports it; otherwise it passes the key through a private, short-lived environment variable. It must not place the key in a prompt, shell argument, checked-in file, temporary JSON file, logs, or final report. Temporary directories are mode 700.

The script records non-secret evidence for each row: client, model, check name, exit status, source fetch timestamp, and parsed agent JSON. It never records an Authorization header or API key.

### Pi execution

Pi uses a temporary `PI_CODING_AGENT_DIR` containing a `models.json` custom provider:

- `baseUrl`: `http://llm.local:<configured-port>/v1`
- `api`: `openai-completions`
- model IDs: aliases returned by `/v1/models`
- compatibility disables unsupported developer-role and reasoning-effort fields if llama.cpp rejects them
- API key: a command that reads the protected llm-env config at request time

Invoke Pi non-interactively with `-p`, `--mode json`, `--no-session`, an explicit `llm-env/<alias>` model ID, and an explicit Bash-tool allowlist. Disable unrelated extension, skill, prompt-template, and context-file discovery. Do not use `--approve` to bypass command approvals unless experiment results show it is needed and the approved design explicitly accepts that behavior.

### OpenCode execution

OpenCode uses a temporary config directory with a custom `@ai-sdk/openai-compatible` provider:

- `options.baseURL`: `http://llm.local:<configured-port>/v1`
- credentials from a private short-lived environment variable, not config text
- models: aliases returned by `/v1/models`

Invoke OpenCode with `opencode run --format json --model <provider>/<alias>` from the temporary workspace. The implementation must experimentally verify its permission behavior before enabling automatic approvals. If the current OpenCode release cannot provide a safe non-interactive Bash tool invocation, report it as unsupported and do not weaken its safety settings.

## Client Research Before Documentation

Research and test these integrations in this exact order before adding their instructions to `QUICK_START.md`:

1. **Antigravity:** identify the exact product, version, extension host, custom-provider format, and non-interactive capability. If the installed product cannot be identified or does not support this API, document that limitation with an official reference.
2. **VS Code:** evaluate compatible extensions separately from VS Code itself. Test each candidate extension’s OpenAI-compatible base URL, API-key storage, selected-model configuration, and command or task-based smoke test. Recommend only one tested extension as the primary path.
3. **Codex:** verify whether its current local-provider implementation can target arbitrary OpenAI-compatible servers. If it supports only LM Studio/Ollama, document that restriction rather than presenting llama.cpp as supported.
4. **Claude Code:** verify whether the current release offers a supported custom OpenAI-compatible endpoint. If it does not, document it as unsupported for direct llm-env inference.

For any verified client, test a specific enabled alias through `llm.local`, with the local API key, in a non-interactive command. Capture only non-secret test evidence. Use official documentation and installed-command help as primary sources.

## Documentation

### README.md

The README serves a new local user. It must contain:

- a one-sentence description: local llama.cpp router on Bazzite/Fedora that serves selected GGUF models through an authenticated OpenAI-compatible API;
- what llm-env handles: GPU detection, model preparation, offline smoke test, backend benchmark, systemd lifecycle, LAN/mDNS exposure, and API key protection;
- the normal lifecycle: `prerequisites → setup → check-setup → benchmark → start → check-server → stop`;
- a short command table including `key-reset`, boot controls, status, logs, and `check-with-agents`;
- a concise compatibility matrix that labels Pi/OpenCode according to tested state and labels unverified clients honestly;
- a prominent link to `QUICK_START.md` for curl and client commands.

### QUICK_START.md

Quick Start contains the operational detail:

- DNS health verification: `curl --fail http://llm.local:<port>/health`;
- local and LAN endpoint guidance, including the `127.0.0.1` IPv4 behavior and `.local` mDNS requirement;
- authenticated curl inference using an in-memory shell variable read from the mode-600 config, followed by `unset`;
- a deterministic alias-specific `ready` request matching `check-server`;
- Pi and OpenCode configuration plus one non-interactive alias-specific command for each, after they have been verified;
- direct live-data examples matching `check-with-agents`, with clear warning that these require an agent shell/network tool and outbound Internet;
- a client research table for Antigravity, VS Code extension, Codex, and Claude Code in the required order;
- troubleshooting links to `make check-setup`, `make check-server`, `make check-with-agents`, `make logs`, and `make status`.

Examples use `<alias>` and dynamically read the configured port. They never use a stale hardcoded API key or LAN IP.

## Testing

- Add shell-level command-construction tests for deterministic `ready` normalization and every error result.
- Add isolated tests for `scripts/check-with-agents.sh`: detection, skip/report policy, no-client failure, full agent/model/prompt matrix construction, temporary-directory cleanup, no key in logs, and source comparison failures.
- Mock agent binaries and public-source curl responses in automated tests. Live Internet and real-agent checks run only when the user invokes `make check-with-agents`.
- Run `make validate && make test` after shell and Python changes.
- Manually run the full lifecycle for Gemma4 alone and Ornith alone. For each enabled model, run `make check-with-agents` with every installed agent and preserve only redacted result evidence.

## Acceptance Criteria

- `make check-server` fails if any enabled model does not return normalized `ready` through authenticated curl.
- `make check-with-agents` reports Pi/OpenCode as tested or skipped, fails when no client is available, and fails on any tested matrix row failure.
- Each agent/model combination performs both live checks and provides JSON evidence matching fresh authoritative responses.
- No API key appears in command lines, logs, generated client configs, test artifacts, or documentation examples.
- Quick Start includes verified curl, Pi, and OpenCode commands through `llm.local` for a selected alias.
- Client instructions for Antigravity, VS Code, Codex, and Claude Code follow the specified research order and state only tested capabilities.
