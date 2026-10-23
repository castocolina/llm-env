"""Handlers for `llmenv.py omniroute-update <subcommand>`. Handlers take the injected `run` in-process (tests pass a
fake); there is no environment variable, pytest-detection or module-attribute seam that changes production behavior."""
from __future__ import annotations

import functools
import json
import math
import os
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path

import yaml

from pylib.omniroute_activity import (
    Collector,
    IdleGate,
    JournalctlReader,
    bounded_get,
    bounded_login,
    failed_sample,
    load_profile,
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
def cmd_preflight(args, run=None, env=None, which=shutil.which) -> int:
    env = os.environ if env is None else env
    run = run or scrubbed_runner(env)
    check_private_dir(args.out_dir)
    if args.recheck:
        recheck(args.out_dir, args.config, args.compose, env,
                with_deadline(run, time.monotonic() + args.budget))
        return 0
    cfg = yaml.safe_load(Path(args.config).read_text()) or {}
    dep, snap, fp, effective = preflight(cfg, args.config, args.compose, env, run,
                                         provider=which("podman-compose") or None, service=args.service)
    write_preflight_artifacts(args.out_dir, dep, snap, fp, effective, snap.docs[dep.container_id])
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
    vc = usub.add_parser("verify-commit")
    vc.add_argument("--dir", required=True, dest="dir")
    vc.add_argument("--service", required=True)
    vc.add_argument("--old-id", required=True, dest="old_id")
    vc.add_argument("--config-id", dest="config_id", default=None)
    vc.add_argument("--timeout", required=True, type=int)
    vc.add_argument("--expect-no-deps", action="store_true", dest="expect_no_deps")
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
        password = Path(args.password_file).read_text()
        base = f"http://127.0.0.1:{dep['port']}"
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

        def recheck_fn(deadline):
            recheck(art_dir, args.config, args.compose, env, with_deadline(run, deadline))

        collector = Collector(deployment=view, profile=profile, reader=reader, fetch=fetch, recheck=recheck_fn,
                              tolerance_ms=measure_clock_tolerance_ms(time.time, time.monotonic))

        def sample_fn(deadline, force_barrier=False):
            return collector.sample(deadline, force_barrier)
    except Deferral as exc:
        failure = exc  # a profile or construction failure is a sample, not a crash
        state = {"seq": 0}

        def sample_fn(deadline, force_barrier=False):
            state["seq"] += 1
            return failed_sample(dep["container_id"], dep["started_at"], state["seq"], failure.reason, time.monotonic())

    latest = str(Path(art_dir, "latest.json"))
    supervise(lambda stop, end: observe_loop(sample_fn, gate, latest, args.interval, stop, end),
              reader.terminate if reader is not None else (lambda: None),
              args.lease_pid, args.lease_start_tick, args.lifetime)
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
def cmd_verify_commit(args, run=None, env=None) -> int:
    env = os.environ if env is None else env
    run = run or scrubbed_runner(env)
    check_private_dir(args.dir)
    print(f"new_id={verify_commit(run, args.dir, args.service, args.old_id, args.config_id, args.timeout, expect_no_deps=args.expect_no_deps, env=env)}")
    return 0
