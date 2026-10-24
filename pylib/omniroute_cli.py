"""Handlers for `llmenv.py omniroute-update <subcommand>`. Handlers take the injected `run` in-process (tests pass a
fake); there is no environment variable, pytest-detection or module-attribute seam that changes production behavior."""
from __future__ import annotations

import contextlib
import functools
import json
import math
import os
import shutil
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path

import yaml

from pylib import omniroute_characterize
from pylib.omniroute_activity import (
    Collector,
    IdleGate,
    JournalBatch,
    JournalctlReader,
    api_answers,
    bounded_get,
    bounded_login,
    classify,
    failed_sample,
    has_profile,
    load_profile,
    local_profile_dir,
    measure_clock_tolerance_ms,
    validate_overrides,
)
from pylib.omniroute_observer import lock_path, observe_loop, supervise
from pylib.omniroute_update import (
    Deferral,
    ImageIdentity,
    Reason,
    UpdateError,
    backend_env,
    build_projection,
    canon_image_id,
    check_private_dir,
    preflight,
    recheck,
    resolve_image,
    scrubbed_runner,
    verify_commit,
    verify_projection,
    verify_staged,
    with_deadline,
    write_preflight_artifacts,
    write_private,
)


def guarded(fn):
    """Exit-code contract: Deferral -> `deferred: <reason>` on stdout + 75; UpdateError -> sanitized stderr + 1."""
    @functools.wraps(fn)
    def wrapper(args, *a, **kw):
        try:
            return fn(args, *a, **kw)
        except Deferral as exc:
            print(f"deferred: {exc.reason.value}")
            return 75
        except UpdateError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return wrapper


@guarded
def cmd_resolve(args, run=None, env=None) -> int:
    run = run or scrubbed_runner(os.environ if env is None else env)
    identity = resolve_image(args.reference, (args.os, args.arch, args.variant), run)
    write_private(args.json, json.dumps(asdict(identity)))
    for key, value in asdict(identity).items():
        print(f"{key}={value}")
    return 0


@guarded
def cmd_preflight(args, run=None, env=None, which=shutil.which, api_ok=api_answers) -> int:
    env = os.environ if env is None else env
    run = run or scrubbed_runner(env)
    check_private_dir(args.out_dir)
    if args.recheck:
        recheck(args.out_dir, args.config, args.compose, env,
                with_deadline(run, time.monotonic() + args.budget))
        return 0
    cfg = yaml.safe_load(Path(args.config).read_text()) or {}
    dep, snap, fp, effective = preflight(cfg, args.config, args.compose, env, run,
                                         provider=which("podman-compose") or None, service=args.service,
                                         api_ok=api_ok)
    write_preflight_artifacts(args.out_dir, dep, snap, fp, effective, snap.docs.get(dep.container_id))
    for key, value in asdict(dep).items():
        if isinstance(value, (str, int, bool)):
            print(f"{key}={int(value) if isinstance(value, bool) else value}")
    return 0


def register(sub) -> None:
    group = sub.add_parser("omniroute-update")
    usub = group.add_subparsers(dest="update_command", required=True)
    resolve = usub.add_parser("resolve")
    resolve.add_argument("--reference", required=True)
    resolve.add_argument("--os", required=True)
    resolve.add_argument("--arch", required=True)
    resolve.add_argument("--variant", default="")
    resolve.add_argument("--json", required=True)
    resolve.set_defaults(func=cmd_resolve)
    pf = usub.add_parser("preflight")
    pf.add_argument("--config", required=True)
    pf.add_argument("--compose", required=True)
    pf.add_argument("--out-dir", required=True, dest="out_dir")
    pf.add_argument("--recheck", action="store_true")
    pf.add_argument("--budget", type=int, default=60)
    pf.add_argument("--service", default="omniroute")
    pf.set_defaults(func=cmd_preflight)
    ov = usub.add_parser("overrides")
    ov.add_argument("--idle")
    ov.add_argument("--max-wait", dest="max_wait")
    ov.set_defaults(func=cmd_overrides)
    dl = usub.add_parser("deadline")
    dl.add_argument("--file", required=True)
    dl.add_argument("--init", type=int)
    dl.set_defaults(func=cmd_deadline)
    lp = usub.add_parser("lock-path")
    lp.add_argument("--container", default="omniroute")
    lp.set_defaults(func=cmd_lock_path)
    ob = usub.add_parser("observe")
    ob.add_argument("--dir", required=True, dest="dir")
    ob.add_argument("--config", required=True)
    ob.add_argument("--compose", required=True)
    ob.add_argument("--password-file", required=True, dest="password_file")
    ob.add_argument("--lease-pid", required=True, type=int, dest="lease_pid")
    ob.add_argument("--lease-start-tick", required=True, dest="lease_start_tick")
    ob.add_argument("--lifetime", required=True, type=int)
    ob.add_argument("--interval", type=float, default=5)
    ob.add_argument("--min-wait", required=True, type=int, dest="min_wait")
    ob.add_argument("--deadline-file", required=True, dest="deadline_file")
    ob.set_defaults(func=cmd_observe)
    ac = usub.add_parser("activity")
    ac.add_argument("--inspect", required=True)
    ac.add_argument("--port", required=True, type=int)
    ac.add_argument("--password-file", required=True, dest="password_file")
    ac.add_argument("--min-wait", required=True, type=int, dest="min_wait")
    ac.set_defaults(func=cmd_activity)
    vs = usub.add_parser("verify-staged")
    vs.add_argument("--resolved", required=True)
    vs.add_argument("--inspect", required=True)
    vs.set_defaults(func=cmd_verify_staged)
    pj = usub.add_parser("projection")
    pj.add_argument("--dir", required=True, dest="dir")
    pj.add_argument("--service", required=True)
    pj.add_argument("--image", required=True)
    pj.add_argument("--out", required=True)
    pj.set_defaults(func=cmd_projection)
    ch = usub.add_parser("characterize")
    ch.add_argument("--config-id", required=True, dest="config_id")
    ch.add_argument("--budget", type=int, default=omniroute_characterize.BUDGET_S)
    ch.set_defaults(func=cmd_characterize)
    vc = usub.add_parser("verify-commit")
    vc.add_argument("--dir", required=True, dest="dir")
    vc.add_argument("--service", required=True)
    vc.add_argument("--old-id", required=True, dest="old_id")
    vc.add_argument("--config-id", dest="config_id", default=None)
    vc.add_argument("--timeout", required=True, type=int)
    vc.add_argument("--expect-no-deps", action="store_true", dest="expect_no_deps")
    vc.add_argument("--password-file", dest="password_file", default=None)
    vc.set_defaults(func=cmd_verify_commit)


@guarded
def cmd_overrides(args) -> int:
    min_wait, max_wait = validate_overrides(args.idle, args.max_wait)  # output is built only after validation
    print(f"min_wait={min_wait}\nmax_deferred_wait={max_wait}")
    return 0


@guarded
def cmd_deadline(args, clock=time.monotonic) -> int:
    if args.init is not None:
        if args.init < 1:
            raise UpdateError("--init must be a positive integer")
        write_private(args.file, repr(clock() + args.init))
        return 0
    try:
        deadline = float(Path(args.file).read_text())
    except (OSError, ValueError) as exc:
        raise UpdateError("deadline file is unreadable") from exc
    now = clock()
    if now >= deadline:
        print("deferred: deadline")
        return 75
    print(f"remaining={math.ceil(deadline - now)}")
    return 0


def cmd_lock_path(args, run=None) -> int:
    """The one handler that does not use `guarded`: stdout carries only the path; deferrals go to stderr."""
    try:
        print(lock_path(run or scrubbed_runner(os.environ), args.container))
        return 0
    except Deferral as exc:
        print(f"deferred: {exc.reason.value}", file=sys.stderr)
        return 75
    except UpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _fetcher(port: int, password: str):
    """Authenticated bounded GET against the local OmniRoute admin API (one login, renewed once on auth expiry)."""
    base = f"http://127.0.0.1:{port}"
    token: list[str] = []

    def fetch(path, deadline):
        if not token:
            token.append(bounded_login(base, password, deadline))
        try:
            return bounded_get(base, path, token[0], deadline)
        except Deferral as exc:
            if exc.reason is not Reason.auth:
                raise
            token[0] = bounded_login(base, password, deadline)
            return bounded_get(base, path, token[0], deadline)
    return fetch


class _NoJournal:
    """API-only sample: no journal records and a trivially continuous feed (the call-log rows carry arrivals)."""

    def poll(self, deadline, force_barrier=False):
        return JournalBatch(records=[], boot_id="", ready=True, barrier_seen=True, continuous=True, overflow=False)


@guarded
def cmd_activity(args) -> int:
    """One API-only sample (health, history, call-log rows) under the updater's own profile and client-vs-probe rule.
    Prints `idle`, or defers `busy` / the unknown reason. Shared by the commit worker's last look and the healer."""
    target = json.loads(Path(args.inspect).read_text())[0]
    profile = load_profile(canon_image_id(target.get("Image")), target)
    view = type("DepView", (), {"container_id": str(target.get("Id", "")),
                                "started_at": str((target.get("State") or {}).get("StartedAt", ""))})()
    collector = Collector(deployment=view, profile=profile, reader=_NoJournal(),
                          fetch=_fetcher(args.port, Path(args.password_file).read_text()), recheck=lambda _d: None,
                          tolerance_ms=measure_clock_tolerance_ms(time.time, time.monotonic), min_wait=args.min_wait)
    s = collector.sample(time.monotonic() + 10)
    state = classify(s)
    if state == "idle":
        print("idle")
        return 0
    if s.detail:
        print(f"detail: {s.detail}", file=sys.stderr)  # e.g. the unprofiled route a coverage deferral names
    raise Deferral(Reason.busy if state == "busy" else s.reason, "")


@guarded
def cmd_observe(args, run=None, env=None) -> int:
    env = os.environ if env is None else env
    run = run or scrubbed_runner(env)
    art_dir = args.dir
    dep = json.loads(Path(art_dir, "deployment.json").read_text())
    target = json.loads(Path(art_dir, "target.json").read_text())[0]
    gate = IdleGate(args.min_wait, float(Path(args.deadline_file).read_text()))
    view = type("DepView", (), {"container_id": dep["container_id"], "started_at": dep["started_at"]})()
    reader = None
    try:
        profile = load_profile(dep["image_id"], target)
        reader = JournalctlReader(dep["container_id"], env=backend_env(env))
        fetch = _fetcher(dep["port"], Path(args.password_file).read_text())

        def recheck_fn(deadline):
            recheck(art_dir, args.config, args.compose, env, with_deadline(run, deadline))

        collector = Collector(deployment=view, profile=profile, reader=reader, fetch=fetch, recheck=recheck_fn,
                              tolerance_ms=measure_clock_tolerance_ms(time.time, time.monotonic), min_wait=args.min_wait)

        def sample_fn(deadline, force_barrier=False):
            return collector.sample(deadline, force_barrier)
    except Deferral as exc:
        failure = exc  # a profile or construction failure is a sample, not a crash
        state = {"seq": 0}

        def sample_fn(deadline, force_barrier=False):
            state["seq"] += 1
            return failed_sample(dep["container_id"], dep["started_at"], state["seq"], failure.reason, time.monotonic(),
                                 failure.detail)

    latest = str(Path(art_dir, "latest.json"))
    supervise(lambda stop, end: observe_loop(sample_fn, gate, latest, args.interval, stop, end),
              reader.terminate if reader is not None else (lambda: None),
              args.lease_pid, args.lease_start_tick, args.lifetime,
              orphaned=lambda: shutil.rmtree(art_dir, ignore_errors=True))
    return 0  # supervise never returns


@guarded
def cmd_verify_staged(args) -> int:
    identity = ImageIdentity(**json.loads(Path(args.resolved).read_text()))
    inspected = json.loads(Path(args.inspect).read_text())
    if not (isinstance(inspected, list) and len(inspected) == 1 and isinstance(inspected[0], dict)):
        raise UpdateError("unexpected image inspect shape")
    verify_staged(identity, inspected[0])
    return 0


@guarded
def cmd_projection(args, run=None, env=None) -> int:
    env = os.environ if env is None else env
    run = run or scrubbed_runner(env)
    check_private_dir(args.dir)
    d = Path(args.dir)
    effective = json.loads((d / "effective.json").read_text())
    target = json.loads((d / "target.json").read_text())[0]
    dep = json.loads((d / "deployment.json").read_text())
    write_private(args.out, json.dumps(build_projection(effective, args.service, target, args.image, dep["project"])))
    verify_projection(effective, args.service, target, args.image, dep["project"], args.out, dep["provider"], run)
    return 0


@guarded
def cmd_verify_commit(args, run=None, env=None, probe=api_answers) -> int:
    env = os.environ if env is None else env
    run = run or scrubbed_runner(env)
    check_private_dir(args.dir)
    api_ok = None
    if args.password_file:  # OmniRoute: an answering authenticated health API also counts as ready
        port = json.loads(Path(args.dir, "deployment.json").read_text())["port"]
        password = Path(args.password_file).read_text()
        api_ok = functools.partial(probe, port, password, 5.0)
    print(f"new_id={verify_commit(run, args.dir, args.service, args.old_id, args.config_id, args.timeout, expect_no_deps=args.expect_no_deps, env=env, api_ok=api_ok)}")
    return 0


@contextlib.contextmanager
def _bounded(budget: int):
    """SIGALRM after `budget` s, TERM and HUP: raised in the main thread, so the harness's own cleanup runs."""
    def expire(_signum, _frame):
        raise TimeoutError(f"exceeded its {budget} s budget")

    def stop(signum, _frame):
        raise SystemExit(128 + signum)
    old = {sig: signal.signal(sig, handler)
           for sig, handler in ((signal.SIGALRM, expire), (signal.SIGTERM, stop), (signal.SIGHUP, stop))}
    signal.alarm(budget)
    try:
        yield
    finally:
        signal.alarm(0)
        for sig, handler in old.items():
            signal.signal(sig, handler)


@guarded
def cmd_characterize(args, collect=None, store_dir: Path | None = None) -> int:
    """Nothing to do when the image has a profile (shipped, or local and bound to its evidence). Otherwise run the
    characterization in the disposable isolated backend, derive the profile from the stored evidence and keep it in
    the user-local store when it is complete (exit 0); an incomplete candidate defers `capability` naming its reasons
    and the evidence file. The caller holds the update lock."""
    config_id = canon_image_id(args.config_id)
    store_dir = local_profile_dir() if store_dir is None else store_dir
    if has_profile(config_id, local_dir=store_dir):
        return 0
    if args.budget < 1:
        raise UpdateError("--budget must be a positive integer")
    collect = collect or omniroute_characterize.collect

    def phase(message: str) -> None:
        print(f"phase: {message}", flush=True)
    phase(f"characterizing {config_id[7:19]} (no activity profile) in a disposable isolated Podman backend, "
          f"budget {args.budget} s")
    try:
        with _bounded(args.budget):
            evidence = collect(config_id, log=lambda m: phase(f"characterizing: {m}"))
    except Exception as exc:  # any harness failure: its own cleanup ran, nothing was stored
        raise UpdateError(f"the characterization failed ({type(exc).__name__}: {str(exc)[:300]}); "
                          "nothing was stored") from exc
    phase("deriving")
    candidate, ev_path, profile_path = omniroute_characterize.derive_and_store(evidence, store_dir, log=phase)
    if profile_path is None:
        print(f"detail: the characterization of {config_id} is incomplete; evidence: {ev_path}", file=sys.stderr)
        for reason in candidate["incomplete"]:
            print(f"detail: - {reason}", file=sys.stderr)
        raise Deferral(Reason.capability, "")
    phase(f"stored the activity profile: {profile_path}")
    return 0
