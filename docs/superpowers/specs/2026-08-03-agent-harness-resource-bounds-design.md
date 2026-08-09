# Agent Harness Resource Bounds Design

**Status:** Approved

**Date:** 2026-08-03

**Revised:** 2026-08-04

## Purpose

Bound every harness-side process, file, and attributable request in each
live-agent matrix cell and in the aggregate run so a non-converging model cannot
exhaust the harness allocation measured when work starts. The bounded harness
will restart screening in the fixed publisher-then-greedy order without
changing either sampler profile, prompt, or source validation.

Each cell's normal work will run in one transient systemd user service. That
service contains the source fetch, parsers, client, capture helpers, validators,
and diagnostic workers. An exact sibling control service contains the request
broker and resource observer so both remain alive if the cell is killed. An
outer supervisor launches both services, enforces an independent operational
timer, verifies cleanup and request quiescence, and publishes the authoritative
result. This harness does not redefine llama.cpp's internal memory or task
envelope; the existing server budget and one-slot configuration remain
responsible for inference-server allocations.

This design explicitly supersedes the model sampler design where that design
requires an unchanged live-agent harness or an unchanged invocation of the
target. It also supersedes part of the transparent diagnostics design. The
semantic experiment invariants that remain unchanged are enumerated below.

## Upstream Supersession and Preserved Behavior

For the bounded recovery only, this design supersedes the following requirements
of the Model-Specific Sampler Configuration Design:

- the goal that the Pi and OpenCode live checks remain implementation-identical;
- the non-goal that prohibits changes to `scripts/check-with-agents.sh` or its
  evidence requirements;
- the staged-experiment instruction to invoke an unchanged
  `make check-with-agents` target; and
- the qualification and final-acceptance references to results from the
  unchanged script.

This design also supersedes two contracts that the bounded implementation
cannot preserve safely:

- `pylib/config.py`'s prior acceptance of any nonempty stripped model alias is
  replaced by the bounded alias grammar below before configuration rendering or
  server startup; and
- network-command attribution is limited to literal direct network actions in
  pinned adapter records. Without trusted egress telemetry, wrapper, variable,
  substitution, and indirect-executable syntax cannot prove that a command
  performed network I/O.

The public target remains `make check-with-agents`, accepts no arguments, tests
Pi and OpenCode, and exits nonzero when any tested alias has a failing cell. Its
implementation, machine records, process isolation, capture, and diagnostic
retention change as specified here. Private candidate runners consume the
manifest and result records rather than parsing redirected target output.

The following experiment behavior remains invariant and is checked against the
identified upstream source before the bounded harness hash is pinned:

- Candidate order, sampler values, screening policy, three-round qualification
  policy, and the four required `gemma4` identities do not change.
- Each row fetches a fresh source snapshot immediately before model invocation.
  Weather uses
  `https://api.open-meteo.com/v1/forecast?latitude=-33.4489&longitude=-70.6693&current=temperature_2m,weather_code&timezone=America%2FSantiago`;
  FX uses `https://open.er-api.com/v6/latest/USD`.
- The weather and FX prompt bytes retain the current exact-command instruction,
  URL, only-network-request rule, source timestamp rule, required field list,
  literal `source_url` rule, and exactly-one-JSON-object requirement. The prompt
  receives no source value or expected result.
- Pi and OpenCode retain their provider type, model selection, Bash-only tool
  policy, isolation flags, JSON output mode, and normal output-token settings.
  Only the provider base URL and credential transport change to use the cell
  request broker. On the broker's upstream hop only, a transport correlation
  header is replaced as described below; it does not change prompt content or
  add a generation or sampler parameter.
- Evidence remains valid only as one bare JSON object or one `json` fenced
  object with no surrounding prose. Required field types, source URL equality,
  timezone-aware source-date comparison, and exact source-value comparison do
  not change.
- Before screening, the active configuration, generated preset, and
  `/v1/models` response must contain the same duplicate-free set of aliases,
  and every alias must satisfy the bounded grammar below. Every alias in that
  accepted response remains visible and tested. Additional valid configured
  aliases retain their existing effect on the target's final exit status and
  final all-alias acceptance, but not on the four-cell sampler qualification.

Preflight extracts these invariants from commit
`3dbc3069484518efe4e4d2ad03535edb181c9afc`, records the two exact prompt
SHA-256 values and the source/validator projection, and compares that projection
to the bounded implementation. Any semantic difference not listed as a
supersession above blocks the run.

## Evidence

The first greedy screening attempt invoked the model and produced 355 Bash
tool calls, including 354 extra network requests. Pi emitted about 1 GB of
JSONL before the outer run timed out. A retry reached the same matrix cell and
the kernel killed the harness Bash process at about 9 GiB anonymous RSS.

The failure had two causes:

- The greedy model did not converge and generated repeated altered actions.
- The harness expanded the complete transcript into a Bash argument for
  diagnostic logging, multiplying memory use.

Retained successful cells establish these maxima:

| Measurement | Observed maximum | Bound | Headroom |
|---|---:|---:|---:|
| Client runtime | 86.333 seconds | 300 seconds | 3.47x |
| JSONL transcript | 6,349,035 bytes | 33,554,432 bytes | 5.28x |

The common bounds cover Pi and OpenCode. The runaway crossed 32 MiB after
about 390 seconds, well before it reached 1 GB.

## Decisions

- The 300-second and 32 MiB bounds are harness-side host-safety limits, not
  candidate performance requirements or llama.cpp-internal limits.
- A resource event rejects a sampler only when complete accepted evidence
  directly proves model behavior under the classification rules below.
- One transient cell service contains every normal stage. One separately
  bounded control service contains only the cell request broker and resource
  observer; the design does not split normal stages into controller, client, or
  stage services.
- Deadlines are operational targets. General-purpose Linux cannot guarantee
  signal delivery at an exact monotonic instant under arbitrary scheduler or
  service-manager delay.
- The client runs in a Bubblewrap child sandbox inside the cell service. This
  blocks the tested same-user cgroup and user-manager escape paths without
  claiming a security boundary against arbitrary same-UID code.
- The sandbox creates its one required user namespace with
  `--disable-userns`, drops every capability, and permits no nested user or
  mount namespace. The single `WorkspaceMax` tmpfs is therefore the only
  client-writable mount.
- A credential-terminating request broker and its resource observer run in an
  exact, separately bounded control service outside the limited cell cgroup.
  They are ready before gate release, survive a cell `SIGKILL`, OOM, or task
  limit, and seal terminal-request and server-quiescence records before the
  control service exits. Broker loss or unproved quiescence makes the attempt
  infrastructure-invalid and aborts the matrix. No harness path stops or kills
  `llm-server.service`.
- The host-safety claim covers only harness-side processes, files, and
  requests. Server request growth is measured and reported, and request
  quiescence is mandatory, but llama.cpp's internal allocations remain under
  its existing server budget and one-slot configuration.
- Raw or complete redacted client transcripts and stderr streams are never
  intentionally displayed or copied into retained artifacts. Stream redactors
  process every accepted byte, but output only hashes, counts, structured proof,
  and an intentionally incomplete capped excerpt. Failed exact-name deletion
  enters the bounded private quarantine contract below; while quarantine
  exists, the harness does not claim that raw files are absent.
- The outer supervisor and every transient process become non-dumpable and
  receive a zero soft and hard core limit before they can read a credential or
  raw byte. An inherited seccomp rule prevents descendants from restoring
  dumpability. A core handler invocation, coredump journal payload, or core file
  is a boundary failure, never a diagnostic path.
- Complete and bounded behavior proof use a shared causal stream cutoff. Only
  adapter records whose complete byte ranges were committed before that cutoff
  may contribute to model attribution.
- Existing experiment ledger entries and an exact pre-recovery ledger snapshot
  remain immutable and survive successful final cleanup.
- Alias ordinals and cell ordinals are distinct. A globally unique cell ordinal,
  not an alias ordinal, names each unit, exchange, artifact, and result.
- Historical publisher evidence remains immutable but cannot certify the new
  bounds. Publisher screening is rerun under this harness before greedy
  screening resumes.

## Goals

- Start the systemd runtime budget before the first source fetch in each cell.
- Apply one 300-second runtime target to every normal stage in that cell.
- Give systemd a 10-second graceful-stop interval before its effective first
  escalation signal becomes `SIGKILL`.
- Cap raw transcript and client stderr input at 33,554,432 bytes each.
- Keep source bodies, client streams, parser output, and diagnostics out of
  shell variables and command arguments. The validated alias and prompt are
  the only bounded variable strings allowed in required client arguments.
- Bound memory, tasks, private workspace, source bodies, parser output,
  diagnostics, result records, the run manifest, and run cleanup.
- Disable core generation before any process receives credentials or raw data,
  and prove that the deployed coredump handler receives no harness crash.
- Start the exact control service outside the cell cgroup before gate release;
  make a `pids.events` limit event seal `resource-limit/tasks` and kill through
  the observer's already-open exact cell-cgroup descriptor.
- Reserve current host and parent-cgroup memory headroom and current filesystem
  data and metadata before discovery and model work. Pre-create the complete
  fixed path and file allowlist so Btrfs inode counters are not required.
- Kill and reap all processes that remain in the cell cgroup after wrapper
  completion, failure, timeout, or resource exhaustion.
- Make the surviving broker finish or cancel every accepted upstream request,
  then prove the selected llama.cpp model quiescent before finalizing the cell
  or starting another one.
- Validate every inference body before forwarding, require its sole root
  `model` member to equal the cell alias, and bind the request to the selected
  model child and one observed llama.cpp task identity.
- Continue after a cell failure only when cleanup and result finalization are
  proved.
- Test every valid alias in the accepted discovery response. Derive aggregate
  output, manifest, temporary-copy, and storage maxima from the discovered alias
  count rather than imposing a fixed alias ceiling.
- Never intentionally display or retain raw streams; on bounded deletion
  failure, quarantine the exact private run and block all future attempts until
  exact-name deletion is verified.
- Produce private, redacted evidence that supports conservative sampler
  classification.
- Publish one capped canonical storage plan whose closed terms justify setup
  admission and every later storage maximum.

## Non-Goals

- Do not change sampler candidates, prompts, source URLs, expected JSON, or
  source comparisons.
- Do not add client-specific generation settings, turn budgets, or tool-call
  budgets.
- Do not treat the 300-second or 32 MiB bound alone as a model failure.
- Do not provide hard real-time scheduling guarantees.
- Do not treat Bubblewrap, the Bash tool, or a same-UID user service as a
  security boundary.
- Do not infer network activity from shell wrappers, substitutions, variables,
  pipelines without a literal network executable, or indirect executables.
  This design adds no trusted egress telemetry.
- Do not limit network bandwidth. The runtime target bounds its duration, and
  existing evidence rules evaluate prohibited requests.
- Do not claim that the harness bounds llama.cpp's internal request-time memory
  or task growth. The server keeps its existing independently validated budget,
  disabled offload/fitting behaviors, and one request slot.
- Do not reclassify or delete existing ledger entries.
- Do not grandfather a sampler classification produced by the unbounded
  harness into bounded screening or qualification.
- Do not publish a sampler default unless a candidate passes the existing
  screening and three-round qualification policy.

## Architecture

### Components

| Component | Responsibility |
|---|---|
| Outer supervisor | Own the run lock, create private fixed-layout directories, launch exact cell and control units, verify properties, release gates, observe operational timers, finalize results, and decide whether another cell may start. |
| Discovery service | Fetch and parse `/v1/models` with bounded input and output before planning the matrix. |
| Cell wrapper | Run every normal stage for one client/model/check identity and atomically publish bounded state and a candidate result. |
| Cell control service | Run in a sibling cgroup outside the cell limit; host the credential-terminating request broker and resource observer; hold exact cell-cgroup descriptors; survive cell termination; finish or cancel upstream work; and seal resource, terminal-request, and server-quiescence records. |
| Bounded capture helpers | Consume transcript and stderr streams, enforce byte caps, commit accepted byte ranges through the shared cutoff lock, redact accepted bytes, and publish one-writer status records. |
| Behavior evaluator | Read accepted client records through a version-pinned adapter, apply complete non-resource attribution or the narrower bounded-resource predicate, and publish a structured bundle. |
| Bubblewrap client sandbox | Hide the supervisor exchange, user-manager transports, host process namespace, and writable cgroup hierarchy from the client and its Bash tool. |
| No-core launcher | Set `RLIMIT_CORE={0,0}`, set `PR_SET_DUMPABLE=0`, install the inherited dumpability seccomp rule, verify all three facts, and only then execute a supervisor or service role. |
| Result finalizer | Combine wrapper records with systemd facts, verify cleanup, publish the authoritative cell result, and remove raw fixed-name files. |
| Run finalizer | Publish the manifest and either retain the redacted artifact allowlist, remove the private run directory through a bounded cleanup service, or atomically quarantine that fixed directory on deletion failure. |

The outer supervisor handles only bounded scalar values and files whose schemas
and maximum sizes it has already verified. Every external command that accepts
variable-size source, client, or diagnostic data runs in the discovery service,
cell service, control service, or run-finalizer service.

### Run Lifecycle

The supervisor acquires an exclusive private run lock. A stale lock or a
previous exact-prefix transient unit blocks a new run until the supervisor
proves that no old cell or control process remains. The supervisor never kills
units by a broad glob.

Before discovery, it records an allowlisted, credential-free server identity:
the exact active `llm-server.service` invocation ID and control group, container
image digest, llama.cpp build number and revision, router role, configured
models maximum, model-set projection, and one-slot setting. The deployed
grounding for this design is image digest
`sha256:162762d4bad73319e28b09e0285390a72628e11079299d84b815255399043482`,
build `b10200`, and revision `5f55650a78f92aff4d48d671423e888fac0469ff`.
Preflight and every cell gate require the current values to equal the calibrated
record. Invocation or build drift aborts before gate release. The invocation ID
is an identity and drift check only: the harness never calls `StopUnit`,
`KillUnit`, or an equivalent signal path for `llm-server.service`.

The server-identity reader obtains only the named systemd scalar properties,
the named `/props` and duplicate-checked `/v1/models` projections, the selected
child's process/cgroup identity, and the image digest field from a filtered
container query. It never requests or reads `ExecStart`, `Environment`,
`EnvironmentFiles`, a full unit or container inspection, `/proc/*/cmdline`, or
`/proc/*/environ`; those sources can contain `LLAMA_API_KEY`. Its RFC 8785
record has exactly `schema`, `unit`, `invocation_id`, `control_group`,
`image_digest`, `build`, `revision`, `router_role`, `models_max`,
`parallel_slots`, and `models_sha256`. After preparation, a separate per-cell
child record contains exactly `schema`, `run_id`, `cell_ordinal`,
`alias_sha256`, `router_invocation_id`, `child_pid`, `child_cgroup`,
`child_starttime_ticks`, `router_model_sha256`, and `slot_count`;
`model_child_sha256` is the SHA-256 of those canonical bytes.
It uses the same safe sources and never reads child argv or environment. Unknown
fields, unavailable allowlisted fields, or access to a forbidden source aborts
preflight. Calibration and each cell bind the SHA-256 of these canonical bytes.

The discovery service fetches `/v1/models` through the existing endpoint. It
caps the response, validates each alias under the exact encoding contract below,
rejects decoded duplicates, and writes a canonical JSON alias file. Response
position assigns a zero-based `alias_ordinal`; this ordinal identifies only the
alias and never names a cell.

Before accepting that file, preflight validates the active `models.yml` through
the same Python alias validator used by setup, verifies that the generated
preset preserves each enabled alias exactly, and compares the decoded API alias
set with the enabled configured alias set. A missing, extra, changed, or invalid
alias is `invalid-output/model-list-parse/invalid-schema` and blocks screening;
the harness never silently drops an API alias.

The planner uses alias response order, client order `pi`, `opencode`, and check
order `weather`, `fx`. With zero-based `client_index` and `check_index`, it
assigns each identity the checked value

```text
cell_ordinal = ((alias_ordinal * 2) + client_index) * 2 + check_index
```

It uses checked unsigned 64-bit arithmetic internally, rejects a published
value above `9007199254740991`, and proves that every tuple maps to one distinct
ordinal and every ordinal maps back to the same tuple. A run ID is exactly 32
lowercase hexadecimal digits; the supervisor creates its private run directory
with exclusive creation and rejects a run ID already present in the ledger,
filesystem, quarantine, or user manager. Exact cell and control unit names are
`llm-env-agent-<run_id>-c<20-digit-cell_ordinal>.service` and
`llm-env-agent-<run_id>-k<20-digit-cell_ordinal>.service`. Exchange directories,
artifact directories, and result basenames use the same full run ID and
20-digit cell ordinal. Alias hashes remain record fields but are not the source
of name uniqueness. Before release, the supervisor proves that all planned
cell-unit, control-unit, exchange, artifact, and result names are pairwise
distinct and absent.

After discovery, the planner computes the run-derived aggregate maxima below
for every discovered alias. It imposes no fixed alias-count ceiling. The setup
service then creates every planned mode `0700` directory and every fixed-name
mode `0600` regular file, including both members of each atomic-publication
pair, before any model preparation or client work. Planning proceeds whenever
the current host can reserve the resulting finite run. The discovery-response
cap statically guarantees the checked formulas fit their representation; a
runtime disagreement is a harness protocol defect, not an alias ceiling. Every
alias remains visible and tested and keeps its existing final-acceptance effect.

Before setup starts, the outer supervisor publishes the closed canonical
storage plan defined below through its pre-created atomic pair. The setup
service consumes only that plan, and the supervisor independently recomputes
its counts, checked products, hash, and filesystem assignments before releasing
the setup gate.

For each identity, the supervisor populates the pre-created bounded input
record, then starts the exact control service and cell service with the
client-work gate closed. The control service is a sibling, never a child, of the
limited cell cgroup. Its broker binds a kernel-assigned loopback port, accepts
only a fresh cell-scoped bearer token, and receives the real llama.cpp API key
as a systemd credential that the cell and Bubblewrap cannot see. It strips the
cell token, injects the real credential upstream, and writes bounded
request-status records containing only request ordinal, method, allowlisted
path, model alias hash, selected-child hash, bound task ID, connection state,
terminal state, and byte counts.
Supported client configuration points only to this broker. Direct requests to
the real inference endpoint cannot authenticate with the cell token; public
health and model-list requests cannot start inference.

For each inference request, the broker first captures at most 8,388,608 body
bytes into its bounded private control-service buffer and forwards no upstream
byte until validation completes. A pair-preserving UTF-8 JSON decoder requires
one root object, rejects duplicate names at every depth, and requires exactly
one root `model` string whose decoded code-point sequence equals the cell alias.
The broker rejects a missing, non-string, duplicate, or different model as a
protocol failure. The pinned clients must also send `stream=true`; another value
cannot supply the live correlation proof below and fails closed. The broker
removes any client-supplied `X-Conversation-Id` and injects a fresh bounded
unpredictable ID used for this request only. That ID is exactly 32 lowercase
hexadecimal digits from the kernel RNG, is absent in a pre-forward lookup, and
is never reused within the run. Lookup uses the canonical body
`{"conversation_ids":["<id>"]}` and deletion uses exactly
`/v1/stream?conv_id=<id>`; hexadecimal needs no query escaping. The pinned
router source binds that header to the model selected from the validated body,
and the selected child creates the matching stream session. This transport-only
header is not prompt content and changes no generation option. The broker
removes the buffer after forwarding or rejection; it never retains, displays,
hashes, or logs request content. This bounded validation buffer is included in
`ControlVariableMax`.

The broker derives `UpstreamMax` from the selected model's measured slot count,
which is one on this deployment, and verifies it at gate release. It never opens
more than `UpstreamMax` inference connections and has no upstream request queue;
a concurrent excess request is recorded and rejected before forwarding. This
prevents the supported clients from building an unbounded queue in the server.
The queued-request fixture remains required because an otherwise idle slot can
be taken by unrelated activity between gate verification and forwarding.

Every selected-model query uses this exact target:

```text
/slots?model=<pct-encoded-alias>
```

The encoder operates on the validated UTF-8 alias bytes, leaves only RFC 3986
unreserved bytes `ALPHA / DIGIT / "-" / "." / "_" / "~"` unchanged, and
encodes every other byte as `%` plus two uppercase hexadecimal digits. It never
uses `+`, accepts no pre-existing escape, and appends no other query member or
fragment. The broker rejects any response whose child projection does not
match the gate's credential-free selected-child identity.

The supervisor caches both services' invocation IDs, cgroup paths, active
timestamps, and effective properties. Before opening the cell gate, it proves
the control service ready, and the observer opens the exact cell cgroup's
`pids.events`, `memory.events`, `cgroup.events`, and `cgroup.kill` files and
seals its initial snapshots. The ready broker then performs one capped
exact-encoded selected-model slot preparation request. This request may load the
selected model, remains inside the cell's 300-second interval, and is subject to
the same finish-or-cancel protocol as a client request. After it completes, the
supervisor proves the selected model child is loaded with no pending load
ticket, records its credential-free router and child-identity projections,
proves all of its slots idle, verifies the broker's token and
zero-open-connection record, and repeats the current-memory and
remaining-storage gates. This prevents a production client request from
entering llama.cpp's detached load-and-replay path. Preparation failure must
reach the same terminal-request and quiescence proof before it produces a
cell-infrastructure result without releasing source or client work. Model-child
or safe router-projection drift, observer loss, broker loss, or control-service
loss closes an unreleased gate or terminates an already released cell and aborts
the matrix.

Immediately before each inference forward, one exact encoded slot sample must
show the bound child idle. The broker then polls the exact child slot and
`POST /v1/streams/lookup` for only its fresh conversation ID while the upstream
stream is open. It holds the first nonempty upstream chunk instead of forwarding
it until the same unique new `id_task` is active in the sole slot immediately
before and after that chunk and the matching conversation session's byte count
increases. The pinned source makes that conjunction causal: the router maps the
fresh ID to the body-selected child, that child's session receives the observed
chunk, and only the active slot task can produce it. The broker then seals
`(request_ordinal, conversation_id_sha256, alias_sha256,
model_child_sha256, id_task)` and releases the chunk. It retains neither the
conversation ID nor chunk content after terminal sealing.

No second inference is forwarded until that binding is terminal. A different
child, reused or unknown conversation ID, changed task between bracket samples,
missing byte-count transition, response completion before the bracket, or any
ambiguous slot/session projection leaves the request unbound. The broker records
a protocol failure, cannot seal quiescence, and aborts the matrix as
infrastructure-invalid. Thus an unrelated request or a fast response must be
distinguished by the bracket or fail closed; temporal proximity alone never
binds a task.

The wrapper then runs source fetch, source parsing, client configuration,
capture setup, client execution, final-response parsing, evidence parsing,
source comparison, behavior evaluation, and normal diagnostics in sequence.
All normal subprocesses remain in the cell cgroup; broker and observer processes
and all upstream sockets remain in the separately limited control cgroup. The
wrapper waits for every child and capture helper, closes its downstream broker
connections, publishes its candidate result when possible, and exits. It cannot
stop the broker or publish a server-quiescence claim.

After the cell service becomes inactive, the outer supervisor reads only
bounded records, selects the provisional mechanical outcome, and verifies the
saved cell cgroup is absent or has `populated 0`. Cell-cgroup cleanup alone is
never accepted as server-request cleanup because llama.cpp remains in its
separate server cgroup.

The still-live control service closes its listener and handles every accepted
upstream request in one of two ways: it finishes reading a complete upstream
response, or it closes that exact upstream socket to request cancellation and
proves the request's bound `id_task` is no longer active or deferred in the
same bound model child. Its capped exact-encoded slot reader rejects duplicate
or unknown projected members and stores only `id`, `id_task`, and
`is_processing`; prompt, generated text, and sampling fields are neither
retained nor displayed. EOF, cell death, broker death, an unbound task identity,
or an inferred cancellation without the live broker's cancellation action never
creates a terminal-request record.

For a bound inference, the broker next sends the pinned router delete for its
still-private conversation ID and requires a later router lookup for that ID to
be empty before it drops the ID. The delete and lookup are capped observer
requests and cannot start inference. This proves only that the router forgot its
correlation mapping: pinned `server-models.cpp` ignores the child delete response
and returns 204 after forgetting the mapping, so neither 204 nor the empty lookup
is child-session-eviction evidence. A missing 204, a nonempty router lookup, or
conversation-ID loss prevents terminal sealing. The bound task's absence and the
two idle slot samples below, not stream deletion, prove request quiescence.

The control service seals server quiescence only when it is still healthy,
every accepted preparation and inference request has a terminal record, no
upstream socket remains open, and two consecutive 100-millisecond slot samples
show the bound model child idle with no bound or observed deferred task, while
every injected conversation mapping is absent from the router. The resource
observer performs its final controller reads before the same seal. A cell OOM,
task-limit event, timeout escalation, or direct `SIGKILL` may remain the cell's
mechanical outcome only after this terminal-request and quiescence record
validates. The finalizer then stops the exact control service, verifies its
cgroup empty, and removes raw files by exact name.

Preflight against the exact llama.cpp image and revision proves that the
surviving broker can finish a running request or cancel a running and queued
request after cell death and then reach the two-sample idle condition.
Production binds this probe to image digest, build number, revision, the
credential-free server-identity projection, one-slot configuration, and harness
hash. Broker or control-service loss, an incomplete terminal record, or
quiescence not proved within the calibrated drain allowance records an
infrastructure-invalid boundary or cleanup failure and aborts the matrix. It
never signals `llm-server.service`, and no unproved path starts another cell; a
repaired run must repeat preflight.

The pinned source path supports, but does not replace, that live proof:
`server-http.cpp` passes connection closure into the request, the router proxy
propagates it to the model child, `server_response_reader` enqueues a task cancel
on teardown, and the child removes the matching active slot or deferred task.
`GET /slots` exposes `id_task` and `is_processing`. A different source revision
must repeat the live active-and-queued cancellation proof rather than inherit
these observations.

### Service Properties

Every discovery, cell, control, setup, and cleanup service uses reviewed
effective properties:

| Property | Required value or rule |
|---|---|
| `Type` | `exec` |
| `ExitType` | `main` |
| `KillMode` | `control-group` |
| `Restart` | `no` |
| `Delegate` | `no` |
| `NoNewPrivileges` | `yes` |
| `RestrictSUIDSGID` | `yes` |
| `UMask` | `0077` |
| `TimeoutStartSec` | `30s` |
| `RuntimeMaxSec` | `300s` for cells, `30s` for discovery or setup, `10s` for cleanup, and the checked control-service formula below |
| `RuntimeRandomizedExtraSec` | `0` |
| `TimeoutStopSec` | `10s` |
| `SendSIGKILL` | `yes` |
| `FinalKillSignal` | `SIGKILL` |
| `WatchdogSignal` | `SIGKILL` |
| `OOMPolicy` | `kill` |
| `MemoryAccounting` / `TasksAccounting` | `yes` |
| `MemoryMax` / `TasksMax` | Role-specific bootstrap values during calibration; role-specific hash-bound calibrated values afterward |
| `MemorySwapMax` | `0` |
| `LimitCORE` | `0` soft and hard |
| `StandardInput` | `null` |
| `StandardOutput` / `StandardError` | `null`; service processes write only through capped files |

`ExitType=main` makes loss of a service's main process stop that exact service.
`KillMode=control-group` then signals every process that remains in its cgroup,
including descendants in another process group or session. Cell and control
services are siblings with independent limits; a cell-wide kill cannot include
the broker or observer.

### Core-Dump Exclusion

`LimitCORE=0` is defense in depth, not the primary control: Linux does not
enforce `RLIMIT_CORE` when `core_pattern` pipes a dump to a handler. The public
target therefore starts the outer supervisor through the hash-bound no-core
launcher before the supervisor reads configuration, credentials, or recovery
state. The same launcher is the first executable in every transient service.
It sets both core limits to zero, calls `prctl(PR_SET_DUMPABLE, 0)`, installs a
no-log seccomp filter that returns `EPERM` for
`prctl(PR_SET_DUMPABLE, value)` when `value` is nonzero, verifies the limit,
dumpable state, and filter mode, and only then `execve`s the role. The filter,
zero hard limit, and non-dumpable state survive fork and ordinary exec. The
launcher rejects privileged executables, and `NoNewPrivileges=yes` plus
`RestrictSUIDSGID=yes` prevents an exec from acquiring credentials that could
reset dumpability.

Preflight records and hash-binds `/proc/sys/kernel/core_pattern`, requires the
deployed systemd-coredump pipe form, and obtains bounded system-journal cursors
before and after its probes. A crash fixture holds canary credential and stream
bytes, proves that restoring dumpability fails, then dies from `SIGSEGV` in each
role. Preflight passes only if no systemd-coredump handler instance starts, no
journal entry with `MESSAGE_ID=fc2e22bc6ee647b6b90729ab34a250b1` or any
`COREDUMP*` payload refers to a fixture PID, unit, invocation ID, or cgroup, and
`coredumpctl` reports no external file. Inability to query any of those facts,
a changed core pattern, or a generic `core`/`core.*` file in an allowlisted
writable directory fails preflight.

Production brackets every transient invocation with the same cursor and PID,
unit, invocation-ID, and cgroup checks and seals a 4,096-byte `core-audit`
record. Its closed canonical shape is exactly `schema`,
`record_type="core-audit"`, `run_id`, `scope`, `unit`, `invocation_id`,
`cgroup`, `journal_cursor_start`, `journal_cursor_end`,
`core_pattern_sha256`, `dumpable_violation`, `handler_seen`, `payload_seen`, and
`file_seen`; it contains no matched journal field or payload value. The client
sandbox also omits `/dev/log`, `/run/systemd/journal/socket`, and
`/run/systemd/journal/stdout`. Any dumpable process, failed inherited-filter
check, coredump handler, coredump journal payload, or core file is
`boundary-violation/protocol`, sets `raw_absence=false`, aborts the matrix, and
blocks future attempts for operator recovery. Generic systemd exit metadata
without a `COREDUMP*` payload is not a raw artifact.

The control service starts before the cell and receives

```text
ControlRuntimeMaxSec = 30s + 300s + 10s + manager_allowance + drain_allowance
```

where both calibrated allowances are at most 10 seconds. The first term covers
cell activation, the next two cover cell runtime and graceful stop, and the
remaining terms cover measured manager delay and request quiescence. Arithmetic
is checked before launch. This independent last-resort bound cannot turn
control-service loss into a valid cell resource result; any such loss aborts the
matrix as infrastructure-invalid.

`TasksMax` only makes a fork or clone over `pids.max` fail with `EAGAIN`; it
does not kill existing tasks. The resource observer therefore treats an
increase in the exact cgroup's `pids.events` `max` counter as the task-limit
event. The observer is outside the limited cgroup and already running before
gate release. It holds the event file open, uses its file-change notification
with a 100-millisecond monotonic read fallback, and seals the observed counter
and `resource-limit/tasks` marker before writing `1` to its already-open
`cgroup.kill` descriptor. The descriptor identifies the exact cell cgroup
object and cannot retarget a reused systemd unit name. Failure to match the
descriptor's cgroup identity to the gate record is
`boundary-violation/protocol`; the observer never falls back to a broad name.

The observer also snapshots `memory.events` before release and seals
`resource-limit/memory` when `oom` or `oom_kill` increases. On service
inactivity it performs one final read before closing its descriptors. Failure
to start the observer, loss of its heartbeat, an unreadable event descriptor,
or failure of the exact-cgroup kill write is a boundary violation and aborts
the matrix. Preflight must prove the task path with a fork-saturation fixture;
observing `EAGAIN` without an incremented counter and an exact-cgroup kill fails
preflight.

Discovery, setup, and cleanup wrappers also start behind a gate while the outer
supervisor opens their exact controller files. The same event and
exact-cgroup-kill protocol applies. The control observer watches both the cell
and its own controller state; any unsealed control-service resource event or
manager-reported control loss is a boundary failure. The result table maps
discovery and setup events to a run resource outcome and cleanup events to
`cleanup-failure`; no bounded service relies on `TasksMax` alone.

Fedora configures user services with `TimeoutStopFailureMode=abort`. The
preflight must inspect the effective stop path. It accepts a unit only when the
first signal after the 10-second stop interval is `SIGKILL`: either normal
termination mode uses `FinalKillSignal=SIGKILL`, or abort mode uses
`WatchdogSignal=SIGKILL`. A different effective path blocks the run.

## Time Contract

### Runtime Anchor

`A`, the unit's `ActiveEnterTimestampMonotonic`, is the cell runtime anchor.
The wrapper is already waiting behind its gate at `A`, so gate verification
consumes part of the same manager-measured interval. No source artifact exists
and no source request starts before gate release.

The cell service receives `RuntimeMaxSec=300s`. The outer supervisor also arms
an absolute monotonic timer for `D = A + 300s`. If the service remains active
when that timer is processed, the supervisor atomically records its timeout
request and submits `StopUnit`. Systemd's runtime timer independently requests
the same stop if the supervisor stalls or exits.

`TimeoutStopSec=10s` measures from the service manager's actual stop
processing, not from `D`. This preserves a full graceful-stop interval even
when the manager processes the runtime event late.

### Operational, Not Real-Time

Timer expiry makes a process or the service manager runnable; it does not
guarantee immediate scheduling. The design therefore makes these enforceable
claims:

- systemd and the supervisor each arm a 300-second runtime target;
- when either observer processes an expired target, it requests service stop;
- systemd schedules its effective `SIGKILL` escalation after the configured
  10-second stop interval;
- excessive measured delay is a boundary failure and stops the matrix.

The design does not claim that `SIGTERM` is requested no later than `D` or that
`SIGKILL` is delivered no later than `D + 10s`.

### Latency Calibration

Preflight runs five TERM-ignoring transient-service probes under the production
properties. For each probe it measures the positive delay between the expected
end of `RuntimeMaxSec + TimeoutStopSec` and observed service inactivity. Let
`L` be the largest delay, rounded up to the next 100 milliseconds. The run-time
manager allowance is `max(1 second, 4 * L)`. Preflight fails if that allowance
would exceed 10 seconds.

If a production cell remains active after the runtime target, stop interval,
and calibrated allowance, the surviving observer writes to the already-open
cell `cgroup.kill` descriptor, records `boundary-violation` with detail
`manager-delay`, and aborts the matrix. This request remains subject to
scheduler latency; the independent systemd limits continue to own the service.

After the unit becomes inactive, cleanup verification and result finalization
receive a separate 10-second operational target. Failure to finish when that
target is processed records `cleanup-failure` or `finalization-failure` and
aborts the matrix. The already-running control service must seal terminal and
quiescence records inside that interval; cleanup does not start another request
service.

Preflight also runs five long-generation cancellation probes through the
production request broker. For each, it kills the sibling probe cell while the
control service stays alive, makes the broker finish or cancel its upstream
request, and measures the positive monotonic delay until terminal records and
the two-sample selected-model idle condition are sealed. Let `Q` be the largest
delay rounded up to 100 milliseconds. The production drain allowance is
`max(1 second, 4 * Q)` and preflight fails if it would exceed 10 seconds. A
production drain that exceeds this allowance is infrastructure-invalid and
aborts without signaling the server; no scheduler-independent completion
deadline is claimed.

Service activation has a separate 30-second `TimeoutStartSec` target. Because
the gate remains closed, activation failure cannot consume source or client
resources and produces a run or cell infrastructure result.

## Resource Envelope

### Byte Caps

Each cap applies at the boundary named in the table. Raw-input caps apply before
parsing or redaction; displayed and retained caps apply to the final serialized
bytes after redaction, escaping, or Base64 encoding. A bounded writer keeps the
permitted prefix, reads at most one additional byte to prove overflow, and then
closes its input and notifies the wrapper. It never stores the overflow byte.

| Artifact | Maximum bytes |
|---|---:|
| Input record | 65,536 |
| Policy record | 16,384 |
| One systemd credential | 16,384 |
| Phase, helper, or resource status record | 4,096 |
| Setup completion, server-identity, causal-cutoff state/record, or core-audit record | 4,096 each |
| `/v1/models` response | 1,048,576 |
| Canonical alias file | `alias_file_max` derived after discovery |
| One alias | 4,096 |
| Source response | 1,048,576 |
| One stage stderr file | 65,536 |
| Prompt | 16,384 |
| Generated client configuration | 65,536 |
| One broker request header block | 65,536 |
| One broker request body | 8,388,608 |
| One broker response stream | 33,554,432 |
| One upstream correlation ID | 32 |
| One raw stream-lookup response | 65,536 |
| One projected task-binding record | 4,096 |
| Broker request-status and terminal-record file | 1,048,576 |
| One raw `/slots` response | 1,048,576 |
| One projected slot-state record | 4,096 |
| One server-quiescence record | 4,096 |
| Raw sandbox mountinfo | 65,536 |
| Raw client transcript prefix | 33,554,432 |
| Raw client stderr prefix | 33,554,432 |
| Serialized redacted excerpt per client stream | 262,144 |
| Raw final assistant text | 1,048,576 |
| Serialized redacted final response | 1,048,576 |
| Snapshot, evidence, or differences file | 65,536 each |
| Human-auditable proof bundle | 1,048,576 |
| Candidate, authoritative cell, or run result | 65,536 |
| Run failure marker | 4,096 |
| Calibration record | 65,536 |
| One serialized human cell block | 3,194,880 |
| Aggregate human output | `human_output_max` derived after discovery |
| `matrix.log` | `matrix_log_max` derived after discovery |
| Canonical storage plan | `plan_record_max` derived after discovery |
| Run manifest | `manifest_max` derived after discovery |
| Quarantine recovery record | `recovery_record_max` derived after discovery |
| All atomic siblings | `temporary_copy_max` derived after discovery |
| Host-backed private run | `run_storage_max` derived after discovery |
| Active ledger or exact legacy-ledger snapshot | 4,194,304 each |

The fixed exchange allowlist, per-file caps, and checked run-derived formulas
define the maximum host-backed storage. The supervisor rejects symlinks,
special files, unknown names, wrong ownership, unsafe modes, and oversize files
before reading them.

The stream redactor consumes every accepted raw byte and computes a checked
post-redaction byte count and SHA-256 without storing the complete redacted
stream. If that count is `R`, the excerpt contains the first
`min(131072, R - 1)` post-redaction bytes when `R > 0`, and no stream bytes when
`R = 0`. It Base64-encodes that prefix in a canonical JSON record containing
raw and redacted counts, both hashes, overflow state, and omitted-byte count.
The final capped writer enforces the 262,144-byte serialized limit. Thus every
nonempty stream omits at least one post-redaction byte, even when the whole
stream is shorter than the excerpt cap. The proof bundle contains only
allowlisted canonical fields and record hashes, never verbatim JSONL records.

Redaction status is valid only if the helper processed every accepted raw byte.
An expanding replacement cannot expand a retained or displayed artifact past
its post-redaction cap. Redaction, excerpt serialization, or cap failure deletes
the partial output by exact name and produces an infrastructure result; no path
falls back to displaying or retaining raw or complete redacted bytes. Failure
of that exact deletion invokes the quarantine contract rather than an absence
claim.

The broker buffers a request body only in its bounded private control-service
buffer until model validation succeeds, then forwards it without creating a
retained artifact. It streams responses without retaining them. It rejects a
header or request-body cap before forwarding any request byte, closes both sides
when a response crosses its cap, and seals counters before notifying the
wrapper. Before an additional status entry would cross the record cap, the
broker seals a broker-status overflow, closes its listener and upstream sockets,
and forwards no more requests; it neither drops an entry nor continues silently.
The client-stream cap and broker-response cap are independent because client
framing may expand the upstream response.

### Human Output and `matrix.log`

The outer supervisor is the sole writer of human output and `matrix.log` after
it validates an authoritative result. They are different streams. Human output
uses the section order inherited from the transparent diagnostics design and is
assembled only from capped redacted records. Each cell block has this exact
order: identity, configuration, command, input, source result, client status,
final response, parsed evidence, comparison, bounded failure diagnostics when
applicable, and verdict. The 3,194,880-byte post-serialization cell-block cap is
the checked sum of these component allowances:

| Human cell-block component | Reserved bytes |
|---|---:|
| Identity, configuration, command, input, and framing | 65,536 |
| Redacted final response | 1,048,576 |
| Source snapshot, evidence, and differences | 196,608 |
| Four bounded non-client stderr/status blocks | 262,144 |
| Two intentionally incomplete client-stream excerpts | 524,288 |
| Authoritative result projection | 65,536 |
| Serialization and redaction expansion reserve | 1,032,192 |
| **Total** | **3,194,880** |

With 65,536 bytes each for the header and footer, the planner sets

```text
human_output_max = 65,536 + planned_cells * 3,194,880 + 65,536
```

using checked arithmetic. Four required cells require at most 12,910,592 bytes,
but that example is not a ceiling. Every planned cell contributes the same
allowance; output is never silently truncated.

`matrix.log` is a machine-oriented summary, not redirected human output. It
contains one RFC 8785 canonical JSON line for the run header, one line after
each authoritative cell, and one run-trailer line. A cell line contains exactly
`record_type="cell-summary"`, `cell_ordinal`, `alias_ordinal`, `alias_sha256`,
`client`, `check`, `outcome`, `stage`, `qualification_class`, and
`qualification_reason`; it never contains alias text, prompts, responses, or
excerpts. The header contains exactly `schema`,
`record_type="matrix-header"`, `run_id`, `attempt_id`, `planned_cells`,
`harness_sha256`, and `calibration_sha256`. The trailer contains exactly
`schema`, `record_type="matrix-trailer"`, `run_id`, `outcome`,
`qualification_class`, `qualification_reason`, `run_result_sha256`, and
`manifest_body_sha256`. The outer supervisor is its only writer and uses a
1,024-byte line writer that stores at most 1,023 canonical bytes plus LF.
The `attempt_id` grammar below limits it to 128 unescaped ASCII bytes. With all
other header fields at their legal maxima, the canonical header is 435 bytes;
a maximum-field golden fixture proves that exact bound before the harness hash
is pinned.
Planning proves and enforces

```text
matrix_log_max = 1,024 * (planned_cells + 2)
```

The four required cells need 6,144 bytes, but the file grows by one bounded line
for every additional planned cell. A serialization or append that violates the
proved bound is
`finalization-failure/finalization/byte-cap(matrix-log)` and aborts the run.

### Run-Derived Aggregate Maxima

Let `A` be the number of valid aliases in the accepted discovery response and
`C = A * 4` be `planned_cells`. The response cap makes `A` finite. The planner
uses checked arithmetic and these schema-derived bounds:

```text
alias_file_max      = 65,536 + A * 32,768
plan_record_max     = 262,144 + A * 65,536 + C * 262,144
manifest_max        = 65,536 + A * 32,768 + C * 65,536
recovery_record_max = 16,384 + C * 4,096
```

The 32,768-byte alias term covers the worst RFC 8785 escaping of one 4,096-byte
alias plus its ordinal and hash. The 65,536-byte manifest cell term covers the
closed identity, exact unit and path names, result, child-identity, cutoff,
core-audit, and quiescence-record hashes. The 4,096-byte recovery term covers
the exact fixed raw names and caps for one cell. The storage-plan terms reserve
fixed schema/framing space, one bounded alias projection, and one complete
fixed-layout and storage-term projection per cell. Golden maximum-field fixtures
must prove each constant before the harness hash is pinned.

Every atomically published file has a pre-created same-cap sibling. The planner
defines `temporary_copy_max` as the checked sum of all such sibling caps,
including the run-derived values above, `human_output_max`, `matrix_log_max`,
the storage plan, the next ledger image, and each per-cell atomic cap. It emits
the ordered `(artifact, cap, multiplicity, checked_product)` terms in the plan
record. No aggregate has a repository-wide fixed cap or alias-count limit; only
checked representation and the current host's ability to reserve the computed
finite run are consulted.

Preflight evaluates these formulas at the maximum alias count and escaped bytes
that can fit in the 1,048,576-byte discovery response and proves every result is
within unsigned 64-bit arithmetic and the canonical I-JSON integer range.
Production still repeats the checks, but an arithmetic failure within an
accepted response is a harness protocol defect, not an alias policy. For a
valid accepted alias set, insufficient current host reservation is the only
capacity reason to reject the finite plan.

### Storage Plan Record

The outer supervisor is the sole writer of `plan.json`. After discovery and the
initial checked reservation for this record, its sibling, and the setup-status
pair, it creates those fixed mode `0600` regular files, writes the inactive plan,
validates its derived cap, calls `fsync`, and publishes it with
`RENAME_EXCHANGE`. The record is RFC 8785 canonical JSON with no trailing LF and
exactly these root members:
`schema`, `record_type="run-plan"`, `run_id`, `alias_count`, `planned_cells`,
`aggregate_caps`, `allowlist`, `rendered_instances`, `storage_terms`,
`filesystem_budgets`, `operation_count`, and `plan_body_sha256`. The body hash
covers the same canonical object before `plan_body_sha256` is inserted;
`plan_sha256` is the SHA-256 of the final canonical file and is stored outside
the record in the manifest and each cell input.

`aggregate_caps` contains exactly `alias_file_max`, `plan_record_max`,
`manifest_max`, `recovery_record_max`, `human_output_max`, `matrix_log_max`,
`temporary_copy_max`, and `run_storage_max`.
Each `allowlist` entry contains exactly `path_id`, `relative_path`, `file_type`,
`mode`, `cap`, `multiplicity`, and `atomic_peer`. Each `storage_terms` entry
contains exactly `filesystem_id`, `phase`, `term`, `cap`, `multiplicity`, and
`checked_product`. Each `filesystem_budgets` entry contains exactly
`filesystem_id`, `st_dev`, `allocation_quantum`, `data_later`,
`metadata_later`, and `operation_count`. Path IDs, filesystem IDs, counts, caps,
products, device IDs, and operation counts are nonnegative I-JSON integers;
multiplicities and `allocation_quantum` are positive, and every
`checked_product` equals its checked `cap * multiplicity`. `file_type` is exactly
`directory` or `regular`, `mode` is exactly the string `0700` or `0600` as
required by that type, and `atomic_peer` is another entry's `path_id` or null.
Each `rendered_instances` entry contains exactly `logical_name`, `path_id`,
`template_path`, `canonical_inputs_sha256`, `rendered_length`, and
`rendered_sha256`. Its path ID names one regular allowlist entry, its template is
one source-manifest `service-template`, and its input hash covers the closed
canonical scalar renderer inputs. Arrays use fixed generator order: root
objects first, aliases by alias ordinal, cells by cell ordinal, artifacts by the
closed artifact enum, rendered instances by unsigned logical-name bytes, and
filesystems by unsigned `st_dev`. All paths are run-relative validated strings;
no path is client-controlled. Unknown or duplicate members, terms named `other`,
inconsistent products, unsafe paths, an unlisted template, or a byte count above
`plan_record_max` invalidate setup.

The outer supervisor first consumes and independently recomputes the record for
headroom and gate checks. The setup service is the execution consumer: it
revalidates the record and hash through an already-open descriptor and creates
only listed objects. The run finalizer binds its hash into the manifest; the
classifier checks that hash before using any result. Publication or validation
failure maps through the explicit setup rows in the result table. The plan and
its atomic sibling contribute their full caps to `temporary_copy_max`,
`data_later`, metadata operations, and `run_storage_max`.

The setup service is also the sole writer of 4,096-byte `setup.json`. Its closed
canonical shape is exactly `schema`, `record_type="setup-completion"`, `run_id`,
`plan_sha256`, `directories_created`, `files_created`, `atomic_pairs_created`,
and `state="complete"`. It publishes this record through its pre-created pair
only after every listed object validates. The supervisor compares all three
counts with the plan before it accepts setup; a missing, malformed, oversize, or
failed publication uses the dedicated setup result row.

### Alias Encoding

The model-list parser accepts one UTF-8 RFC 8259 object whose `data` member is
an array. Before selecting any member, a pair-preserving decoder rejects a
duplicate member name in the root object, any model object, or any nested
object. Unknown members remain bounded and ignored only after this duplicate
check. The parser does not inherit a decoder's first-wins or last-wins policy.

Every array element must contain exactly one `id`, and each `id` must be a JSON
string containing only Unicode scalar values, encode to 1 through 4,096 UTF-8
bytes, and contain no code point whose Unicode General_Category is `Cc`, no
White_Space code point,
noncharacter, `[`, `]`, or `:`. It also rejects the exact strings `*` and
`DEFAULT`. The White_Space set is U+0009 through U+000D, U+0020, U+0085,
U+00A0, U+1680, U+2000 through U+200A, U+2028, U+2029, U+202F, U+205F, and
U+3000. The noncharacters are U+FDD0 through U+FDEF and every code point ending
in U+FFFE or U+FFFF on every plane. These explicit sets, rather than Python
`str.strip()`, are normative. The remaining characters are preserved exactly;
non-ASCII text, `/`, `&`, `#`, and `%` remain valid, so `nested/model` remains
valid.

These exclusions are the pinned preset-format constraints. In llama.cpp's
`common/preset.cpp`, `]` ends a section, optional leading ASCII whitespace is
outside the captured name, `:` triggers tag canonicalization, and `*` names the
global preset. The current `pylib/presets.py` renderer uses Python
`ConfigParser`, whose reserved section is exactly `DEFAULT`. Rejecting all
White_Space also prevents a renderer or line transport from changing the alias;
rejecting control characters prevents a multi-line or control-bearing section.
Duplicate alias detection compares decoded code-point sequences without
normalization or case folding.

This grammar is also the normative configuration grammar. Before the bounded
harness can be installed or screening can resume, one shared Python validator
must enforce it in `pylib/config.py`; preset rendering,
`setup/setup-local-llm-agents.sh`, `make check-setup`, and `make check-server`
must consume that validated projection rather than implement weaker string
checks. An existing configured alias outside the grammar is a configuration
migration error and prevents server restart and screening until the operator
renames it. Setup then verifies that the generated preset and the decoded API
set preserve every enabled alias byte-for-byte. This deliberate validation
change aligns configuration, rendering, and discovery instead of letting the
harness reject an alias that the supported configuration can emit.

The canonical alias file is one RFC 8785 JSON Canonicalization Scheme array in
response order. Each object has exactly `alias`, `alias_ordinal`, and
`alias_sha256`. The hash is over the exact validated UTF-8 alias bytes. Records
use I-JSON strings and integers only, so canonicalization has no binary-float
case. Every integer is in `-9007199254740991..9007199254740991`. The parser
rejects malformed UTF-8, lone surrogates, controls, White_Space, noncharacters,
preset-unsafe alias forms, a missing `data` or `id`, and a canonical file over
its run-derived cap. It ignores other bounded response and model members after
the duplicate-name pass; their presence does not invalidate an otherwise valid
alias.

Aliases never become path components, unit-name fragments, line-delimited
protocol fields, or shell source. Cell paths and exact unit names use the run ID
and globally unique cell ordinal. JSON records carry the exact alias, and human
output uses its RFC 8785 JSON string form. The client launcher may place the
already bounded alias in the required `--model` argument and generated JSON
configuration; no reparsing through shell words occurs.

The parser implementation hash, full Python `major.minor.micro` version, JSON
decoder implementation/version, and `unicodedata.unidata_version` are recorded
in calibration even though the character-set and canonicalization contracts
above do not depend on their defaults. Any change invalidates calibration and
reruns the duplicate-name, scalar-value, control, whitespace, noncharacter,
preset-delimiter, interoperable-integer-boundary, and canonical-byte fixtures
before a new harness hash can be pinned.

### Run-Wide Storage and Launch Gates

Before discovery, the supervisor computes the checked discovery-phase maximum
from a fixed path/file allowlist and the response caps and passes the applicable
free-space check. It pre-creates the discovery directories, current files, and
atomic siblings before releasing the discovery gate. After discovery, it
derives `A`, `C`, all aggregate maxima, and the complete run allowlist, reserves
and publishes the plan pair, and passes the full-run check before setup. A
bounded setup service then pre-creates every
remaining directory and regular file before model preparation. No model-work
path may call a file- or directory-creation primitive.

Each mutable record has fixed `.current` and `.next` names. Publication writes
and `fsync`s the inactive pre-created file, validates it, and uses
`renameat2(RENAME_EXCHANGE)` so both names continue to exist. Preflight proves
exchange support on every backing filesystem. This avoids later inode
allocation; later work can only update extents and metadata for known regular
files or delete exact names. The quarantine parent and recovery-record pair are
also pre-created, and the quarantine target is on the run directory's
filesystem so the directory move is atomic.

For each backing filesystem, the fixed-layout generator emits checked
`data_later` and `metadata_later` terms. With measured allocation quantum `B`,
define `alloc_B(n) = ceil(n / B) * B`. `data_later` is the checked sum of
`alloc_B(cap) * multiplicity` for every current file and atomic sibling that can
coexist, every run-derived aggregate, and a same-size copy-on-write extent for
each later update. Let `O` be the generated count of all remaining directory,
file, extent, exchange, truncation, `fsync`, exact-name deletion, and quarantine
rename operations. The deliberately conservative metadata reserve is

```text
metadata_later = data_later + B * O
```

This reserves one logical metadata byte for every possible live or stale data
byte plus one allocation block per fixed metadata operation. The generated
ordered terms, multiplicities, operation count, and checked products are part of
the plan record; there is no sampled or unspecified `other` term.

The checked run formula includes `run_fixed_max`, `A * alias_storage_max`,
`C * retained_cell_max`, one serial `active_cell_extra_max`, all aggregate
maxima, `temporary_copy_max`, and the later data/metadata reserves. Thus
`run_storage_max` grows with every discovered alias and cell instead of relying
on a fixed aggregate cap. Paths on different filesystems receive separate
budgets by `st_dev`. Bubblewrap tmpfs bytes belong to the memory and workspace
budget, not this host-backed storage budget.

On Btrfs, inode counters are never consulted. With `LC_ALL=C`, the supervisor
runs `btrfs filesystem usage --raw <path>` and strictly parses `Device
unallocated`, `Free (estimated)` including its `min` value, every `Data` and
`Metadata` profile's `Size` and `Used`, and the reported data and metadata
ratios, parsed as exact decimal rationals rather than binary floats. It excludes
the global reserve. Let `Df` and `Mf` be allocated logical
free bytes for the applicable data and metadata profiles, `U` be unallocated
physical device bytes, and `Rd` and `Rm` be twice the remaining logical
`data_later` and `metadata_later` requirements. It computes

```text
data_shortfall_raw     = max(0, Rd - Df) * data_ratio
metadata_shortfall_raw = max(0, Rm - Mf) * metadata_ratio
```

with checked arithmetic and requires their sum to be at most `U`. It also
requires the reported minimum estimated free bytes to be at least `Rd` and each
already allocated profile plus its assigned unallocated share to satisfy its
logical requirement. Mixed profiles are summed without double-counting `U`;
an unknown profile, ratio, locale, field, or changed filesystem fails the
measurement. This separately reserves data and metadata while allowing Btrfs
to allocate new chunks from real unallocated device space.

The tested non-Btrfs fallback uses `statvfs`: `f_bavail * f_frsize` must be at
least twice the combined remaining block-rounded data and metadata maximum.
Before pre-creation, positive `f_favail` must also cover every not-yet-created
allowlisted inode; after pre-creation, no inode reserve is needed. A non-Btrfs
filesystem with indeterminate inode counters fails pre-creation rather than
silently passing. Fixtures cover Btrfs `f_files=f_ffree=0`, Data `single` plus
Metadata `DUP`, insufficient data, insufficient metadata, shared-unallocated
double counting, and the non-Btrfs block/inode fallback.

The supervisor repeats the applicable check after pre-creation and immediately
before every cell gate, using only the maximum remaining extent and metadata
updates. A failed command, parse, arithmetic check, exchange probe, changed
filesystem, or insufficient current headroom records
`operation-failed/run-setup/headroom` and starts no more work. The finite byte
caps and fixed names remain authoritative after release.

### Memory and Task Calibration

The repository will not contain machine-specific `MemoryMax`, `TasksMax`, or
workspace values. No calibration probe runs without limits. Before the first
discovery or client probe, preflight measures:

- host `MemAvailable` and `memory.current`/`memory.max` at every finite cgroup
  ancestor;
- `pids.current`/`pids.max` at every finite cgroup ancestor, finite
  `RLIMIT_NPROC` headroom for the current UID, and system PID headroom;
- current RSS and task counts for the outer supervisor process set; and
- the fixed supervisor, cell, control-service, finalizer, and record capacities
  from the reviewed process graph and byte-cap table.

Effective memory headroom `Hmem` is the minimum of host `MemAvailable` and each
finite ancestor's `memory.max - memory.current`. Effective task headroom
`Htasks` is the minimum of the finite task measurements. Bootstrap supervisor
reserves `Smem` and `Stasks` equal twice the measured outer usage plus the fixed
not-yet-running capacities. The checked sum of simultaneously live bootstrap
cell and control limits must fit within `floor(Hmem / 2) - Smem` and
`floor(Htasks / 2) - Stasks`. Each role receives at least its complete reviewed
fixed process and buffer graph; a nonpositive remainder or insufficient role
minimum fails preflight. Bootstrap workspace size is one quarter of the cell
bootstrap memory limit, rounded down to the page size, and must fit every seeded
workspace file at its cap.

Preflight applies those effective bootstrap properties to discovery, setup,
cleanup, and every first cell/control probe. It verifies the properties before
gate release and runs the same control-service observer used in production. A
bootstrap limit event means calibration failed safely; it never causes an
unbounded retry or a larger inferred limit.

Successful base-model probes record:

- systemd `MemoryPeak` for each cell;
- cgroup `pids.peak` for each cell;
- systemd `MemoryPeak` and cgroup `pids.peak` for each sibling control service;
- peak allocated bytes in the single private client tmpfs;
- peak broker tasks, buffers, open upstream sockets, and request/response bytes;
- peak outer-supervisor memory and tasks;
- effective memory and task headroom before each probe and gate release; and
- llama.cpp server-cgroup memory and task values before, during, and after each
  preparation and inference request, including the observed peak growth.

The server measurements are report fields, not terms in a harness service limit
or reserve. Preflight separately verifies the existing server budget, disabled
automatic fitting/offload behavior, and one-slot configuration. `Hmem` and
`Htasks` reflect the server's current consumption through host and ancestor
measurements, but the harness makes no claim about future llama.cpp-internal
allocation growth.

Calibration never tries to infer which allocation was already represented in a
peak. For each client, `WorkspaceSeedMax` is the checked sum of every
wrapper-created file seeded into the client tmpfs. `WorkspaceMax` is
`round_up_page(2 * largest_healthy_workspace_peak + WorkspaceSeedMax)`.

`CellVariableMax` is generated mechanically from the reviewed fixed-layout
manifest. It adds every variable allocation at its full cap even when it was
already resident at the measured peak. It contains exactly:

- one prompt, generated configuration, source response, raw final assistant
  text, redacted final response, and cell-scoped credential;
- both raw client-stream prefixes and both serialized excerpts;
- one snapshot, evidence, differences, proof bundle, candidate, input, policy,
  phase, helper-status, core-audit, and causal-cutoff shared-state allowance;
- one reused stage-stderr allowance and one temporary atomic sibling for every
  fixed record that can coexist with its destination;
- the complete `WorkspaceMax`, not merely its seeded bytes; and
- each fixed wrapper, parser, redactor, capture, monitor, and proof buffer from
  the cell process-graph manifest, with explicit byte size and multiplicity.

`ControlVariableMax` is generated by the same method and contains the real
credential, broker status and terminal entries, request-header, request-body,
response, correlation ID, stream-lookup, task-binding, raw-slot, projected-slot,
resource-record, and quiescence-record
allowances, the selected-child identity, causal-cutoff mapping and record,
core-audit record, every atomic sibling that can coexist, and every fixed broker
and observer buffer. No broker or observer allocation is charged to the cell
limit, and no control allocation is omitted from the simultaneous host-headroom
gate.

The implementation emits the ordered list of `(term, cap, multiplicity,
checked_product)` values into the calibration record. No term may be named
`other`, `already occupied`, or depend on sampling the instant of `MemoryPeak`.
With `Pcell` and `Pcontrol` equal to the largest healthy peaks for each role,
production uses

```text
CellMemoryMax    = round_up_MiB(2 * Pcell + CellVariableMax)
ControlMemoryMax = round_up_MiB(2 * Pcontrol + ControlVariableMax)
```

These deliberately double-count allocations that contributed to each peak.
`CellTaskCapacity` covers wrapper, sandbox, client, capture, parser, redactor,
monitor, proof, and diagnostic tasks. `ControlTaskCapacity` covers broker and
observer tasks. Both reviewed graphs include mutually sequential helpers. With
`Pcell_tasks` and `Pcontrol_tasks` equal to the largest healthy role peaks,
production uses

```text
CellTasksMax    = 2 * Pcell_tasks + CellTaskCapacity
ControlTasksMax = 2 * Pcontrol_tasks + ControlTaskCapacity
```

All additions and multiplications are checked unsigned 64-bit operations
internally. Every value published to canonical JSON must additionally be at most
`9007199254740991`. Memory rounds upward to MiB; tasks remain whole numbers. The
calibration record contains the full manifests, measured peaks, formulas,
intermediate sums, and results, so two implementations given the same record
derive identical limits.

Production `Smem` and `Stasks` replace their bootstrap values with twice the
largest healthy outer-supervisor memory and task peaks plus fixed
not-yet-running capacities. Preflight rejects a service
limit and outer reserve whose sum exceeds half the minimum corresponding
effective headroom measured across successful probes. It binds all measured
terms and calibrated values to the harness hash, client versions, systemd
version, kernel cgroup version, Bubblewrap version, parser runtime, llama.cpp
image digest/build/revision, and base-model fingerprint.
A candidate run refuses stale or mismatched calibration. Discovery, setup, and
cleanup services use the largest applicable calibrated harness-side limits after
calibration and the bootstrap limits before it, so every transient workload
always has effective limits.

Immediately before every production gate release, let `Mc` and `Mk` be the
remaining cell and control memory allowances, and let `Tc` and `Tk` be their
remaining task allowances. Current effective headroom must satisfy
`2 * (Mc + Mk + Smem) <= Hmem` and
`2 * (Tc + Tk + Stasks) <= Htasks`. Failure records
`operation-failed/run-setup/headroom`, kills the gated unit, and aborts before
source or client work. `MemorySwapMax=0` prevents a runaway from shifting
pressure to swap. A cgroup OOM becomes `resource-limit/memory`; the external
observer, not `TasksMax` alone, turns a pids limit event into
`resource-limit/tasks` and kills the exact cell cgroup. A control-service limit
is instead an infrastructure-invalid boundary failure and aborts the matrix.

## Client Sandbox

The trusted wrapper starts only the client process and its Bash descendants in
Bubblewrap. The sandbox:

- presents the installed runtime and client files read-only;
- creates exactly one writable mount with
  `--size WorkspaceMax --perms 0700 --tmpfs /client`, places `HOME`, XDG,
  `TMPDIR`, configuration, data, state, and work subdirectories beneath it, and
  remounts the sandbox root read-only after setup;
- creates no other writable bind, tmpfs, overlay, or device mount; any private
  `/proc` and `/dev` mounts are populated before the gate and explicitly made
  read-only with `--remount-ro` because remounting the root is not recursive;
- uses explicit `--unshare-user --disable-userns --cap-drop ALL`; preflight
  requires Bubblewrap's `--assert-userns-disabled` check to pass before client
  exec;
- exposes only the fresh cell-broker token required by that client; the real
  llama.cpp API key remains outside Bubblewrap;
- shares the network namespace because the client must reach the loopback cell
  broker and public source; the configured provider never names the real
  inference endpoint;
- creates a new PID namespace and mounts a private `/proc`;
- exposes `/sys/fs/cgroup` read-only;
- omits the supervisor exchange directory;
- omits `/run/user/$UID/bus`, `/run/user/$UID/systemd`, and other user-manager
  transports;
- omits `/dev/log`, `/run/systemd/journal/socket`, and
  `/run/systemd/journal/stdout`;
- inherits the verified no-core seccomp filter and zero core limit; and
- uses `--die-with-parent` and remains in the cell service cgroup.

One tmpfs enforces `WorkspaceMax` across all client-writable directories; no
per-directory mount can consume an additional allowance. With every capability
dropped, creating a mount namespace requires a new user namespace, and
`--disable-userns` prevents that namespace. Before releasing the Bubblewrap sync
gate, the trusted launcher obtains the sandbox child PID from Bubblewrap's
status descriptor, parses that process's bounded `/proc` mountinfo, and requires
`/client` to be the only mount whose effective flags permit writes. An unknown
mount, a response over 65,536 bytes, a writable `/proc` or `/dev`, or another
writable mount is a boundary violation. The launcher then starts a workspace
monitor outside the child's PID namespace. The monitor samples the exact
`/client` mount through the child's `/proc` root and records its peak allocated
blocks. If the mount reports no allocatable data blocks or inodes, the monitor
seals `resource-limit/workspace`, terminates the Bubblewrap process, and lets
`KillMode=control-group` clean the service. Monitor loss is a boundary violation.
The kernel tmpfs size remains the hard aggregate bound even if monitor
scheduling is delayed.

Preflight must run the exact Pi and OpenCode commands through this sandbox and
prove source DNS, HTTPS, the broker endpoint, configuration, capture, parsing,
and normal completion still work. It proves that the cell token cannot
authenticate directly to llama.cpp, every supported-client inference request
appears in the broker record, and abrupt cell death leaves the sibling broker
alive to finish or cancel active and queued llama.cpp work. It also proves that
`setsid` stays in the cell cgroup and direct cgroup migration,
`systemctl --user`, `systemd-run --user`, and access through known user-manager
sockets fail. Broker death is a deliberately injected infrastructure-failure
fixture, never a successful cancellation path.
Separate fixtures fill each writable subdirectory and prove their combined
allocated bytes cannot exceed the one `WorkspaceMax` mount.
Architecture-specific fixtures invoke `unshare(CLONE_NEWUSER)`,
`unshare(CLONE_NEWNS)`, `clone(CLONE_NEWUSER)`, and
`clone3(CLONE_NEWUSER)`, then attempt a nested tmpfs mount. They must fail while
ordinary client process and thread creation still works. A successful nested
namespace, any effective capability, an unsupported `--disable-userns`, or an
initial or nested writable mount other than `/client` blocks qualification.

These checks establish a hardened resource boundary for harness-side supported
client processes, files, and requests. They do not bound llama.cpp internals or
establish a security boundary against arbitrary same-UID code, kernel defects,
inherited unknown file descriptors, or untested IPC paths. Any failed hardening
probe blocks qualification.

## File Protocol

### Input

The outer supervisor publishes one immutable `input.json` through its
pre-created atomic pair before service launch.
It contains schema version, run ID, alias ordinal, cell ordinal, client, exact
model alias, alias SHA-256, check name, policy caps, harness hash, client
version, calibration hash, plan SHA-256, server-identity SHA-256, cutoff shared
object identity, and an input hash. The wrapper re-encodes the alias
under the canonical contract, verifies its hash and alias ordinal against the
canonical alias file, recomputes the cell ordinal from the fixed client/check
indexes, and rejects unknown keys, unknown enums, duplicate JSON keys, invalid
strings, or any mismatch before it writes `ready.json`. `input_sha256` hashes
the RFC 8785 canonical input body before that field is inserted, avoiding a hash
cycle. The supervisor can retain this hash in an early cell result even when the
final input-with-hash publication crosses its cap; no service starts on that
path.

Credentials use systemd credentials or pre-opened private files. They never
appear in argv, the input record, a result, or a manifest. The ephemeral broker
token may appear only in the generated private client configuration and broker
credential; redactors treat it as a secret, and final cleanup removes both.

### Phase State

The wrapper is the only writer of `phase.json`. It writes the pre-created
inactive sibling, validates size, calls `fsync`, and exchanges it atomically
with `renameat2(RENAME_EXCHANGE)`. Each transition increments `seq` through this
closed forward-only enum:

```text
wrapper-start
gate-wait
source-fetch
source-parse
client-config
capture-setup
client-launch
client-run
final-response-parse
evidence-parse
evidence-compare
diagnostics
candidate-finalize
complete
```

The capture, proof, and workspace helpers each own a separate atomic status
record. The control-service resource observer alone writes the supervisor-side
`resource.json`; the cell and sandbox cannot see its directory. An overflow
helper seals its byte count and overflow flag before it notifies the wrapper.
The client launcher uses an exec-status handshake, so `client-start` can
distinguish successful `execve` from exit status 127.

The protocol does not compare filesystem timestamps or impose a general total
order across status writers. The causal cutoff below is the sole ordered
cross-writer operation. Fixed outcome precedence resolves concurrent resource
events after that cutoff. Incomplete, malformed, contradictory, or missing
status records fail closed.

Each phase write records entry into the named phase, not completion of it, and
contains `phase`, `seq`, and `last_completed_stage`. Before `source-fetch`,
`last_completed_stage` is `wrapper-start`. On entry to each later normal phase,
it is the result-stage enum for the immediately preceding completed phase; it
does not advance while the current phase is active. `candidate-finalize` and
`complete` both retain the last completed normal result stage. Every result-table
reference to `last complete stage` means this stored value. A missing valid
phase record before the first write selects `wrapper-start`; an invalid or
backward record selects `boundary-violation/wrapper-start/protocol`.

### Causal Stream Cutoff

Before service launch, the supervisor creates one fixed 4,096-byte mode `0600`
shared-state file for the cell, opens it by exact descriptor in the supervisor,
control service, wrapper, and trusted helpers, and removes every inherited copy
before client exec. Bubblewrap cannot see its path or descriptor. The
hash-bound layout contains a process-shared robust mutex, a `closed` bit, a
terminal event ordinal and enum, and checked committed-byte and complete-record
counters for transcript and stderr. Initial state is open with zero counters.

Each capture helper reads at most one fixed-size block before taking the shared
mutex. While holding it, the helper first checks `closed=false`, appends the
block to its bounded raw prefix and adapter framer, assigns every complete
adapter record its half-open stream byte range `[start,end)`, increments the
committed counters, and releases the mutex. If the latch is already closed, it
stores and parses none of that block. A block read concurrently with a terminal
event is therefore either wholly committed before the event or wholly excluded;
there is no partially eligible record.

The outer timeout observer, control-service runtime/resource observer, broker
overflow path, workspace monitor, capture-overflow helper, and client launcher
are the only terminal producers. A producer takes the same mutex, and the first
producer sets `closed=true`, assigns its predeclared event ordinal and one of
`client-terminal`, `transcript-limit`, `stderr-limit`, `broker-limit`,
`workspace-limit`, `memory-limit`, `tasks-limit`, or `timeout`, snapshots both
committed counters, and releases the mutex. Later producers record their events
without changing the cutoff. The control observer independently arms the cell
deadline; a manager timeout without a matching closed latch is a protocol
failure. `EOWNERDEAD`, an invalid layout, a counter rollback, overflow, or a
producer that cannot acquire and seal the latch forbids behavior attribution
and selects `boundary-violation/protocol`.

For `client-terminal` only, the launcher first reaps the client and waits for
both capture helpers to acknowledge EOF and commit all preceding pipe bytes;
then it closes the latch. A timeout, overflow, or resource producer closes the
latch immediately and never waits for a stream drain. This distinction lets a
normal complete stream include every byte emitted before exit without admitting
bytes committed after a safety event.

After cell inactivity, the control service is the sole writer of canonical
`cutoff.json`. It contains exactly `schema`,
`record_type="causal-cutoff"`, `run_id`, `cell_ordinal`, `event_ordinal`,
`event`, `transcript_eligible_bytes`, `stderr_eligible_bytes`,
`transcript_record_count`, `stderr_record_count`, and
`producer_records_sha256`. It is capped at 4,096 bytes, atomically published,
and hashed. The finalizer checks it against the capture statuses, terminal
producer records, and raw accepted-byte counts. The behavior evaluator may use
only complete adapter records whose `end` is at most the corresponding eligible
byte count. A record parsed later remains eligible because its complete bytes
were committed earlier; a recovered, partial, or post-cutoff record is never
eligible. Complete non-resource attribution uses the same record with
`event=client-terminal`, so both proof bases have one cutoff rule.

Each producer record contains exactly `schema`, `producer`, `event_ordinal`,
`event`, `observed_value`, and `won_cutoff`. `producer` is one of
`outer-timeout`, `control-observer`, `broker`, `workspace-monitor`,
`transcript-capture`, `stderr-capture`, or `client-launcher`; `observed_value`
is the checked byte count, controller counter, deadline nanoseconds, or wait
status defined by that producer. The hash domain is the RFC 8785 canonical array
of all records ordered by event ordinal and then producer enum, with no trailing
LF. The winning producer record is mandatory; a duplicate producer or event
ordinal is rejected.

### Candidate and Final Result

The wrapper writes `candidate.json` only after normal child reaping and online
diagnostic work. It never claims cgroup cleanup, because only the outer
supervisor can observe the service after wrapper exit. `candidate.json` is a
closed internal record with exactly `schema`, `record_type="candidate"`,
`input_sha256`, `alias_sha256`, `identity`, `outcome`, `stage`, `detail`,
`client`, `streams`, and `proof`. It uses the field definitions below, forbids
`qualification`, `service`, `server_boundary`, and `cleanup`, and is never an
authoritative result. A missing or invalid candidate follows the wrapper-failure
row rather than creating another candidate shape.

After cell inactivity, request quiescence, and control-service cleanup, the
outer supervisor writes the sole authoritative cell `result.json`. A
representative cell result is:

```json
{
  "schema": 1,
  "record_type": "cell",
  "input_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "alias_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "identity": {"cell_ordinal": 0, "alias_ordinal": 0, "client": "pi", "model": "gemma4", "check": "weather"},
  "outcome": "timeout",
  "stage": "client-run",
  "detail": {"kind": "deadline"},
  "qualification": {"class": "infra-invalid", "reason": "resource-cause-ambiguous"},
  "service": {
    "state": "inactive",
    "unit": "llm-env-agent-0123456789abcdef0123456789abcdef-c00000000000000000000.service",
    "invocation_id": "0123456789abcdef0123456789abcdef",
    "cgroup": "/user.slice/example/cell",
    "manager_result": "timeout",
    "memory_peak_bytes": 123456789,
    "pids_peak": 17,
    "pids_events_max": 0,
    "workspace_peak_bytes": 123456
  },
  "client": {"state": "exited", "exec_confirmed": true, "completion_recorded": false, "exit_code": null, "signal": 15},
  "streams": {
    "transcript": {"state": "complete", "accepted_bytes": 1234, "overflow": false},
    "stderr": {"state": "complete", "accepted_bytes": 0, "overflow": false}
  },
  "proof": {"basis": "bounded-resource-proof", "model_behavior_attributed": false, "kind": null, "adapter": "pi-0.82.1", "cutoff_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd", "bundle_sha256": null},
  "server_boundary": {"state": "quiescent", "accepted_requests": 3, "terminal_requests": 3, "open_upstreams": 0, "idle_samples": 2},
  "cleanup": {"state": "complete", "inactive": true, "cgroup_empty": true}
}
```

A successful aggregate run record is:

```json
{
  "schema": 1,
  "record_type": "run",
  "attempt_id": "greedy-screening-1-bounded-1",
  "outcome": "pass",
  "stage": "finalization",
  "detail": null,
  "qualification": {"class": "model-failure", "reason": "required-cell-model-failure"},
  "planned_cells": 4,
  "manifest_body_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
}
```

The emergency variant is:

```json
{
  "schema": 1,
  "record_type": "run-failure-marker",
  "attempt_id": "greedy-screening-1-bounded-1",
  "outcome": "finalization-failure",
  "stage": "finalization",
  "detail": {"kind": "io"},
  "qualification": {"class": "infra-invalid", "reason": "run-infrastructure-failure"},
  "last_run_record_sha256": null
}
```

The examples are expanded for readability. Files and hashes use the canonical
serialization contract below. The harness has three closed authoritative record
variants:

| `record_type` | Required shape |
|---|---|
| `cell` | Contains exactly `schema`, `record_type`, `input_sha256`, `alias_sha256`, `identity`, `outcome`, `stage`, `detail`, `qualification`, `service`, `client`, `streams`, `proof`, `server_boundary`, and `cleanup`. No field is omitted for an early or synthetic cell. |
| `run` | Contains exactly the fields in the run example. It forbids every cell-only field. `planned_cells` is a nonnegative integer after planning and `null` before planning; `manifest_body_sha256` is a hash after a canonical body exists and `null` before one exists. |
| `run-failure-marker` | Contains exactly the fields in the emergency example and forbids every cell-only field. `last_run_record_sha256` is a hash when a run record was sealed and `null` otherwise. |

Every string contains Unicode scalar values and no U+0000, lone surrogate, or
Unicode noncharacter. Every JSON integer is in
`-9007199254740991..9007199254740991`, and every count is in
`0..9007199254740991`; no authoritative record contains a fractional number.
`schema` is integer `1`. Every SHA-256 is exactly 64 lowercase hexadecimal
digits. `invocation_id` is exactly 32 lowercase hexadecimal digits when
non-null. `attempt_id` matches
`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`; it is therefore 1 through 128 unescaped
ASCII bytes. The following cell subobjects are closed:

| Object | Exact fields and legal values |
|---|---|
| `identity` | `cell_ordinal` and `alias_ordinal` are nonnegative integers; `client` is `pi` or `opencode`; `model` is the exact validated alias; `check` is `weather` or `fx`. The ordinal formula, alias hash, input, and manifest must agree. |
| `qualification` | Exactly `class` and `reason`, using the closed enums and legal combinations below. |
| `service` | Exactly `state`, `unit`, `invocation_id`, `cgroup`, `manager_result`, `memory_peak_bytes`, `pids_peak`, `pids_events_max`, and `workspace_peak_bytes`. `state` is `not-started`, `activation-failed`, or `inactive`; `unit` is always the planned exact name. `manager_result` is `not-started`, `success`, `exit-code`, `signal`, `timeout`, `oom-kill`, `resources`, or `protocol`. |
| `client` | Exactly `state`, `exec_confirmed`, `completion_recorded`, `exit_code`, and `signal`. `state` is `not-started`, `exec-failed`, `exited`, or `completed`. |
| `streams` | Contains exactly `transcript` and `stderr`. Each contains exactly `state`, `accepted_bytes`, and `overflow`; `state` is `not-started`, `complete`, `overflow`, or `failed`. |
| `proof` | Exactly `basis`, `model_behavior_attributed`, `kind`, `adapter`, `cutoff_sha256`, and `bundle_sha256`. `basis` is `not-evaluated`, `complete-attribution`, or `bounded-resource-proof`. |
| `server_boundary` | Exactly `state`, `accepted_requests`, `terminal_requests`, `open_upstreams`, and `idle_samples`. Request counts include preparation and inference requests. `terminal_requests` counts only a complete upstream response or a live-broker cancellation action followed by absence of its bound task in the sealed quiescence proof. `state` is `not-required`, `quiescent`, or `failed`. |
| `cleanup` | Exactly `state`, `inactive`, and `cgroup_empty`. The booleans cover every created cell and control service. `state` is `not-required`, `complete`, or `failed`. |

Nullability and cross-field rules are exhaustive:

- `service.state=not-started` requires null invocation ID, cgroup, all four peak
  or event counters, and `manager_result=not-started`.
- `service.state=activation-failed` permits an invocation ID and cgroup only
  when the manager supplied them, requires null unavailable counters, and
  forbids `manager_result=not-started` or `success`.
- `service.state=inactive` requires non-null invocation ID, cgroup, manager
  result, and all counters. A synthetic unavailable cell uses `not-started`;
  there is no service-less variant.
- `client.state=not-started` requires both booleans false and both terminal
  values null. `exec-failed` requires `exec_confirmed=false` and exactly one of
  exit code or signal. `exited` and `completed` require `exec_confirmed=true`
  and exactly one of exit code or signal; `completed` additionally requires
  `completion_recorded=true` and exit code zero.
- A `not-started` stream has zero bytes and no overflow. A `complete` or
  `failed` stream has bytes within its cap and no overflow. An `overflow`
  stream has accepted bytes exactly equal to its cap and `overflow=true`.
- `proof.basis=not-evaluated` requires false attribution and null kind, adapter,
  cutoff hash, and bundle hash. Either evaluated basis requires the exact pinned
  adapter and a valid causal-cutoff hash.
  False attribution requires null kind and bundle hash. True attribution
  requires a non-null proof kind and bundle hash. `failure-to-converge` requires
  `complete-attribution`; `bounded-resource-proof` forbids it and
  `forbidden-resource-control`. The complete basis requires complete
  non-overflowing streams and a non-resource terminal state; the bounded basis
  requires a resource terminal state or a truncated relevant stream.
- `server_boundary.state=not-required` requires all four counters zero and is
  legal only before server preparation was attempted, when client exec was not
  confirmed and the broker accepted no inference request. `quiescent` requires
  accepted and terminal counts equal, zero open upstreams, a healthy broker at
  seal time, the preparation request terminal when preparation occurred, and at
  least two idle samples from the bound model child with no active or deferred
  bound task.
  `failed` records the observed bounded counts and cannot produce `pass` or
  `model-failure`. A cell resource event with any other server-boundary state is
  invalid.
- `cleanup.state=not-required` requires both booleans true and is legal only
  when no service cgroup was created. `complete` requires both true. `failed`
  records each proved Boolean; an unproved fact is false and cannot produce
  `pass` or `model-failure`.

For `detail`, `null` is legal only for pass rows. `exit` has exactly
`{"kind":"exit","code":N}` with `N` in `0..255`; `signal` has exactly
`{"kind":"signal","number":N}` with a positive signal number; `byte-cap`
has exactly `{"kind":"byte-cap","artifact":A}` where `A` is the closed
artifact enum below. Every other detail is exactly `{"kind":K}`. In
particular, non-stream details never carry `stream`, and the canonical timeout
shape is exactly `{"kind":"deadline"}` as shown above.

All records use RFC 8785 JSON Canonicalization Scheme with I-JSON strings and
integers, no trailing LF, and no insignificant whitespace. Duplicate member
names are rejected before canonicalization at every nesting depth. Atomic
publication validates this exact canonical byte sequence against the named cap,
hashes those bytes, calls `fsync`, and exchanges the pre-created files. Tests
parse each pretty example, assert its exact legal shape, canonicalize it to a
golden byte string, and reject the timeout example if a `stream` member is
added. Canonical fixtures accept both integer endpoints and adjacent permitted
Unicode values, and reject `-9007199254740992`, `9007199254740992`, every lone
surrogate, U+FDD0, U+FDEF, and U+nFFFE/U+nFFFF for every plane 0 through 16.

A successful run record uses `outcome=pass` and `stage=finalization`; its
qualification carries the aggregate required-cell classification. A failure
before aliases or cell identities exist uses a `run` record with
`planned_cells=null` and `manifest_body_sha256=null`. The manifest body hash
covers its canonical content before the `run_result_sha256` field is added;
this avoids a hash cycle. If the run record or manifest cannot be published,
the supervisor writes only the closed failure-marker variant through its
pre-created atomic pair. Its 128-byte attempt-ID bound and fixed remaining
fields have a statically checked canonical maximum below 4,096 bytes. A marker
serialization that
violates that invariant or an I/O failure while publishing it leaves no claimed
authoritative record, exits nonzero, and is classified infrastructure-invalid
by the outer experiment driver.

The finalizer validates record type, identity, input, plan, and server-identity
hashes, cell and control unit identities, invocation IDs, enums, legal field
combinations, stream and cutoff counts, candidate and proof hashes, broker task
bindings, server quiescence, the core-audit record, resource counters, and
cleanup. Every object rejects unknown and duplicate keys. Unknown,
contradictory, noncanonical, or variant-forbidden data cannot produce `pass` or
`model-failure`.

## Result Contract

### Closed Enums

`outcome` has these values:

```text
pass
unavailable
operation-failed
invalid-output
client-exit
timeout
output-limit
resource-limit
mismatch
boundary-violation
wrapper-failure
cleanup-failure
finalization-failure
```

`stage` has these values:

```text
run-setup
model-list-fetch
model-list-parse
client-discovery
wrapper-start
source-fetch
source-parse
client-config
capture-setup
client-launch
client-run
final-response-parse
evidence-parse
evidence-compare
diagnostics
cleanup
finalization
```

`detail.kind` has these values:

```text
exit
signal
deadline
byte-cap
memory
tasks
workspace
invalid-json
invalid-schema
empty
missing
duplicate
protocol
io
manager
manager-delay
escape
difference
not-installed
headroom
redaction
proof
```

`qualification.class` has exactly `pass`, `model-failure`, and
`infra-invalid`. `candidate-regression` remains an outer sampler-experiment
classification and never appears in a harness record. `qualification.reason`
has these values:

```text
evidence-match
generated-action-violation
generated-action-exit
failure-to-converge
evidence-mismatch
invalid-final-evidence
resource-cause-ambiguous
client-exit-cause-ambiguous
client-unavailable
cell-infrastructure-failure
run-infrastructure-failure
required-cell-infra-invalid
required-cell-model-failure
required-cells-pass
```

`proof.kind` is `null` or one of:

```text
repeated-direct-network-command
altered-direct-network-command
forbidden-resource-control
generated-action-exit
failure-to-converge
```

The `byte-cap` artifact enum is:

```text
input
policy
credential
phase-status
helper-status
resource-status
setup-status
server-identity
plan
cutoff-state
cutoff-record
core-audit
model-list-response
alias-file
source-response
stage-stderr
prompt
client-configuration
broker-request-headers
broker-request-body
broker-response
broker-status
stream-lookup
task-binding
slots-response
slot-projection
server-quiescence
transcript
stderr
stream-excerpt
final-assistant
final-response
snapshot
evidence
differences
proof-bundle
candidate
cell-result
run-result
calibration
human-cell-block
human-output
matrix-log
manifest
ledger
legacy-ledger
failure-marker
quarantine-record
```

The detail and proof shapes use the closed rules in the record schema. An
evaluated adapter is exactly `pi-0.82.1` or `opencode-1.18.10`. Only a cell with
confirmed client exec and complete adapter records whose ranges end at or before
the validated causal cutoff may set `model_behavior_attributed=true`.

The following table is the exhaustive scope, outcome, stage, detail, and
default-qualification relation. A brace-delimited cell is the complete allowed
set; `null` is a JSON null detail. The behavior-attribution override below is
the only rule that changes a cell's default qualification.

| Scope and condition | Outcome | Stage | `detail.kind` | Default qualification | Continue after cleanup? |
|---|---|---|---|---|---|
| Run lock, configuration, missing credential, calibration-schema, current-headroom, or checked-budget failure | `operation-failed` | `run-setup` | `{invalid-schema,protocol,io,manager,headroom}` | `infra-invalid/run-infrastructure-failure` | No |
| A required systemd credential crosses its cap | `output-limit` | `run-setup` | `byte-cap(credential)` | `infra-invalid/run-infrastructure-failure` | No |
| Calibration record crosses its cap | `output-limit` | `run-setup` | `byte-cap(calibration)` | `infra-invalid/run-infrastructure-failure` | No |
| Bootstrap or calibration probe reaches memory, task, or workspace limit | `resource-limit` | `run-setup` | `{memory,tasks,workspace}` | `infra-invalid/run-infrastructure-failure` | No |
| No-core launcher, inherited filter, core-pattern, coredump audit, or credential-free server-identity preflight fails | `operation-failed` | `run-setup` | `protocol` | `infra-invalid/run-infrastructure-failure` | No |
| Server-identity record crosses its cap | `output-limit` | `run-setup` | `byte-cap(server-identity)` | `infra-invalid/run-infrastructure-failure` | No |
| A non-cell harness role becomes dumpable or a matching coredump handler, payload, file, or invalid core-audit record is observed | `boundary-violation` | `{run-setup,cleanup,finalization}` from active run phase | `{protocol,byte-cap(core-audit)}` | `infra-invalid/run-infrastructure-failure` | No; block future attempts |
| Discovery service activation fails or exceeds `TimeoutStartSec` | `operation-failed` | `run-setup` | `manager` | `infra-invalid/run-infrastructure-failure` | No |
| Discovery service reaches its runtime bound | `timeout` | `model-list-fetch` or `model-list-parse` from phase | `deadline` | `infra-invalid/run-infrastructure-failure` | No |
| Discovery service remains active beyond its runtime, stop interval, and calibrated manager allowance | `boundary-violation` | `model-list-fetch` or `model-list-parse` from phase | `manager-delay` | `infra-invalid/run-infrastructure-failure` | No |
| Discovery service reaches memory or task bound | `resource-limit` | `model-list-fetch` or `model-list-parse` from phase | `{memory,tasks}` | `infra-invalid/run-infrastructure-failure` | No |
| Model-list request or capture fails | `operation-failed` | `model-list-fetch` | `{exit,signal,io,protocol}` | `infra-invalid/run-infrastructure-failure` | No |
| Model-list response crosses its cap | `output-limit` | `model-list-fetch` | `byte-cap(model-list-response)` | `infra-invalid/run-infrastructure-failure` | No |
| Model list or alias set is malformed, empty, duplicate, or cannot fit the checked plan | `invalid-output` | `model-list-parse` | `{invalid-json,invalid-schema,empty,duplicate,headroom}` | `infra-invalid/run-infrastructure-failure` | No |
| Canonical alias file crosses its cap | `output-limit` | `model-list-parse` | `byte-cap(alias-file)` | `infra-invalid/run-infrastructure-failure` | No |
| Canonical storage plan crosses its derived cap | `output-limit` | `run-setup` | `byte-cap(plan)` | `infra-invalid/run-infrastructure-failure` | No |
| Run: setup service activation fails or exceeds `TimeoutStartSec` | `operation-failed` | `run-setup` | `manager` | `infra-invalid/run-infrastructure-failure` | No |
| Run: setup service reaches its runtime bound | `timeout` | `run-setup` | `deadline` | `infra-invalid/run-infrastructure-failure` | No |
| Run: setup service remains active beyond its runtime, stop interval, and calibrated manager allowance | `boundary-violation` | `run-setup` | `manager-delay` | `infra-invalid/run-infrastructure-failure` | No |
| Run: setup service reaches memory or task bound | `resource-limit` | `run-setup` | `{memory,tasks}` | `infra-invalid/run-infrastructure-failure` | No |
| Run: setup rejects the plan or cannot pre-create or exchange an allowlisted object | `operation-failed` | `run-setup` | `{invalid-schema,io,protocol}` | `infra-invalid/run-infrastructure-failure` | No |
| Run: setup completion record crosses its cap | `output-limit` | `run-setup` | `byte-cap(setup-status)` | `infra-invalid/run-infrastructure-failure` | No |
| Run: setup completion publication is missing, malformed, or otherwise fails | `operation-failed` | `run-setup` | `{io,protocol}` | `infra-invalid/run-infrastructure-failure` | No |
| Supported client is absent; one synthetic result per planned identity | `unavailable` | `client-discovery` | `not-installed` | `infra-invalid/client-unavailable` | Yes, for installed clients |
| Cell input or policy publication crosses its cap | `output-limit` | `wrapper-start` | `byte-cap(input\|policy)` | `infra-invalid/cell-infrastructure-failure` | Yes, after proving no service work remains |
| Cell activation fails or exceeds `TimeoutStartSec` before gate release | `operation-failed` | `wrapper-start` | `manager` | `infra-invalid/cell-infrastructure-failure` | Yes, after cleanup |
| Broker-owned model preparation, load-ticket cancellation, or initial idle proof fails | `operation-failed` | `wrapper-start` | `{io,protocol,manager}` | `infra-invalid/cell-infrastructure-failure` | Yes, only after terminal-request, quiescence, and cleanup proof |
| Preparation slot response or projection crosses its cap | `output-limit` | `wrapper-start` | `byte-cap(slots-response\|slot-projection)` | `infra-invalid/cell-infrastructure-failure` | Yes, only after terminal-request, quiescence, and cleanup proof |
| Source request or capture fails | `operation-failed` | `source-fetch` | `{exit,signal,io,protocol}` | `infra-invalid/cell-infrastructure-failure` | Yes |
| Source response crosses its cap | `output-limit` | `source-fetch` | `byte-cap(source-response)` | `infra-invalid/cell-infrastructure-failure` | Yes |
| Source parser rejects the bounded body | `invalid-output` | `source-parse` | `{invalid-json,invalid-schema,empty}` | `infra-invalid/cell-infrastructure-failure` | Yes |
| Snapshot publication crosses its cap | `output-limit` | `source-parse` | `byte-cap(snapshot)` | `infra-invalid/cell-infrastructure-failure` | Yes |
| Prompt or generated client configuration crosses its cap | `output-limit` | `client-config` | `byte-cap(prompt\|client-configuration)` | `infra-invalid/cell-infrastructure-failure` | Yes |
| Client configuration or broker setup otherwise fails | `operation-failed` | `client-config` | `{io,protocol,invalid-schema}` | `infra-invalid/cell-infrastructure-failure` | Yes |
| Capture helper setup or protocol fails | `operation-failed` | `capture-setup` | `{io,protocol}` | `infra-invalid/cell-infrastructure-failure` | Yes |
| Client exec handshake fails | `operation-failed` | `client-launch` | `{exit,signal,protocol,not-installed}` | `infra-invalid/cell-infrastructure-failure` | Yes |
| Client exits nonzero or by signal | `client-exit` | `client-run` | `{exit,signal}` | `infra-invalid/client-exit-cause-ambiguous` | Yes |
| Runtime expires outside an uncompleted confirmed client execution | `timeout` | Stored last complete non-client stage | `deadline` | `infra-invalid/resource-cause-ambiguous` | Yes |
| Runtime expires during an uncompleted confirmed client execution | `timeout` | `client-run` | `deadline` | `infra-invalid/resource-cause-ambiguous` | Yes |
| Cell remains active beyond runtime, stop interval, and calibrated manager allowance | `boundary-violation` | Stored last complete cell stage | `manager-delay` | `infra-invalid/cell-infrastructure-failure` | No |
| Transcript or client stderr crosses its cap | `output-limit` | `client-run` | `byte-cap(transcript\|stderr)` | `infra-invalid/resource-cause-ambiguous` | Yes |
| Broker request headers, request body, or response crosses its cap and complete terminal/quiescence records remain possible | `output-limit` | `client-run` | `byte-cap(broker-request-headers\|broker-request-body\|broker-response)` | `infra-invalid/resource-cause-ambiguous` | Yes, only after terminal-request, quiescence, and cleanup proof |
| Broker status crosses its cap or cannot retain every accepted request's terminal state | `output-limit` | `client-run` | `byte-cap(broker-status)` | `infra-invalid/cell-infrastructure-failure` | No |
| Correlation lookup or task-binding projection crosses its cap | `output-limit` | `client-run` | `byte-cap(stream-lookup\|task-binding)` | `infra-invalid/cell-infrastructure-failure` | No |
| Broker rejects a concurrent excess, disallowed path, invalid cell token, duplicate or invalid body, or model mismatch before forwarding | `operation-failed` | `client-run` | `protocol` | `infra-invalid/cell-infrastructure-failure` | Yes, after proving no rejected request was forwarded |
| A forwarded request cannot bind to the exact encoded alias query, selected model child, and unique task identity | `boundary-violation` | `client-run` | `protocol` | `infra-invalid/cell-infrastructure-failure` | No |
| Memory controller reports cell OOM | `resource-limit` | Stored last complete cell stage | `memory` | `infra-invalid/resource-cause-ambiguous` | Yes, only after surviving-control and server-quiescence proof |
| Control observer sees cell `pids.events.max` increase | `resource-limit` | Stored last complete cell stage | `tasks` | `infra-invalid/resource-cause-ambiguous` | Yes, only after surviving-control and server-quiescence proof |
| Single client tmpfs exhausts blocks or inodes | `resource-limit` | `client-run` | `workspace` | `infra-invalid/resource-cause-ambiguous` | Yes, only after surviving-control and server-quiescence proof |
| Final-response parser rejects a complete bounded transcript | `invalid-output` | `final-response-parse` | `{invalid-json,invalid-schema,empty,protocol}` | `infra-invalid/cell-infrastructure-failure` | Yes |
| Raw assistant text or serialized redacted final response crosses its cap | `output-limit` | `final-response-parse` | `byte-cap(final-assistant\|final-response)` | `infra-invalid/resource-cause-ambiguous` | Yes |
| Nonempty final assistant text is not accepted evidence JSON | `invalid-output` | `evidence-parse` | `{invalid-json,invalid-schema}` | `model-failure/invalid-final-evidence` | Yes |
| Parsed evidence publication crosses its cap | `output-limit` | `evidence-parse` | `byte-cap(evidence)` | `infra-invalid/resource-cause-ambiguous` | Yes |
| Parsed evidence differs from the fresh source | `mismatch` | `evidence-compare` | `difference` | `model-failure/evidence-mismatch` | Yes |
| Differences publication crosses its cap before a complete mismatch record exists | `output-limit` | `evidence-compare` | `byte-cap(differences)` | `infra-invalid/cell-infrastructure-failure` | Yes |
| Stage stderr crosses its cap | `output-limit` | Active result stage from `phase.json` | `byte-cap(stage-stderr)` | `infra-invalid/cell-infrastructure-failure` | Yes |
| Wrapper phase or helper status crosses its cap | `output-limit` | Active result stage from the last valid phase | `byte-cap(phase-status\|helper-status)` | `infra-invalid/cell-infrastructure-failure` | Yes if cleanup succeeds |
| Supervisor resource status crosses its cap | `boundary-violation` | Active result stage from the last valid phase | `byte-cap(resource-status)` | `infra-invalid/cell-infrastructure-failure` | No |
| Causal-cutoff state or record is missing, malformed, owner-dead, contradictory, or oversize | `boundary-violation` | Active result stage from the last valid phase | `{protocol,byte-cap(cutoff-state\|cutoff-record)}` | `infra-invalid/cell-infrastructure-failure` | No |
| A cell or control process becomes dumpable or a matching coredump handler, payload, file, or invalid core-audit record is observed | `boundary-violation` | Active result stage from the last valid phase | `{protocol,byte-cap(core-audit)}` | `infra-invalid/cell-infrastructure-failure` | No; block future attempts |
| Redaction, proof, excerpt, or diagnostic sealing fails | `operation-failed` | `diagnostics` | `{redaction,proof,io,protocol}` | `infra-invalid/cell-infrastructure-failure` | Yes if raw cleanup succeeds |
| Excerpt or proof bundle crosses its serialized cap | `output-limit` | `diagnostics` | `byte-cap(stream-excerpt\|proof-bundle)` | `infra-invalid/cell-infrastructure-failure` | Yes if raw cleanup succeeds |
| Candidate publication crosses its cap | `wrapper-failure` | `diagnostics` | `byte-cap(candidate)` | `infra-invalid/cell-infrastructure-failure` | Yes if cleanup succeeds |
| Candidate shape is invalid | `wrapper-failure` | `diagnostics` | `protocol` | `infra-invalid/cell-infrastructure-failure` | Yes if cleanup succeeds |
| Bubblewrap, broker, observer, control service, cgroup, credential-isolation, or hardening invariant fails after launch | `boundary-violation` | Stored last complete cell stage | `{escape,protocol,manager}` | `infra-invalid/cell-infrastructure-failure` | No |
| A forbidden resource-control action is observed without complete causal attribution to an unsuccessful client exit | `boundary-violation` | `client-run` | `escape` | `infra-invalid/cell-infrastructure-failure` | No |
| Wrapper crashes, disappears, or omits a valid candidate | `wrapper-failure` | Stored last complete cell stage or `wrapper-start` | `{exit,signal,missing,protocol}` | `infra-invalid/cell-infrastructure-failure` | Yes if cleanup succeeds |
| Inactive state or empty cgroup cannot be proved | `cleanup-failure` | `cleanup` | `{manager,io,protocol}` | `infra-invalid/cell-infrastructure-failure` | No |
| Surviving control broker cannot finish or cancel every request or seal quiescence | `cleanup-failure` | `cleanup` | `{manager,io,protocol,deadline}` | `infra-invalid/cell-infrastructure-failure` | No |
| Raw slot response, projected slot state, or quiescence record crosses its cap | `cleanup-failure` | `cleanup` | `byte-cap(slots-response\|slot-projection\|server-quiescence)` | `infra-invalid/cell-infrastructure-failure` | No |
| Cell cleanup service reaches memory or task limit | `cleanup-failure` | `cleanup` | `{memory,tasks}` | `infra-invalid/cell-infrastructure-failure` | No |
| Authoritative cell result crosses its cap | `finalization-failure` | `finalization` | `byte-cap(cell-result)` | `infra-invalid/cell-infrastructure-failure` | No |
| Authoritative cell finalization exceeds its operational target | `finalization-failure` | `finalization` | `deadline` | `infra-invalid/cell-infrastructure-failure` | No |
| Authoritative cell result otherwise cannot be sealed | `finalization-failure` | `finalization` | `{io,protocol}` | `infra-invalid/cell-infrastructure-failure` | No |
| Evidence matches and every required record, cleanup fact, and server-boundary fact is valid | `pass` | `evidence-compare` | `null` | `pass/evidence-match` | Yes |
| Run cleanup service activation or non-raw exact-allowlist cleanup fails | `cleanup-failure` | `cleanup` | `{manager,io,protocol}` | `infra-invalid/run-infrastructure-failure` | No |
| Run cleanup service reaches runtime, memory, or task limit | `cleanup-failure` | `cleanup` | `{deadline,memory,tasks}` | `infra-invalid/run-infrastructure-failure` | No |
| Run cleanup service remains active beyond its runtime, stop interval, and calibrated manager allowance | `cleanup-failure` | `cleanup` | `manager-delay` | `infra-invalid/run-infrastructure-failure` | No |
| Raw exact-name deletion fails and the run is moved to quarantine | `cleanup-failure` | `cleanup` | `{io,protocol,deadline}` | `infra-invalid/run-infrastructure-failure` | No; block all future attempts |
| Quarantine recovery record crosses its derived cap | `finalization-failure` | `finalization` | `byte-cap(quarantine-record)` | `infra-invalid/run-infrastructure-failure` | No; block all future attempts |
| Human cell block, aggregate output, `matrix.log`, manifest, ledger, legacy ledger, run result, or failure marker crosses its cap | `finalization-failure` | `finalization` | `byte-cap(human-cell-block\|human-output\|matrix-log\|manifest\|ledger\|legacy-ledger\|run-result\|failure-marker)` | `infra-invalid/run-infrastructure-failure` | No |
| Run finalization exceeds its operational target | `finalization-failure` | `finalization` | `deadline` | `infra-invalid/run-infrastructure-failure` | No |
| Run result, manifest, `matrix.log`, or ledger append otherwise cannot be sealed | `finalization-failure` | `finalization` | `{io,protocol}` | `infra-invalid/run-infrastructure-failure` | No |
| Successful manifest aggregation | `pass` | `finalization` | `null` | One of `pass/required-cells-pass`, `model-failure/required-cell-model-failure`, or `infra-invalid/required-cell-infra-invalid` | End |

Human output distinguishes `agent-timeout` from `cell-timeout` without adding
another result enum. A `timeout` at `client-run` with confirmed exec and no
completion record prints `agent-timeout`; every other timeout prints
`cell-timeout`.

### Precedence

After the cell is inactive and the control service has sealed its records, the
finalizer selects the first proved condition in this order:

1. `cleanup-failure`
2. `boundary-violation`
3. sealed memory `resource-limit`
4. sealed task `resource-limit`
5. sealed workspace `resource-limit`
6. broker `output-limit`
7. transcript `output-limit`
8. stderr `output-limit`
9. another sealed cell-artifact `output-limit`
10. `timeout`
11. capture protocol failure
12. `wrapper-failure`
13. valid wrapper candidate

The first shared cutoff event separates eligible bytes from later bytes; a
candidate completed after a timeout cutoff cannot win. An atomic overflow or
supervisor-owned resource record is valid only when it agrees with that shared
cutoff, then fixed mechanical precedence selects among concurrent events. The
resource observer's final event-counter
read must agree with its sealed record; an unexplained increase is a boundary
violation. Simultaneous valid memory, task, and workspace markers are not
contradictory: the finalizer validates and retains all three marker hashes, then
uses the fixed order above for the single authoritative detail. A marker without
its required controller counter or workspace fact, or a counter increase without
its marker, is `boundary-violation/protocol`. These rules remove the need for a
lifecycle arbiter.

A sealed cell resource marker is eligible for positions 3 through 5 only when
the separately bounded control service survived the event, every accepted
request is terminal, and server quiescence validates. Broker loss, control loss,
or unproved quiescence selects position 1 or 2 instead, makes the attempt
infrastructure-invalid, and aborts the matrix. No precedence path signals the
inference server.

If the final result itself cannot be written, the supervisor exits nonzero,
leaves the closed atomic `run-failure-marker`, and aborts. The sampler
classifier treats an absent required result or manifest as
infrastructure-invalid.

## Diagnostics Supersession

For `scripts/check-with-agents.sh` only, this design supersedes these
requirements from the Transparent Check Diagnostics Design:

- the prohibition on any temporary raw file;
- complete stdout and stderr display for agent streams;
- a full JSONL transcript for every agent row;
- default printing of the same complete data later retained on request;
- tests and acceptance criteria that require complete agent transcripts.

Other checks keep the earlier complete-diagnostic contract.

The cell service may create mode `0600` raw prefixes under the fixed private
exchange while it runs. It never intentionally displays them or copies them
into the retained-artifact allowlist. Capture helpers feed accepted bytes to
bounded online redactors and the behavior evaluator without placing a complete
stream in a shell variable or argv.

Normal output keeps the command, exact prompt, final response, parsed evidence,
source facts, comparison, and verdict. Failure output adds:

- outcome, stage, detail, and client exit status;
- accepted raw byte counts and overflow flags;
- complete post-redaction byte counts and SHA-256 values;
- the same intentionally incomplete excerpt record defined by the byte-cap
  contract, capped at 262,144 serialized bytes per client stream;
- attribution basis, model-behavior predicate, proof kind, bundle hash, and
  qualification reason;
- causal-cutoff and core-audit hashes, without journal or core payload; and
- bounded nonempty parser stderr and final response when available.

The output and retained artifact use the same excerpt bytes; neither contains a
complete nonempty stream. A redaction digest or excerpt is valid only when its
helper atomically proves it processed every accepted raw byte. Partial excerpt
files are deleted. The streaming redactor must handle secrets that cross input
chunk boundaries. Redaction failure makes the diagnostic result
infrastructure-invalid; the harness never falls back to retaining or printing
raw or complete redacted bytes.

When `LLM_ENV_KEEP_CHECK_ARTIFACTS=1`, the run finalizer retains only the fixed
allowlist of capped redacted excerpts and final responses, cell results,
structured proof bundles, and the run manifest. Directories remain mode
`0700`; files remain mode `0600`.

A quarantined run is failure containment, not a retained diagnostic artifact.
It is never displayed or consumed for classification. Its private recovery
record identifies the exact possible raw names and caps. While that record or
quarantine directory exists, all output and manifests state `raw_absence=false`,
and no code or acceptance check claims that raw streams are absent.

## Qualification Classification

### Relationship to Sampler Failure Handling

This design narrows the model sampler design's network attribution for every
cell and narrows convergence attribution for a cell whose evidence is truncated
or whose terminal outcome is `timeout`, `output-limit`, or `resource-limit`.
The harness has no trusted egress telemetry: adapter records prove generated
literal actions and their client states, not network syscalls made by wrappers
or indirect executables. Resource outcomes additionally prove non-completion or
resource use, but not whether the model, client, transport, server, or harness
caused the event.

A resource-bound required cell is `model-failure` only when complete adapter
records whose byte ranges end at or before the causal cutoff satisfy the
narrower direct predicate below. The classified failure is that proved
generated action, not an inference that the action caused the later resource
event. Without proof, apparent non-convergence, repetitive text, a missing final
response, timeout, output growth, cgroup OOM, task exhaustion, and workspace
exhaustion are `infra-invalid` with reason `resource-cause-ambiguous`.

This explicit refinement supersedes the broader resource-bound interpretation
of `failure to converge` and the prior attribution of indirect network syntax.
A completed cell with complete client records is still a model failure for a
proved literal direct network-command violation, invalid final JSON, altered
source values, non-convergence, wrong source URL or timestamp, or an
unsuccessful exit attributed to generated actions. An unrelated local Bash
action and ambiguous indirect network syntax are not themselves
`altered-direct-network-command`.

### Complete Attribution and Bounded Proof

The behavior evaluator reads client records online before raw files are removed
and selects exactly one basis.

`complete-attribution` is legal only when client exec is confirmed, both client
streams and every required adapter record are complete and non-overflowing, the
client has reached a non-resource terminal state, and no capture, parser,
transport, broker, server-boundary, cutoff, or cleanup failure is proved. Its
`client-terminal` cutoff must equal both streams' final committed byte counts.
Under this basis the evaluator applies these observable behavior rules:

- `repeated-direct-network-command`: a second distinct assistant tool-call
  identity has a complete execution-start record whose entire literal command
  is exactly the permitted `curl` command. This proves a repeated generated
  direct action, not successful network I/O. A second local Bash action does not
  satisfy it;
- `altered-direct-network-command`: one complete execution-start record is a
  single simple shell command with literal `curl` or `wget` as `argv[0]`, no
  assignment, expansion, substitution, redirection, control operator, pipeline,
  function, or alias syntax, and command bytes other than the exact permitted
  command. This directly proves an altered generated network-command action.
  Any shell form outside that grammar is unproved on network grounds;
- `forbidden-resource-control`: a complete assistant action attempts a blocked
  user-manager or cgroup-control path, its matching terminal record proves that
  attempt failed, and complete client records attribute the client's
  unsuccessful exit to that failure with no independent infrastructure cause.
  Without this complete causal exit chain, the attempt is an
  infrastructure-invalid boundary event rather than model failure;
- `generated-action-exit`: a complete assistant-generated action joins to its
  terminal execution error and the client's unsuccessful exit, with no
  independently proved infrastructure cause. Wrapper, dynamic, and indirect
  commands remain attributable because the complete generated action and its
  terminal error are present; and
- `failure-to-converge`: after model invocation, either the client emits its
  pinned terminal turn/step-limit event, or the complete event sequence contains
  a second assistant action or continuation after the first source action and
  then terminates without nonempty final assistant evidence. A client crash,
  transport loss, or single incomplete action without this sequence is not
  enough.

The complete evaluator runs before a nominal pass is accepted. Consequently, a
cell that eventually returns matching evidence after a proved repeated or
altered literal direct network action cannot become `pass`. An otherwise valid
completion containing only wrapper, substitution, variable, alias, pipeline,
or indirect-executable syntax is not classified as a network violation because
the adapter cannot prove its egress behavior. Exit, convergence, and final
evidence rules still apply. Unrelated successful local Bash activity does not
override matching evidence.

`bounded-resource-proof` applies when the terminal outcome is resource-bound or
any relevant stream is truncated. It reads only complete client records wholly
within the validated causal cutoff and may seal only these narrower direct
predicates:

- `repeated-direct-network-command`: a second distinct assistant tool-call
  identity executes the exact permitted `curl`;
- `altered-direct-network-command`: an executed literal simple command satisfies
  the same direct `curl` or `wget` grammar above and differs from the exact
  permitted command;
- `generated-action-exit`: a complete assistant-generated Bash call joins to a
  terminal execution error and then to the client's nonzero exit, and the
  execution record attributes the error to the generated command rather than
  DNS, source availability, credentials, local API, transport, capture,
  timeout, or a harness failure.

For Pi `0.82.1`, either basis joins a complete assistant `toolCall.id` to a
complete `tool_execution_start.toolCallId` and, where a terminal action is
required, the matching `tool_execution_end.toolCallId`. For OpenCode `1.18.10`,
it requires a complete Bash `tool` part with literal command input and the
required terminal `completed` or `error` state. Exit attribution also requires
the sealed client exit record and absence of an independently proved
infrastructure event. An errored exact permitted command with only an external
network or source error is insufficient. Client-version or event-shape drift
makes either evaluated basis invalid and the attempt infrastructure-invalid.

For either proof basis, command substitutions, wrappers, dynamic
variables, indirect executables, incomplete joins, reused contradictory IDs,
aliases, functions, redirections, pipelines, malformed records, and partial JSON
cannot set a network predicate. Text, reasoning, byte count, a tool-start record
without its required join, and the resource event itself cannot prove model
behavior. Complete generated commands outside the direct grammar remain usable
for non-network exit and convergence rules. Either basis sets
`model_behavior_attributed=true` only after atomically sealing a bounded
structured bundle that includes the cutoff hash and every contributing byte
range.

`forbidden-resource-control` is never legal under `bounded-resource-proof`.
Seeing a direct resource-control attempt in truncated or resource-bound evidence
seals the boundary event, not model attribution. Only the complete causal
unsuccessful-exit chain defined above can use that proof kind.

### Cell Classification

Qualification applies this fixed order after mechanical outcome selection:

1. A cleanup, boundary, finalization, or run-level infrastructure failure is
   always `infra-invalid`; behavior evidence cannot certify an unsafe attempt.
2. Complete `failure-to-converge` attribution maps the cell to
   `model-failure/failure-to-converge`.
3. Complete `generated-action-exit` or `forbidden-resource-control` attribution
   maps the cell to `model-failure/generated-action-exit`.
4. `repeated-direct-network-command` or `altered-direct-network-command` under
   either legal basis maps the cell to
   `model-failure/generated-action-violation`.
5. Without attributed behavior, `mismatch` maps to
   `model-failure/evidence-mismatch`.
6. Without attributed behavior, `invalid-output/evidence-parse` after
   extraction of nonempty assistant text maps to
   `model-failure/invalid-final-evidence`.
7. Without attributed behavior, nominal `pass` maps to `pass/evidence-match`.
8. Every unproved resource outcome maps to
   `infra-invalid/resource-cause-ambiguous`; unproved `client-exit` maps to
   `infra-invalid/client-exit-cause-ambiguous`; every other outcome uses its
   closed infrastructure reason from the result table.

Attribution therefore has precedence over nominal `pass` and over an otherwise
ambiguous non-resource client exit. Missing, malformed, contradictory,
version-drifted, or otherwise unverifiable records never upgrade an ambiguous
result. Truncation selects the narrower bounded proof basis rather than erasing
a direct violation already proved by complete accepted records.

## Manifest and Ledger

### Reproducible Hash Domains

The repository contains a closed `scripts/agent-harness-files.json` source
manifest. It is one RFC 8785 object with exactly `schema=1` and `files`; each
file entry has exactly `path` and `role`. Paths are unique normalized printable
ASCII repository-relative paths, contain no empty, `.` or `..` component, and
are strictly ordered by unsigned path byte. The manifest lists itself,
`scripts/check-with-agents.sh`, every sourced shell file and imported Python
module, every record schema and client adapter, the no-core launcher and its
seccomp program, all service-property templates, the deterministic renderer,
and the complete private candidate-runner and classifier definitions. The
closed role enum is `entrypoint`, `helper`, `schema`, `adapter`, `no-core`,
`seccomp`, `service-template`, `renderer`, `private-runner`, and
`private-classifier`. A loaded harness file absent from this manifest, an entry
not loaded by the closed component graph, a symlink, or a non-regular file is a
protocol failure.

`source_manifest_sha256` is SHA-256 over the source manifest's exact canonical
file bytes, with no added or removed trailing byte. It identifies the manifest
record; `harness_sha256` below separately binds every listed file's content.

`harness_sha256` is SHA-256 over this exact binary framing:

```text
ASCII("llm-env-agent-harness-v1") || 0x00 ||
u32be(file_count) ||
for each manifest entry in manifest order:
    u32be(path_utf8_length) || path_utf8 ||
    u64be(content_length) || exact_file_bytes
```

Lengths are unsigned and checked before concatenation. File content includes
every leading and trailing byte; the hasher adds no newline, separator, Unicode
normalization, or text conversion beyond the displayed framing. The source
manifest's own exact bytes are one listed content value, so its roles and order
are bound without a hash cycle. Golden fixtures cover an empty final line, one
and two trailing LFs, embedded NUL, reordered paths, and length boundaries.

Private runtime scripts are exact copies of their listed definitions. Generated
service instances and policy files are rendered only by the listed renderer
from a listed template and closed canonical scalar inputs. The renderer emits
the template's specified final byte exactly; it does not inherit platform line
endings. The plan stores each generated logical name, template path, canonical
input hash, rendered length, and `rendered_sha256`; preflight and the supervisor
rehash the actual private file or effective-property projection before every
launch. Thus run-specific names do not change `harness_sha256`, but no private
service definition can drift outside the hash-bound template and instance
hashes.

The first-seven ledger projection has a separate exact domain. A
pair-preserving decoder requires a top-level array, rejects duplicate members at
every depth, and selects the seven whole objects at array indexes 0 through 6
without deleting fields or changing array order. It RFC 8785-canonicalizes that
seven-element array, appends exactly one LF byte, and hashes the result. The
canonical JSON contains no other trailing byte. Applied to the immutable
legacy objects, this recipe yields
`7720f63175866e1884421e3bd97e26c1c0021e7b3550e058c06f333521eac88b`.
The separate legacy snapshot hash remains over the exact original
`ledger.json` file bytes, including indentation and its final LF, and yields
`5069e1204609bbd982fde920f479736116e5ed01cc133935aa6a7e73baa53160`.

The atomic run manifest binds:

- attempt ID, profile, run ID, candidate fingerprint, harness SHA-256,
  source-manifest SHA-256, calibration SHA-256, and client versions;
- plan SHA-256 and the credential-free server-identity SHA-256;
- its canonical body SHA-256 and the authoritative run-result path and
  SHA-256;
- every planned alias ordinal and cell identity, including its globally unique
  cell ordinal, exact cell and control unit names, exchange path, artifact path,
  and result path;
- every result, causal-cutoff, core-audit, and server-quiescence record hash;
- run-level discovery, setup, cleanup, and finalization status; and
- `raw_absence=true` only after exact-name deletion has been verified, every
  core audit proves no coredump payload, and no recovery record or quarantine
  directory exists.

The sampler classifier validates safe ownership and modes, manifest hash,
schema, harness framing, rendered-instance and plan hashes, exact result and
cutoff hashes, inverse ordinal mapping, pairwise uniqueness of all planned
names, identity agreement, and this required set:

```text
(pi, gemma4, weather)
(pi, gemma4, fx)
(opencode, gemma4, weather)
(opencode, gemma4, fx)
```

It aggregates required identities in this order:

1. A run-level infrastructure failure, malformed or incomplete manifest,
   missing or duplicate required identity, or any required `infra-invalid`
   result makes the attempt `infra-invalid`.
2. Otherwise, any required `model-failure` makes the attempt `model-failure`.
3. Otherwise, all four required results must be `pass` and the attempt is
   `pass`.

All additional aliases have all four planned results in the manifest and are
tested. They do not change sampler qualification, but retain their separate
effect on final all-alias acceptance.

The ledger entry stores the manifest hash and a canonical projection of the
four required identities, both ordinals, result hashes, outcomes,
classifications, and reasons. `matrix.log` remains the bounded machine summary
defined above, not the source of ledger classification. Human output uses its
run-derived maximum. A conflicting operator classification is rejected.

The seven existing ledger objects remain present, unchanged, and in their
current order. Before creating any private script, recovery verifies the exact
current `ledger.json` SHA-256
`5069e1204609bbd982fde920f479736116e5ed01cc133935aa6a7e73baa53160`, copies
those exact bytes through the pre-created bounded mode `0600` atomic pair for
`legacy-ledger.json`, calls `fsync`, exchanges it, and verifies the same hash
again. The immutable snapshot makes the original byte representation
independently verifiable after later atomic ledger appends.

New attempts append a new entry schema to `ledger.json`; migration never
deletes or reorders its first seven entries. Before and after every append and
before and after final cleanup, the classifier verifies the snapshot's exact
hash, the active ledger's first-seven projection bytes under the exact recipe
above and SHA-256
`7720f63175866e1884421e3bd97e26c1c0021e7b3550e058c06f333521eac88b`, and the
presence and order of all seven legacy objects. A mismatch aborts before any
destructive cleanup.

## Bounded Cleanup

The exchange contains only pre-created fixed names and capped regular files.
Raw streams are never intentionally displayed or retained. Before the first
cell gate, the supervisor arms a pre-created mode `0600` recovery record outside
the run directory. It contains the run ID, exact normal and quarantine paths,
and every allowlisted raw source, transcript, stderr, parser, credential, and
temporary basename with its cap; it contains no raw content. The finalizer
deletes those files by exact name after it seals the result and never traverses
client-controlled paths.

At run completion, the supervisor starts a transient cleanup service with a
10-second runtime target. When artifacts are not retained, it removes the
fixed run allowlist and then the empty private directories. When artifacts are
retained, it verifies the retained allowlist, modes, ownership, caps, and
hashes, then removes only raw and temporary names.

After each bounded deletion, the supervisor verifies every recorded raw name
absent using fixed parent descriptors. Only after all such names are absent,
both cell and control cgroups are empty, and parent directories are `fsync`ed
may it clear the armed recovery record and publish `raw_absence=true`. An
interruption before that point leaves the record armed and blocks startup.

If exact-name deletion times out or fails, the cleanup service first becomes
inactive. The supervisor then atomically renames the fixed mode `0700` private
run directory to its precomputed same-filesystem quarantine path, `fsync`s both
parents, and seals the mode `0600` recovery record with state `quarantined` and
the exact allowlisted raw names and caps. The quarantined directory is bounded
by the already reserved `run_storage_max`, is not a retained artifact, and is
never displayed or used as behavior evidence. Failure to complete the rename or
record update leaves the pre-armed record pointing to both allowed locations and
still blocks startup. The attempt is infrastructure-invalid, and the current
matrix and all future candidate or qualification attempts abort until recovery
clears the latch.

Run-directory recovery runs only a separately bounded exact-name cleanup against
the armed record. It verifies owner, mode, fixed directory identity, every known file's
type and cap, deletes each listed raw name, and proves every listed name absent.
Unknown names or identity drift fail closed. It may remove remaining allowlisted
redacted files and empty directories only by exact name. The quarantine is
cleared only after verified raw-name absence, directory removal, parent `fsync`,
and atomic clearing of the recovery record. Until then, no output, manifest,
test, or acceptance check may claim raw-file absence.

A detected coredump uses a separate external-core latch containing only the
affected PID, unit, invocation ID, cgroup, core-audit hash, and the Boolean facts
that a handler, payload, or file was observed. It contains no coredump metadata
field or payload value. Exact-name run cleanup cannot clear this latch. Operator
recovery must remove any external core file and make the matching coredump
journal payload unavailable under site policy; bounded preflight then proves no
matching `coredumpctl` or journal record remains before atomically clearing the
latch. Until then, `raw_absence=false` and all attempts remain blocked.

Run-directory cleanup and sampler-experiment cleanup are separate. Successful
final experiment cleanup removes obsolete scripts, raw evidence, temporary
rollback material, and the old model only by exact reviewed names. It preserves
the mode `0700` `sampler-experiment` directory and this mode `0600` state
allowlist: `ledger.json`, `legacy-ledger.json`, `selected-profile`, final run
manifests, and their hash records. It then repeats both legacy-ledger
verifications. It never executes the old plan's `rm -rf "$state_dir"` path.

No unbounded `EXIT` trap calls `rm -rf`. An unexpected path is never traversed
or deleted; it keeps the recovery latch armed and requires operator review. A
failed cleanup never starts another cell or future attempt until verified
recovery clears the latch.

## Recovery Flow

The deleted private experiment tools must be recreated from reviewed source,
not memory or the task report. The baseline is:

- commit `3dbc3069484518efe4e4d2ad03535edb181c9afc`;
- `docs/superpowers/plans/2026-08-01-model-sampler-configuration.md`;
- blob `99c3273b062dfe509030c1693a930a7319de1c15`;
- candidate runner source at Task 4 Step 2;
- classifier source at Task 4 Step 3.

Recovery proceeds in this order:

1. Implement and review the bounded harness and its tracked helpers.
2. Run `make validate` and `make test`.
3. Write a follow-up reviewed plan that contains complete revised definitions
   for the private candidate runner, classifier, preflight, projection proxy,
   and cell control service with its request broker and observer.
4. Verify the existing ledger has seven unique entries, mode `0600`, and the
   expected pre-recovery hash; atomically create and verify the exact
   `legacy-ledger.json` snapshot before creating any private script.
5. Recreate private scripts through pre-created mode `0600` atomic pairs and
   file exchange. Do not rerun ledger initialization from the old plan.
6. Calibrate memory, tasks, workspace, manager latency, and llama.cpp
   cancellation latency against the restored base model, then bind the
   calibration to the new harness, clients, parser runtime, and server build.
7. Rerun the complete client request-projection, surviving-broker cancellation,
   server-quiescence, and sandbox preflight.
8. Atomically pin the new harness hash only after preflight passes.
9. Create a new publisher `screening-1-bounded` attempt with the unchanged
   publisher profile and a new attempt ID. The legacy publisher rejection has
   no decision effect under the new harness.
10. After publisher has one non-infrastructure bounded screening result, create
    a new greedy `screening-1-bounded` attempt with the unchanged greedy profile
    and a new attempt ID, even if publisher passed.
11. Qualify screening candidates in the existing fixed order. The first
    candidate that passed bounded screening receives three consecutive valid
    bounded qualification rounds; a valid model failure moves to the next
    screening candidate, while an infrastructure-invalid round is repaired and
    rerun without advancing or resetting the streak.
12. If neither candidate qualifies, restore the base model and stop before
    publishing defaults or deleting rollback assets.
13. If a candidate qualifies, run the existing nondestructive publish and
    verification gates, then use a revised exact-name final cleanup that
    supersedes Task 6 Step 6 of the old plan. Preserve `ledger.json`,
    `legacy-ledger.json`, and all seven legacy objects, and verify both legacy
    hashes after cleanup.

The old publisher rejection and two old greedy attempts remain immutable legacy
entries. None can satisfy bounded screening because they lack the new runtime,
stream, memory, task, workspace, sandbox, broker, server-quiescence, adapter,
canonical-schema, and calibration records. Only new bounded manifests may
advance screening or qualification.

## Testing

Mocked shell tests will prove:

- unchanged Pi and OpenCode pass behavior, prompts, source checks, final
  parsing, and comparisons;
- bounded model discovery rejects duplicate JSON members at every nesting
  depth before selection and covers every run-level outcome;
- aliases use RFC 8785 canonical JSON and opaque paths; reject controls,
  White_Space, preset delimiters, reserved section names, and canonicalizing
  colon forms; preserve permitted Unicode and slash-containing values such as
  `nested/model`; and produce identical bytes across supported parser runtimes;
- configuration, preset rendering, setup, server checks, and discovery use the
  same alias validator; over-4,096-byte, control, whitespace, noncharacter, and
  preset-unsafe aliases fail before server startup, while every accepted
  configured alias appears byte-for-byte in the API set and receives four cells;
- canonical fixtures accept integers `-9007199254740991` and
  `9007199254740991`, reject both adjacent out-of-range integers, reject lone
  surrogates and every Unicode noncharacter, and cover each plane's FFFE/FFFF
  boundary;
- alias ordinals remain distinct from globally unique cell ordinals, the
  inverse ordinal formula holds, and all four required `gemma4` identities have
  distinct cell unit, control unit, exchange, artifact, and result names;
- a six-alias and a response-cap-derived many-alias fixture derive all
  aggregate maxima, create every planned identity, and test every alias without
  a fixed five-alias ceiling;
- one cell service contains source, parser, client, capture, validator, and
  diagnostic descendants, while one separately bounded sibling control service
  contains only the broker and resource observer;
- the wrapper gate prevents cell work until unit identity, cgroup, effective
  properties, helpers, input, calibration, broker state, and initial server-slot
  idleness are verified;
- `ExitType=main` plus `KillMode=control-group` cleans ordinary, background,
  separate-process-group, and `setsid` descendants after normal exit, wrapper
  crash, timeout, overflow, and resource failure;
- outer and per-role no-core launchers set both core limits and dumpability
  before canary secrets are read, descendants cannot restore dumpability, and
  forced `SIGSEGV` produces no systemd-coredump handler, `COREDUMP*` journal
  payload, external file, or allowlisted `core*` file;
- transcript and stderr prefixes never exceed 33,554,432 bytes and store no
  overflow byte;
- every other artifact respects its named raw or post-transform cap; captured
  streams never enter a shell variable or argument, while only the validated
  alias and prompt enter required client arguments;
- every activation failure, discovery, setup, or cleanup timer/resource event,
  setup publication failure, credential/configuration limit, and named artifact
  overflow maps to exactly one legal scope, outcome, stage, detail,
  qualification, and continuation rule;
- empty, one-byte, short, long, and expanding-redaction streams produce the
  documented post-redaction counts and capped excerpt, and every nonempty case
  omits at least one stream byte from output and retention;
- the fixed setup allowlist pre-creates every directory, current file, and
  atomic sibling before model work, and `RENAME_EXCHANGE` updates allocate no
  later inode;
- the sole-writer storage plan has its closed schema, derived cap, body and file
  hashes, deterministic term order, atomic publication, and listed consumers;
  missing terms, unknown terms, bad checked products, cap overflow, and setup
  disagreement fail before model work;
- checked run-wide data and metadata arithmetic includes every retained cell,
  aggregate, atomic sibling, copy-on-write extent update, cleanup operation,
  quarantine record, and recovery operation;
- Btrfs fixtures with zero `statvfs` inode counters parse raw data, metadata,
  profile-ratio, minimum-free, and unallocated-device fields without double
  counting shared unallocated space; tested non-Btrfs fixtures enforce block and
  pre-creation inode reserves;
- `matrix.log` has one sole writer and only canonical summary lines; its
  four-cell golden output is at most 6,144 bytes, while four worst-case human
  cell blocks plus framing are at most 12,910,592 bytes; larger matrices use the
  exact checked run-derived caps;
- a 128-byte maximum legal ASCII `attempt_id` produces the 435-byte canonical
  matrix header, while controls, non-ASCII, and a 129th byte are rejected;
- current host and ancestor-cgroup memory gates reject insufficient headroom
  before discovery and every cell gate;
- source delay consumes the same 300-second runtime target as client execution;
- timeout before client exec, during client execution, and after recorded
  client completion produces the specified stage and human timeout label;
- simultaneous timeout, output, capture, wrapper, and resource fixtures always
  select the fixed precedence;
- every interleaving of transcript/stderr block commit with timeout, stream
  overflow, broker overflow, workspace, memory, task, client-terminal, helper
  death, and robust-mutex owner death either commits the whole block before one
  cutoff or excludes it; only complete adapter ranges within the sealed cutoff
  can enter a proof bundle;
- the normal client-terminal fixture reaps the client, drains both capture
  helpers through EOF, commits their final blocks, and only then closes the
  cutoff, while every safety-event fixture closes it without waiting to drain;
- cell memory OOM and task exhaustion kill the cell service, remain distinct,
  and leave the sibling broker and observer alive through request quiescence;
- simultaneous memory, task, and workspace records validate all counters and
  select memory, then tasks, then workspace in the documented order;
- a pre-existing observer detects an increment of `pids.events.max`, seals
  `resource-limit/tasks`, validates its already-open cgroup descriptor, and
  writes only that cgroup's `cgroup.kill`; observer loss and descriptor mismatch
  fail closed without a unit-name race;
- bootstrap discovery, cell, and control probes have effective memory, task,
  swap, and workspace limits before any calibrated value exists;
- the conservative calibration formula adds every declared allocation and task
  capacity at full size, double-counts measured allocations, has no
  peak-occupancy inference, and reproduces identical limits from its record;
- cell and control limits and current host-headroom gates include both sibling
  services, while server-cgroup request growth is measured and reported but not
  represented as a harness-enforced llama.cpp limit;
- Bubblewrap hides the exchange, host PID namespace, writable cgroup paths, and
  known user-manager transports while supported client networking still works;
- direct migration, `systemctl --user`, `systemd-run --user`, and known socket
  escape fixtures fail;
- `--disable-userns`, zero effective capabilities, `unshare`, `clone`, and
  `clone3` fixtures prevent nested user/mount namespaces and a second tmpfs,
  while live mount-table and over-cap fixtures reject any other writable mount
  and the pinned Pi and OpenCode process/thread paths still work;
- the sandbox receives only a cell token, direct llama.cpp inference with that
  token fails, and every supported-client inference connection belongs to the
  bounded broker;
- duplicate-free request-body fixtures require exactly one root model equal to
  the cell alias before any forward; wrong, missing, nested-only, duplicate, and
  non-string models fail closed, including aliases containing `&`, `#`, `%`,
  `/`, and non-ASCII text;
- exact query fixtures leave only RFC 3986 unreserved bytes literal, use
  uppercase percent escapes and no `+`, and bind every forwarded request to the
  credential-free selected-child identity and one newly observed `id_task` by
  bracketing the first stream chunk with the same active task and a fresh
  conversation-session byte transition; unrelated-task, reused-ID, fast-finish,
  and router-correlation-cleanup races fail closed;
- normal completion and surviving-broker cancellation after cell `SIGKILL`,
  OOM, and task exhaustion produce terminal request records plus two consecutive
  idle slot samples before the mechanical cell outcome is accepted;
- broker loss, control-service loss, a missing terminal record, or unproved
  active/queued quiescence makes the attempt infrastructure-invalid and aborts
  the matrix until the cause is repaired and preflight is repeated;
- systemd-call spies and invocation-restart fixtures prove that no path calls
  `StopUnit`, `KillUnit`, or an equivalent signal operation for
  `llm-server.service`;
- the broker-owned preparation path loads an initially unloaded selected model
  before client release, while preparation timeout cancels its load ticket and
  drains the server without starting source or client work;
- all client-writable paths share one `--size WorkspaceMax` tmpfs, combined
  writes cannot exceed it, and workspace exhaustion remains distinct;
- manager stop-mode variants select `SIGKILL` as the first post-grace signal;
- five manager and five server-cancellation calibration fixtures produce the
  documented latency allowances and reject either allowance over 10 seconds;
- calibrated memory, task, and workspace values bind to the harness, clients,
  systemd, Bubblewrap, parser runtime, llama.cpp image/build/revision, and base
  model;
- the source-manifest framing changes for any helper, schema, adapter, no-core
  policy, private definition, service template, path order, or trailing byte;
  rendered instance hashes reject template/input drift, and the first-seven
  canonical-array-plus-LF and exact legacy-file recipes reproduce both pinned
  ledger hashes;
- systemd, Podman, and `/proc` call spies prove the server-identity reader uses
  only allowlisted credential-free projections and never requests service argv,
  environment, full inspection, unit text, `cmdline`, or `environ`;
- all legal result combinations validate and every illegal, unknown,
  noncanonical, contradictory, missing, duplicate, or oversize record fails
  closed;
- every pretty result example validates and canonicalizes to golden bytes; the
  timeout detail is exactly `{"kind":"deadline"}` and rejects `stream`;
- cell, run, and run-failure-marker variants reject each other's fields and
  cover failures before identities exist;
- source, launch, client exit, capture, parser, evidence, timeout, output,
  resource, boundary, wrapper, cleanup, and finalization outcomes remain
  distinct;
- no complete transcript appears in normal output, failure output, or
  `matrix.log`;
- displayed and retained diagnostics are private, redacted, capped, hashed,
  and limited to the fixed allowlist;
- exact-name deletion failure atomically moves the bounded mode `0700` run to
  quarantine, seals the mode `0600` exact-name/cap recovery record, reports
  `raw_absence=false`, and blocks all future attempts until bounded recovery
  verifies every raw name absent before clearing the latch;
- secrets split across redactor chunks remain redacted;
- Pi and OpenCode adapters separate complete attribution from bounded-resource
  proof and reject malformed, partial, reasoning-only, text-only, ambiguous, or
  contradictory records;
- both proof bases classify a repeated exact permitted command or one altered
  literal simple `curl`/`wget` command as a generated-action violation; wrapper,
  substitution, variable, alias, function, redirection, pipeline, and indirect
  executable forms cannot establish a network predicate without egress
  telemetry;
- complete non-resource records still classify generated-action exits and the
  closed non-convergence sequence, including when the generated command is
  dynamic or indirect, because those predicates join adapter-visible actions
  and terminal states rather than infer network behavior;
- unrelated local Bash actions and ambiguous indirect network syntax are not
  `altered-direct-network-command`;
- a forbidden resource-control attempt becomes model failure only with complete
  causal records joining its terminal error to an unsuccessful client exit;
  every incomplete, recovered, or noncausal case is an infrastructure boundary
  event, and bounded-resource proof cannot attribute it;
- direct literal action attribution overrides nominal evidence `pass`; the
  shared causal cutoff narrows truncated or resource-bound evidence, while
  indirect network syntax and external network or source errors remain
  unattributed on network grounds;
- required result aggregation gives infrastructure invalidity precedence over
  model failure, then model failure over pass;
- cleanup must be proved before a later cell, and cleanup or finalization
  failure aborts the matrix and arms or preserves the quarantine latch when raw
  absence is unproved;
- successful final cleanup preserves `ledger.json`, the exact
  `legacy-ledger.json`, all seven legacy objects, and both legacy hashes;
- legacy publisher and greedy records cannot satisfy a bounded manifest, and a
  new bounded publisher screening precedes the new bounded greedy screening;
- API keys remain absent from stdout, stderr, results, manifests, and retained
  artifacts.

Existing tests that require a complete agent transcript on capture, parser, or
comparison failure will be replaced with assertions for structured stream
facts, bounded redacted diagnostics, and transcript absence after successful
cleanup. The `tee` failure fixture will become a bounded-capture-helper failure
fixture. Tests for other check scripts keep their complete diagnostic
expectations.

The platform preflight will run real transient-unit, timeout, cgroup,
Bubblewrap, broker, active-and-queued llama.cpp cancellation, slot-quiescence,
Pi, OpenCode, DNS, local endpoint, source, no-core crash, and cleanup probes. It
must leave no probe unit, cgroup process, server request, raw artifact, core
handler/file/payload, pending attempt, or credential-bearing output. An
injected deletion failure is the sole exception:
it must leave only the bounded private quarantine and recovery latch, claim no
raw absence, and prevent any subsequent probe until exact-name recovery passes.

Because implementation changes shell and may add Python parser support, the
repository gate is:

```bash
make validate
make test
```

## Acceptance Criteria

- One transient cell service contains every normal stage and descendant; one
  exact separately bounded sibling control service contains the broker and
  resource observer and is ready before gate release.
- The service runtime target starts before source fetch and remains 300 seconds
  without per-stage resets.
- The documentation and output call timing operational and make no exact
  signal-delivery claim.
- Systemd and the outer supervisor independently react to runtime expiry; an
  excessive calibrated delay aborts the matrix.
- Effective stop properties make `SIGKILL` the first signal after the
  10-second graceful interval.
- Every variable-size input, artifact, displayed block, result, and manifest
  has an enforced byte cap.
- The canonical storage plan has one writer, a closed schema, a checked derived
  cap, atomic publication, body and file hashes, deterministic terms, and named
  setup, supervisor, finalizer, and classifier consumers.
- `attempt_id` is restricted to 1 through 128 bytes of the stated ASCII grammar,
  and every legal maximum-field `matrix.log` line fits its 1,023-byte payload.
- Canonical alias parsing rejects duplicate members independently of decoder
  defaults, lone surrogates, controls, White_Space, noncharacters, and
  preset-unsafe forms; preserves every permitted decoded alias including
  `nested/model`; and restricts all JSON integers to
  `-9007199254740991..9007199254740991`.
- Configuration validation, preset rendering, client setup, server checks, and
  API discovery enforce that same alias grammar and exact alias set before
  screening; the harness never drops an alias accepted by supported
  configuration.
- Separate alias and globally unique cell ordinals give every identity a
  distinct unit, exchange, artifact, and result name while keeping alias text
  out of paths, units, and line protocols.
- Every valid discovered alias receives all four cells. Human output,
  `matrix.log`, alias file, manifest, temporary copies, recovery record, and run
  storage derive checked maxima from the alias and cell counts with no fixed
  five-alias ceiling.
- Every directory, current file, and atomic sibling is pre-created before model
  work. Btrfs gates reserve data and metadata from
  `btrfs filesystem usage --raw` without inode counters; the tested non-Btrfs
  fallback checks blocks and pre-creation inodes.
- Discovery and first calibration probes run under measured bootstrap memory,
  task, swap, and workspace limits; no transient workload is ever unbounded.
- Production memory, task, and workspace limits come from a successful,
  hash-bound base-model calibration rather than committed machine values; the
  formula conservatively adds every declared capacity and has no unmeasurable
  peak-occupancy term.
- The host-safety guarantee covers harness-side processes, files, and requests.
  Server request growth is measured and reported and quiescence is required,
  while llama.cpp internal memory and tasks remain governed by the existing
  server budget and one-slot configuration.
- `TasksMax` enforcement includes an observer outside the cell that sees
  `pids.events.max`, seals `resource-limit/tasks`, and writes the already-open
  exact cell `cgroup.kill`; the design does not rely on `pids.max` to kill
  existing tasks or on a reusable unit name.
- All client-writable directories share one size-limited Bubblewrap tmpfs whose
  deterministic calibrated cap is enforced in aggregate. Disabled nested user
  namespaces and zero capabilities prevent creation of another writable mount.
- Timeout, output, OOM, task exhaustion, wrapper loss, and capture failure
  cannot leave a process in the saved cell cgroup.
- The client knows only a cell-scoped broker token. Every authenticated
  cell-attributable llama.cpp request has one duplicate-free body whose root
  model equals the cell alias, an exact percent-encoded slot query, a bound
  selected-child identity and `id_task` proved by the fresh-session stream
  bracket, a forgotten router correlation mapping, a terminal broker record,
  and two idle slot samples before cell finalization. Stream deletion is not
  treated as child-session-eviction evidence.
- The control service survives cell `SIGKILL`, OOM, and task exhaustion long
  enough to finish or cancel upstream work and seal quiescence. Broker loss or
  unproved quiescence makes the attempt infrastructure-invalid and aborts the
  matrix; the harness never stops or kills `llm-server.service`.
- The production Bubblewrap profile blocks every reviewed cgroup and
  user-manager, nested-user-namespace, nested-mount-namespace, journald-socket,
  and writable-mount escape fixture without breaking Pi or OpenCode.
- A later cell starts only after one authoritative result exists, both exact
  services are inactive, both cgroups are absent or empty, server quiescence is
  proved, and raw cleanup succeeds.
- The result schema covers every run and cell path, including pre-identity
  failures, with closed record variants, qualifications, reasons, proof kinds,
  field types, nullability, canonical bytes, enums, and legal combinations.
- Every activation, discovery, setup, cleanup, credential/configuration, and
  artifact overflow path has one legal result mapping and continuation rule.
- Resource limits are safety controls; only the narrower direct predicate over
  complete adapter records whose byte ranges end within the shared causal
  cutoff can turn that cell into a model failure.
- Network attribution is limited to repeated exact commands and altered literal
  simple direct `curl`/`wget` commands that pinned adapters can prove. Wrapper,
  variable, substitution, alias, function, redirection, pipeline, and indirect
  executable syntax is not network attribution; generated-action exit,
  non-convergence, and final-evidence rules remain unchanged.
- A forbidden resource-control attempt is model failure only when complete
  evidence attributes an unsuccessful client exit to it; every other such
  attempt is an infrastructure-invalid boundary event.
- Missing or infrastructure-invalid required identities invalidate an attempt
  before any model-failure aggregation.
- Raw or complete redacted agent transcripts and stderr streams are never
  intentionally displayed or retained; every nonempty stream excerpt is
  intentionally incomplete and capped after redaction and serialization.
- The outer supervisor and every transient process have zero core limits,
  `PR_SET_DUMPABLE=0`, and an inherited filter that prevents restoring
  dumpability before credentials or raw bytes are read. No core handler,
  coredump journal payload, or core file is produced.
- Failed bounded raw deletion atomically moves the fixed private run to a mode
  `0700` quarantine, records exact allowlisted raw names and caps in a mode
  `0600` recovery record, sets `raw_absence=false`, and blocks all future
  attempts until verified exact-name deletion clears the quarantine.
- The seven ledger entries remain present and unchanged after successful final
  cleanup, the exact pre-recovery ledger bytes remain hash-verifiable, and
  recreated private tools derive from the identified reviewed plan source.
- `harness_sha256`, rendered private-definition hashes, and both legacy-ledger
  hashes have deterministic byte domains, ordering, length framing, and
  trailing-byte rules. Server drift uses only the closed credential-free
  identity projection and never reads credential-bearing argv or environment.
- The revised preflight passes before any new candidate attempt.
- A new bounded publisher screening and then a new bounded greedy screening can
  be classified without changing either sampler profile, prompt, source, or
  source/evidence validators; legacy unbounded results cannot advance
  qualification.
