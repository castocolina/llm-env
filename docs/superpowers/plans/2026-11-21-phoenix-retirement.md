# Plan: retire Phoenix; Laya request telemetry through a file sink into Grafana

Date: 2026-11-21. Source: `tmp/forensics-20261120/PH1/design.md` (read-only research, evidence-dated 2026-11-21).
Roles and rules are those of `2026-11-20-recovery-and-usage-observability.md`: the planner plans and verifies, tasks go to
`pi router-env/my-coding-lite`, every task is checked by an independent verifier, no commits, staged apply, one pytest at a time.

## Owner decisions (2026-11-21)
1. Replacement: option D, a bounded JSONL file sink written by Laya's observer (question and answer text DROPPED), read by the existing host collector. No new container.
2. History: accept the gap (no backfill of the 1,542 older spans; the window with no samples stays a labelled gap in the dashboard text). The collector's one-call `limit=1000` Phoenix read is retired with Phoenix.
3. Restart: one Laya recreate through the idle-gated `make update-system-one`, run by the planner after verification, without further questions.
Planner defaults (consistent with the owner's "remove everything Phoenix"): remove `phoenix-trial` too; no token/routing counters now (completion tokens are zero upstream); no trace UI; keep the Phoenix export file and take a 0600 volume backup under `tmp/` before the volume is removed; the owner deletes both when ready.

## Record contract (one JSON object per line, UTF-8, `\n`-terminated, at most 4 KiB, no free text)
`{"v":1,"ts":"<ISO8601 UTC request start>","span_id":"<16 hex>","name":"laya.systemone|laya.batch","endpoint":"/v1/systemone|/v1/systemone/batch","batch":false,"batch_items":1,"http_status":200,"duration_ms":23.4,"requested_questions":1,"completed_answers":1,"answers":{"noul":0,"choice":1,"score":0},"confidences":[0.93],"model":"laya-rl-agent","error_type":null,"truncated":false}`
`answers` and `confidences` are derived inside the observer from the response (numbers only, never the text); `model` and `error_type` come from a short allowlist or null; the file is `laya-spans.jsonl` in a host-visible state directory (bind mount), size-capped with one rotation (`laya-spans.jsonl.1`), appended with one `write` per record.

## Tasks
- PH-A (main checkout, staged): Laya observer file sink + compose/render/build changes + Laya-side tests; removes `LAYA_PHOENIX_URL` and the `phoenix-trace` network; the image is rebuilt by the existing observer build script.
- PH-B (observe worktree, staged): the collector reads the file (byte-offset cursor, rotation by inode, torn last line deferred, bounded read, privacy), replaces the Phoenix HTTP read, keeps the `llm_env_laya_*` families; dashboards updated (source-up, text about the gap).
- PH-C (main checkout, staged): Phoenix removal checklist items 3-10 of the design (scripts, Makefile, `llmenv.py phoenix-trial`, checks, docs, tests, `docker/phoenix*`, resource policy key), leaving `docs/superpowers/**` as history.
- PH-D (planner, live): idle-gated `make update-system-one` with the new image; one real request proves one new line; collector shows source-up and counters; then backup + removal of the Phoenix container, volume and network; acceptance `rg -i phoenix` only in docs history, `podman ps -a|volume ls|network ls` clean, `make check-server` passes.
Rollback: the previous image `localhost/llm-env-system-one-phoenix:05891c21a1ce` is kept until PH-D finishes; Phoenix volume kept until the backup is verified.
