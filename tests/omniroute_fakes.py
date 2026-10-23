import json
import socket
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pylib.omniroute_update import DEFAULT_IMAGE

OMNI, RS, SIB, NEW = "a" * 64, "b" * 64, "c" * 64, "9" * 64
NET, IMG = "e" * 64, "d" * 64
PROJECT, PASSWORD = "llm-env", "s3cret-pass"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def inspect_doc(cid, *, name=None, deps=(), state="running", project=PROJECT, port=20128,
                health="healthy", net=NET, image=IMG, image_name="docker.io/diegosouzapw/omniroute:latest",
                env=()):
    name = name or f"svc-{cid[:4]}"
    return {"Id": cid, "Name": name, "Image": image, "ImageName": image_name,
            "Dependencies": list(deps), "Mounts": [{"Type": "volume", "Name": "omniroute-data"}],
            "HostConfig": {"PortBindings": {f"{port}/tcp": [{"HostPort": str(port)}]},
                           "LogConfig": {"Type": "journald"}},
            "Config": {"Labels": {"io.podman.compose.project": project, "io.podman.compose.service": name},
                       "Env": list(env), "Entrypoint": ["node", "server.js"], "Cmd": ["--serve"], "WorkingDir": "/app",
                       "Healthcheck": {"Test": ["CMD", "true"], "Interval": 5000000000}},
            "NetworkSettings": {"Networks": {f"{project}_default": {"NetworkID": net}}},
            "State": {"Status": state, "Running": state == "running", "StartedAt": "2026-10-22T00:00:00Z",
                      "Health": {"Status": health}}}


def omni(**kw):
    kw.setdefault("env", [f"INITIAL_PASSWORD={PASSWORD}", "REQUIRE_API_KEY=true", "PORT=20128", "NODE_ENV=production"])
    return inspect_doc(OMNI, name="omniroute", **kw)


def fake_backend(containers, *, rootless=True, remote=False, extra=None):
    """Scripted Runner. `containers`: id -> inspect doc, or an Exception instance to raise on inspect.
    `extra`: list of (argv-prefix tuple, bytes) answered after the podman basics."""
    def run(argv, timeout=None):
        if argv[:2] == ["podman", "info"]:
            return json.dumps({"store": {"graphRoot": "/g", "runRoot": "/r"},
                               "host": {"security": {"rootless": rootless}, "serviceIsRemote": remote}}).encode()
        if argv[:2] == ["podman", "ps"]:
            return json.dumps([{"Id": cid} for cid in containers]).encode()
        if argv[:3] == ["podman", "container", "inspect"]:
            doc = containers[argv[-1]]
            if isinstance(doc, Exception):
                raise doc
            return json.dumps([doc]).encode()
        for prefix, out in (extra or []):
            if tuple(argv[:len(prefix)]) == prefix:
                return out
        raise AssertionError(f"unscripted command {argv}")
    return run


def with_dependent():       # RS now depends on OMNI
    return fake_backend({OMNI: omni(), RS: inspect_doc(RS, name="remote-setup", deps=[OMNI])})


def without_sibling():
    return fake_backend({OMNI: omni()})


def with_port_changed(cid):
    return fake_backend({OMNI: omni(), cid: inspect_doc(cid, name="sib", port=9999)})


def default_effective(port=20128, image_ref=DEFAULT_IMAGE, password=PASSWORD, **svc_patch):
    svc = {"image": image_ref, "container_name": "omniroute", "ports": [f"0.0.0.0:{port}:{port}"], "stop_grace_period": "40s",
           "environment": {"INITIAL_PASSWORD": password.replace("$", "$$"), "REQUIRE_API_KEY": "true"}}
    return {"services": {"omniroute": {**svc, **svc_patch}}, "networks": {"default": {}}, "volumes": {"omniroute-data": {}}}


def preflight_world(tmp_path, *, port=20128, password=PASSWORD, image=DEFAULT_IMAGE, omni_kw=None,
                    extra_containers=None, effective=None, env=None, svc_patch=None):
    cfg = {"omniroute": {"port": port, "image": image, "initial_password": password}}
    config_path = tmp_path / "models.yml"; config_path.write_text(yaml.safe_dump(cfg))
    compose_path = tmp_path / "docker-compose.yml"; compose_path.write_text("services:\n  omniroute: {}\n")
    prov = tmp_path / "podman-compose"; prov.write_bytes(b"#!/bin/sh\n"); prov.chmod(0o755)
    eff = effective or default_effective(port, image_ref=image, password=password, **(svc_patch or {}))
    containers = {OMNI: omni(**(omni_kw or {})), **(extra_containers or {})}
    run = fake_backend(containers, extra=[
        ((str(prov), "--version"), b"podman-compose 1.6.0\n"),
        ((str(prov), "--in-pod"), yaml.safe_dump(eff).encode()),
        (("podman", "image", "inspect"), json.dumps([{"Id": IMG, "Os": "linux", "Architecture": "amd64", "Variant": ""}]).encode()),
    ])
    return cfg, str(config_path), str(compose_path), str(prov), (env if env is not None else {}), run


# ---- Task 3: activity profile fixtures and sample factory ----

from pylib.omniroute_activity import Sample
from pylib.omniroute_update import Reason

API = {"health_timestamp": "/timestamp", "admission": ["/chatAdmission/activeHeavy", "/chatAdmission/activeHealthyHeadroom",
       "/chatAdmission/waiting", "/chatAdmission/queuedBytes"], "active": "/active",
       "pending_by_model": "/pending/byModel", "rows": "/logs", "row_id": "id", "row_memory_flag": "memory"}
ROUTES = [
    {"method": "POST", "path_pattern": "/v1/chat/completions", "class": "inference",
     "arrival": {"signal": "journal-info-http", "evidence": "c1"},
     "inflight": {"signal": "usage-history-active", "evidence": "c1"},
     "queue": {"signal": "health-admission", "evidence": "c1"}},
    {"method": "GET", "path_pattern": "/api/monitoring/health", "class": "probe", "evidence": "c1"},
    {"method": "POST", "path_pattern": "/api/auth/login", "class": "probe", "evidence": "c1"},
]


def profile_entry(**kw):
    entry = {"config_id": "sha256:" + "c" * 64, "artifacts": {"app/server.js": "0" * 64},
             "entrypoint": ["node", "server.js"], "env_allowlist": {"NODE_ENV": "production"},
             "logger": {"destination": "journald-stdout", "min_level": "info", "level_env": "LOG_LEVEL",
                        "default_level": "info", "buffering": "line",
                        "loss_detection": "cursor-continuity+journald-rate-limit-checked",
                        "fields": {"method": "method", "path": "path"}},
             "routes": ROUTES,
             "reachable_routes": ["POST /v1/chat/completions", "GET /api/monitoring/health", "POST /api/auth/login"],
             "api_fields": API, "health_cache_ms": 1000, "health_refresh_bound_ms": 500,
             "evidence": {"characterization_sha256": "1" * 64, "measured_at": "2026-10-22"}}
    entry.update(kw)
    return entry


def write_profiles(tmp_path, *entries):
    p = tmp_path / "profiles.json"
    p.write_text(json.dumps({"schema": 1, "profiles": list(entries)}))
    return p


CONTAINER = {"Config": {"Env": ["NODE_ENV=production"], "Entrypoint": ["node", "server.js"]},
             "HostConfig": {"LogConfig": {"Type": "journald"}}}


def mk_sample(**kw):
    base = {"schema": 1, "profile_sha256": "0" * 64, "container_id": "a" * 64, "started_at": "t", "boot_id": "b", "sequence": 1,
                "sample_monotonic": 1.0, "arrival_generation": 0, "row_generation": 0, "active": 0, "pending": 0, "admission": 0,
                "health_timestamp_ms": 1000, "health_age_ms": 10, "feed_ready": True, "barrier_seen": True, "continuous": True,
                "overflow": False, "unknown": False, "reason": Reason.ready}
    return Sample(**{**base, **kw})
