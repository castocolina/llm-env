"""OmniRoute-only updater: immutable image resolution and deployment preflight."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

Runner = Callable[..., bytes]  # run(argv, timeout=None) -> stdout bytes
Platform = tuple[str, str, str]

_MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
_INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}


class UpdateError(Exception):
    """Operational error (exit 1); deferrals use Deferral instead."""


class Reason(str, Enum):
    """Closed deferral/sample reason set (spec ORU-R1-006); stdout prints only these values."""
    ready = "ready"
    busy = "busy"
    capability = "capability"
    coverage = "coverage"
    logging = "logging"
    feed = "feed"
    overflow = "overflow"
    auth = "auth"
    schema = "schema"
    age = "age"
    clock = "clock"
    continuity = "continuity"
    deadline = "deadline"
    ownership = "ownership"
    health = "health"
    lock = "lock"
    input = "input"
    io = "io"
    state = "state"  # the observer cannot write its own verdict file (disk full, quota): not a feed problem


class Deferral(Exception):
    """Safe, expected refusal to proceed (exit 75). `detail` is sanitized, never printed to stdout."""

    def __init__(self, reason: Reason, detail: str = ""):
        super().__init__(f"{reason.value}: {detail}" if detail else reason.value)
        self.reason, self.detail = reason, detail


@dataclass(frozen=True)
class ImageIdentity:
    repository: str
    source_digest: str
    manifest_digest: str
    immutable_ref: str
    config_id: str
    os: str
    architecture: str
    variant: str
    kind: str


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_IMAGE_ID_RE = re.compile(r"(?:sha256:)?([0-9a-f]{64})")
LABEL_PROJECT = "io.podman.compose.project"
LABEL_SERVICE = "io.podman.compose.service"
DEFAULT_IMAGE = "docker.io/diegosouzapw/omniroute:latest"
_ID_RE = re.compile(r"[0-9a-f]{64}")
_UNSUPPORTED = ("include", "extends", "env_file", "secrets", "configs", "build",
                "network_mode", "pod", "volumes_from", "links")


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def canon_image_id(value: str) -> str:
    """The one canonical image-config identity: `sha256:<64 lowercase hex>` (Podman reports the bare hex)."""
    match = _IMAGE_ID_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise UpdateError("malformed image id")
    return "sha256:" + match.group(1)


def _digest(raw: bytes) -> str:
    # Deliberate in-Python equivalent of `skopeo manifest-digest FILE` (spec ORU-R1-003 step 1):
    # sha256 over the exact raw bytes skopeo returned; never re-serialize JSON, never strip bytes.
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def parse_reference(ref: str) -> tuple[str, str, str]:
    """(repository, tag, digest). Only the documented Docker Hub aliases are normalized; a digest pin is kept."""
    name, _, digest = ref.partition("@")
    tag = ""
    if ":" in name.rsplit("/", 1)[-1]:
        name, tag = name.rsplit(":", 1)
    elif not digest:
        tag = "latest"
    head = name.split("/")[0]
    if "/" not in name:
        name = "docker.io/library/" + name
    elif "." not in head and ":" not in head and head != "localhost":
        name = "docker.io/" + name
    return name, tag, digest


def _fetch(ref: str, run: Runner) -> tuple[bytes, dict]:
    raw = run(["skopeo", "inspect", "--raw", f"docker://{ref}"])
    try:
        return raw, json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UpdateError(f"manifest for {ref} is not JSON") from exc


def _config_id(doc: dict, ref: str) -> str:
    if doc.get("schemaVersion") != 2 or doc.get("mediaType") not in _MANIFEST_TYPES:
        raise UpdateError(f"unsupported manifest type for {ref}")
    config = doc.get("config")
    digest = config.get("digest") if isinstance(config, dict) else None
    if not _valid_digest(digest):
        raise UpdateError(f"manifest for {ref} has no config digest")
    return str(digest)


def resolve_image(reference: str, platform: Platform, run: Runner) -> ImageIdentity:
    os_, arch, variant = platform
    repo = parse_reference(reference)[0]
    raw, doc = _fetch(reference, run)
    source = _digest(raw)
    if "@sha256:" in reference and doc.get("mediaType") in _INDEX_TYPES:
        raise Deferral(Reason.input, "pinned reference selects an index, not a single manifest")
    if doc.get("mediaType") in _INDEX_TYPES:
        matches = [
            m for m in doc.get("manifests", [])
            if m.get("mediaType") in _MANIFEST_TYPES
            and (m.get("platform") or {}).get("os") == os_
            and (m.get("platform") or {}).get("architecture") == arch
            and (m.get("platform") or {}).get("variant", "") == variant
        ]
        if len(matches) != 1:
            raise UpdateError(
                f"no runnable manifest (or ambiguous: {len(matches)}) for {os_}/{arch}/{variant}"
            )
        manifest_digest = matches[0].get("digest")
        if not _valid_digest(manifest_digest):
            raise UpdateError("selected descriptor digest is malformed")
        sel_raw, sel_doc = _fetch(f"{repo}@{manifest_digest}", run)
        if _digest(sel_raw) != manifest_digest:
            raise UpdateError("selected manifest bytes do not match the index descriptor")
        return ImageIdentity(repo, source, manifest_digest, f"{repo}@{manifest_digest}",
                             _config_id(sel_doc, manifest_digest), os_, arch, variant, "index")
    return ImageIdentity(repo, source, source, f"{repo}@{source}",
                         _config_id(doc, reference), os_, arch, variant, "single")


def verify_staged(identity: ImageIdentity, image_inspect: dict) -> None:
    """Check the locally staged image (`podman image inspect` JSON) against the resolved identity."""
    if canon_image_id(image_inspect.get("Id", "")) != identity.config_id:
        raise UpdateError("staged image config id does not match the resolved identity")
    got = (image_inspect.get("Os"), image_inspect.get("Architecture"), image_inspect.get("Variant", ""))
    if got != (identity.os, identity.architecture, identity.variant):
        raise UpdateError("staged image platform does not match the resolved identity")


_BACKEND_VARS = ("PATH", "HOME", "XDG_RUNTIME_DIR",
                 "CONTAINERS_STORAGE_CONF", "CONTAINERS_CONF", "CONTAINERS_REGISTRIES_CONF")


def backend_env(env: Mapping[str, str]) -> dict[str, str]:
    """The one environment every Podman/provider/skopeo/journalctl child runs under (matches tools/lib.sh)."""
    out = {name: env[name] for name in _BACKEND_VARS if env.get(name)}
    if "PATH" not in out or "HOME" not in out:
        raise UpdateError("PATH and HOME must be set")
    return out


def default_runner(argv: list[str], timeout: float | None = None, env: Mapping[str, str] | None = None) -> bytes:
    """Production Runner: one bounded subprocess; stderr is never echoed (it may carry secrets)."""
    try:
        return subprocess.run(argv, timeout=60 if timeout is None else timeout,
                              check=True, capture_output=True, env=env).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        raise UpdateError(f"{argv[0]} failed: {type(exc).__name__}") from exc


def scrubbed_runner(env: Mapping[str, str]) -> Runner:
    clean = backend_env(env)

    def run(argv: list[str], timeout: float | None = None) -> bytes:
        return default_runner(argv, timeout, env=clean)
    return run


def with_deadline(run: Runner, deadline: float, clock: Callable[[], float] = time.monotonic) -> Runner:
    """Wrap `run` so every command shares one budget; a result completed at/after the deadline is discarded."""
    def bounded(argv: list[str], timeout: float | None = None) -> bytes:
        remaining = deadline - clock()
        if remaining <= 0:
            raise UpdateError("deadline exceeded before command start")
        out = run(argv, timeout=min(remaining, 60 if timeout is None else timeout))
        if clock() >= deadline:
            raise UpdateError("deadline exceeded; result discarded")
        return out
    return bounded


def write_private(path: str, text: str) -> None:
    """Atomically write a 0600 file (callers place it inside a 0700 directory)."""
    tmp = f"{path}.tmp{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _json(raw_or_call, what: str):
    try:
        return json.loads(raw_or_call)
    except (json.JSONDecodeError, TypeError) as exc:
        raise Deferral(Reason.io, f"{what} output is not JSON") from exc


def _inventory(run: Runner, argv: list[str], what: str):
    try:
        return _json(run(argv), what)
    except UpdateError as exc:
        raise Deferral(Reason.io, f"{what} command failed") from exc


def _canon_hash(obj) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    backend: dict
    containers: dict[str, str]
    deps: frozenset[tuple[str, str]]
    docs: dict[str, dict] = field(default_factory=dict, compare=False, repr=False)

    def to_json(self) -> str:
        return json.dumps({"backend": self.backend, "containers": self.containers,
                           "deps": sorted([list(d) for d in self.deps])}, sort_keys=True)

    @staticmethod
    def from_json(text: str) -> Snapshot:
        raw = json.loads(text)
        return Snapshot(raw["backend"], raw["containers"], frozenset(tuple(d) for d in raw["deps"]))


def take_snapshot(run: Runner, env: Mapping[str, str] = os.environ) -> Snapshot:
    if env.get("CONTAINER_HOST") or env.get("CONTAINER_CONNECTION"):
        raise Deferral(Reason.input, "remote Podman connection configured")
    info = _inventory(run, ["podman", "info", "--format", "json"], "info")
    store, host = info.get("store") or {}, info.get("host") or {}
    if (host.get("security") or {}).get("rootless") is not True or host.get("serviceIsRemote") is not False:
        raise Deferral(Reason.ownership, "backend is not a local rootless Podman")
    try:
        backend = {"graphRoot": os.path.realpath(store["graphRoot"]), "runRoot": os.path.realpath(store["runRoot"]),
                   "rootless": True, "remote": False}
    except (KeyError, TypeError) as exc:
        raise Deferral(Reason.schema, "Podman store roots are missing") from exc
    rows = _inventory(run, ["podman", "ps", "--all", "--no-trunc", "--format", "json"], "ps")
    if not isinstance(rows, list) or any(not isinstance(c, dict) or "Id" not in c for c in rows):
        raise Deferral(Reason.schema, "unexpected container inventory shape")
    ids = [c["Id"] for c in rows]
    containers, deps, docs = {}, set(), {}
    for cid in ids:
        if not isinstance(cid, str) or not _ID_RE.fullmatch(cid):
            raise Deferral(Reason.schema, "malformed container id")
        got = _inventory(run, ["podman", "container", "inspect", "--format", "json", cid], "inspect")
        if not (isinstance(got, list) and len(got) == 1 and isinstance(got[0], dict) and got[0].get("Id") == cid):
            raise Deferral(Reason.schema, "unexpected inspect shape")
        d = got[0]
        dependencies = d.get("Dependencies")
        if not isinstance(dependencies, list) or not all(isinstance(x, str) and _ID_RE.fullmatch(x) for x in dependencies):
            raise Deferral(Reason.schema, "missing or malformed Dependencies")
        deps.update((cid, t) for t in dependencies)
        docs[cid] = d
        containers[cid] = _canon_hash(
            {k: d.get(k) for k in ("Id", "Name", "Image", "ImageName", "Dependencies", "Mounts", "HostConfig", "Config")}
            | {"networks": {n: v.get("NetworkID") for n, v in ((d.get("NetworkSettings") or {}).get("Networks") or {}).items()},
               "started": (d.get("State") or {}).get("StartedAt")})
    return Snapshot(backend, containers, frozenset(deps), docs)


def incoming(snap: Snapshot, target_id: str) -> list[str]:
    return sorted(dep for dep, tgt in snap.deps if tgt == target_id)


def assert_unchanged(before: Snapshot, after: Snapshot) -> None:
    if before.backend != after.backend or before.containers != after.containers or before.deps != after.deps:
        raise Deferral(Reason.ownership, "backend inventory changed")


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _strings(value)


def reject_unsupported(effective: dict) -> None:
    for name, svc in (effective.get("services") or {}).items():
        if any(key in svc for key in _UNSUPPORTED):
            raise Deferral(Reason.input, f"unsupported compose key on service {name}")
        if svc.get("scale", 1) != 1 or (svc.get("deploy") or {}).get("replicas", 1) != 1:
            raise Deferral(Reason.input, f"service {name} is scaled")
        if any("${" in value for value in _strings(svc)):
            raise Deferral(Reason.input, "unresolved interpolation")
        for volume in svc.get("volumes") or []:
            if isinstance(volume, str) and ":" not in volume:
                raise Deferral(Reason.input, "anonymous volume")


def _env_dict(env) -> dict[str, str]:
    if isinstance(env, dict):
        return {k: "" if v is None else str(v) for k, v in env.items()}
    return {key: value for item in env or [] for key, _, value in [str(item).partition("=")]}


def fingerprint_parts(config_path, compose_path, provider, effective, env, snap, run) -> dict[str, bytes]:
    if any(name.startswith(("COMPOSE_", "PODMAN_COMPOSE_")) for name in env):
        raise Deferral(Reason.input, "compose environment override set")
    reject_unsupported(effective)
    config_bytes, compose_bytes = Path(config_path).read_bytes(), Path(compose_path).read_bytes()
    referenced = sorted(set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", compose_bytes.decode("utf-8", "replace"))))
    version = run([provider, "--version"])
    return {
        "config": config_bytes, "config-path": os.path.realpath(config_path).encode(),
        "compose": compose_bytes, "compose-path": os.path.realpath(compose_path).encode(),
        "effective": json.dumps(effective, sort_keys=True).encode(),
        "interpolation-env": json.dumps({n: env.get(n) for n in referenced}, sort_keys=True).encode(),
        "provider": Path(provider).read_bytes() + b"\0" + version,
        "backend": json.dumps(snap.backend, sort_keys=True).encode(),
        "containers": json.dumps(snap.containers, sort_keys=True).encode(),
        "deps": json.dumps(sorted(list(d) for d in snap.deps)).encode(),
    }


def fingerprint(parts: dict[str, bytes]) -> str:
    lines = b"".join(name.encode() + b"\0" + hashlib.sha256(parts[name]).hexdigest().encode() + b"\n"
                     for name in sorted(parts))
    return "sha256:" + hashlib.sha256(lines).hexdigest()


def same_image(configured: str, running: str) -> bool:
    c_repo, c_tag, c_digest = parse_reference(configured)
    r_repo, r_tag, r_digest = parse_reference(running)
    if c_repo != r_repo:
        return False
    if c_digest:
        return r_digest == c_digest
    return bool(r_digest) or r_tag == c_tag


def _published_ports(svc: dict) -> set[str]:
    out = set()
    for entry in svc.get("ports") or []:
        if isinstance(entry, dict):
            out.add(str(entry.get("published", "")))
        else:
            parts = str(entry).split("/")[0].split(":")
            out.add(parts[-2] if len(parts) >= 2 else "")
    return out


def effective_config(provider: str, project: str, compose_path: str, run: Runner) -> dict:
    out = run([provider, "--in-pod", "false", "--env-file", "/dev/null", "-p", project, "-f", compose_path, "config"])
    try:
        effective = yaml.safe_load(out)
    except yaml.YAMLError as exc:
        raise Deferral(Reason.io, "provider config output is not YAML") from exc
    if not isinstance(effective, dict) or not isinstance(effective.get("services"), dict):
        raise Deferral(Reason.schema, "provider config has no services")
    return effective


@dataclass(frozen=True)
class Deployment:
    container_id: str
    container_name: str
    project: str
    image_id: str
    image_ref: str
    started_at: str
    port: int
    stop_seconds: int
    pinned: bool
    os: str
    architecture: str
    variant: str
    provider: str
    network_ids: tuple[str, ...]
    volume_names: tuple[str, ...]
    sibling_ids: tuple[str, ...]
    state: str = "running"  # running | stopped | absent (a down target skips the activity observer)


def _env_value(doc: dict, name: str) -> str | None:
    for item in (doc.get("Config") or {}).get("Env") or []:
        key, _, value = item.partition("=")
        if key == name:
            return value
    return None


_DOWN_STATUSES = ("created", "configured", "exited", "stopped")
_PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*")
LABEL_WORKDIR = "com.docker.compose.project.working_dir"


def _absent_project(snap: Snapshot, compose_path: str) -> str:
    """podman-compose's own default project for `-f compose_path` (top-level `name`, else the directory name).

    Any container that podman-compose created from the same directory must carry that project label."""
    directory = os.path.realpath(os.path.dirname(compose_path))
    try:
        top = yaml.safe_load(Path(compose_path).read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise Deferral(Reason.input, "deployed compose file is unreadable") from exc
    project = str((top.get("name") if isinstance(top, dict) else None) or os.path.basename(directory).lower())
    if not _PROJECT_RE.fullmatch(project):
        raise Deferral(Reason.input, "cannot derive the compose project name")
    for d in snap.docs.values():
        labels = (d.get("Config") or {}).get("Labels") or {}
        if labels.get(LABEL_WORKDIR) == directory and labels.get(LABEL_PROJECT) != project:
            raise Deferral(Reason.ownership, "containers from this compose directory use another project")
    return project


def preflight(cfg, config_path, compose_path, env, run, provider: str | None = None, service: str = "omniroute",
              api_ok: Callable[[int, str], bool] | None = None):
    """Ownership/agreement checks. For OmniRoute the target may also be stopped or absent (nothing can be in flight,
    so the caller skips the activity observer); a running OmniRoute whose Podman health is not `healthy` is accepted
    only when `api_ok(port, password)` proves its authenticated health API answers (the observer needs exactly that)."""
    snap = take_snapshot(run, env)
    omni_cfg = cfg.get("omniroute") or {}
    port, image_ref = int(omni_cfg.get("port", 20128)), omni_cfg.get("image", DEFAULT_IMAGE)
    password = str(omni_cfg.get("initial_password", ""))
    matches = [cid for cid, d in snap.docs.items()
               if ((d.get("Config") or {}).get("Labels") or {}).get(LABEL_SERVICE) == service]
    if service == "omniroute" and not matches:
        if any(str(d.get("Name", "")).lstrip("/") == "omniroute" for d in snap.docs.values()):
            raise Deferral(Reason.ownership, "an unlabeled container already uses the name omniroute")
        cid, doc, mode = "", {}, "absent"
        project = _absent_project(snap, compose_path)
    elif len(matches) != 1:
        raise Deferral(Reason.ownership, f"expected exactly one container for service {service}")
    else:
        cid = matches[0]
        doc = snap.docs[cid]
        project = ((doc.get("Config") or {}).get("Labels") or {}).get(LABEL_PROJECT)
        if not project:
            raise Deferral(Reason.ownership, "project label missing")
        mode = "running" if (doc.get("State") or {}).get("Running") is True else "stopped"
    state = doc.get("State") or {}
    health = (state.get("Health") or {}).get("Status")
    if service != "omniroute":
        if mode != "running" or health != "healthy":
            raise Deferral(Reason.ownership, "target is not running and healthy")
        port, image_ref = 0, doc.get("ImageName", "")
    elif mode == "stopped" and (state.get("Paused") or str(state.get("Status", "")).lower() not in _DOWN_STATUSES):
        raise Deferral(Reason.ownership, "target is neither running nor cleanly stopped")
    elif mode != "absent":
        if not same_image(image_ref, doc.get("ImageName", "")):
            raise Deferral(Reason.ownership, "running image reference differs from configuration")
        bindings = ((doc.get("HostConfig") or {}).get("PortBindings") or {}).get(f"{port}/tcp") or []
        if [b.get("HostPort") for b in bindings] != [str(port)]:
            raise Deferral(Reason.ownership, "published port differs from configuration")
        running_pw = _env_value(doc, "INITIAL_PASSWORD") or ""
        if not hmac.compare_digest(running_pw.encode(), password.encode()):
            raise Deferral(Reason.ownership, "dashboard password differs from configuration")
        if _env_value(doc, "REQUIRE_API_KEY") != "true":
            raise Deferral(Reason.ownership, "REQUIRE_API_KEY is not true")
        # Podman health can be wrong on hosts where its exec-based probe fails (e.g. exit 125 "unable to find user
        # node"); the authenticated API is what the observer needs, so it is the deciding signal.
        if mode == "running" and health != "healthy" and (api_ok is None or not api_ok(port, password)):
            raise Deferral(Reason.health, "running target is not healthy and its authenticated health API does not answer")
    if cid and incoming(snap, cid):
        raise Deferral(Reason.ownership, "a container depends on the target")
    provider = provider or shutil.which("podman-compose") or ""
    if not provider:
        raise Deferral(Reason.input, "podman-compose not found")
    effective = effective_config(provider, project, compose_path, run)
    svc = (effective["services"] or {}).get(service)
    if not isinstance(svc, dict) or (service == "omniroute" and svc.get("container_name") != "omniroute"):
        raise Deferral(Reason.ownership, f"effective compose has no matching {service} service")
    parts = fingerprint_parts(config_path, compose_path, provider, effective, env, snap, run)
    if service == "omniroute":
        if parse_reference(str(svc.get("image", ""))) != parse_reference(image_ref):
            raise Deferral(Reason.ownership, "effective compose image differs from configuration")
        if _published_ports(svc) != {str(port)}:
            raise Deferral(Reason.ownership, "effective compose published port differs from configuration")
        effective_env = _env_dict(svc.get("environment"))
        if effective_env.get("INITIAL_PASSWORD", "") not in (password, password.replace("$", "$$")):
            raise Deferral(Reason.ownership, "effective compose dashboard password differs from configuration")
        if effective_env.get("REQUIRE_API_KEY") != "true":
            raise Deferral(Reason.ownership, "effective compose REQUIRE_API_KEY is not true")
    grace = re.fullmatch(r"(\d+)s", str(svc.get("stop_grace_period", "" if service == "omniroute" else "10s")))
    if not grace:
        raise Deferral(Reason.input, "stop_grace_period must be whole seconds")
    if cid:
        image_info = _inventory(run, ["podman", "image", "inspect", "--format", "json", doc["Image"]], "image inspect")[0]
        platform = (image_info.get("Os", ""), image_info.get("Architecture", ""), image_info.get("Variant", ""))
    else:  # nothing to inspect: resolve for this host's own platform
        host = _inventory(run, ["podman", "info", "--format", "json"], "info").get("host") or {}
        platform = (host.get("os", ""), host.get("arch", ""), host.get("variant", ""))
    siblings = sorted(c for c, d in snap.docs.items()
                      if c != cid and ((d.get("Config") or {}).get("Labels") or {}).get(LABEL_PROJECT) == project)
    dep = Deployment(
        cid, service, project, canon_image_id(doc["Image"]) if cid else "", image_ref, state.get("StartedAt", ""),
        port, int(grace.group(1)), "@sha256:" in image_ref, *platform, provider,
        tuple(sorted({v.get("NetworkID") for v in ((doc.get("NetworkSettings") or {}).get("Networks") or {}).values()})),
        tuple(sorted(m["Name"] for m in doc.get("Mounts") or [] if m.get("Type") == "volume")),
        tuple(siblings), mode)
    return dep, snap, fingerprint(parts), effective


def write_preflight_artifacts(out_dir: str, dep: Deployment, snap: Snapshot, fp: str, effective: dict,
                              target_doc: dict | None) -> None:
    write_private(f"{out_dir}/snapshot.json", snap.to_json())
    write_private(f"{out_dir}/fingerprint.txt", fp + "\n")
    write_private(f"{out_dir}/effective.json", json.dumps(effective))
    write_private(f"{out_dir}/target.json", json.dumps([target_doc] if target_doc else []))  # [] = absent target
    write_private(f"{out_dir}/deployment.json", json.dumps(asdict(dep)))


def check_private_dir(path: str) -> None:
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise Deferral(Reason.lock, "artifact directory is not a private 0700 directory owned by this user")


def recheck(out_dir: str, config_path: str, compose_path: str, env, run: Runner) -> None:
    check_private_dir(out_dir)
    before = Snapshot.from_json(Path(out_dir, "snapshot.json").read_text())
    expected = Path(out_dir, "fingerprint.txt").read_text().strip()
    dep = json.loads(Path(out_dir, "deployment.json").read_text())
    try:
        snap = take_snapshot(run, env)
        assert_unchanged(before, snap)
        if incoming(snap, dep["container_id"]):
            raise Deferral(Reason.ownership, "a container now depends on the target")
        effective = effective_config(dep["provider"], dep["project"], compose_path, run)
        if fingerprint(fingerprint_parts(config_path, compose_path, dep["provider"], effective, env, snap, run)) != expected:
            raise Deferral(Reason.ownership, "deployment fingerprint drifted")
    except UpdateError as exc:
        raise Deferral(Reason.io, "recheck command failed") from exc


# ---- projection, provider round trip and commit verification (Task 6) ----

def _map_strings(obj, fn) -> Any:
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, dict):
        return {k: _map_strings(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_map_strings(v, fn) for v in obj]
    return obj


def _escape(obj):
    return _map_strings(obj, lambda s: s.replace("$", "$$"))


def _unescape(obj):
    return _map_strings(obj, lambda s: s.replace("$$", "$"))


_NAMESPACE_KEYS = ("pod", "network_mode", "links", "volumes_from")


def to_runtime_literal(svc: dict, inspect_doc: dict) -> dict:
    running = _env_dict((inspect_doc.get("Config") or {}).get("Env"))
    forms: set[str] = set()
    for key, value in _env_dict(svc.get("environment")).items():
        actual = running.get(key)
        if actual is None or (value != actual and value != actual.replace("$", "$$")):
            raise Deferral(Reason.ownership, f"effective environment {key} differs from the running container")
        if "$" in actual:
            forms.add("plain" if value == actual else "escaped")
    if len(forms) > 1:
        raise Deferral(Reason.input, "mixed dollar escaping in provider output")
    if forms == {"escaped"}:
        return _unescape(copy.deepcopy(svc))
    if not forms and any("$" in s for s in _strings(svc)):
        raise Deferral(Reason.input, "cannot determine dollar escaping form")
    return copy.deepcopy(svc)


def _core(svc: dict) -> dict:
    return {k: v for k, v in svc.items() if k not in ("image", "depends_on", "networks")}


def projection_matches(baseline_rt: dict, projected_rt: dict) -> bool:
    return _core(baseline_rt) == _core(projected_rt)


def _resolved_networks(svc, effective, project) -> dict[str, str]:
    """compose network key -> real network name for the networks this service attaches to."""
    declared = effective.get("networks") or {}
    keys = list(svc["networks"]) if svc.get("networks") else ["default"]
    return {k: (declared.get(k) or {}).get("name") or f"{project}_{k}" for k in keys}


def _mounts(svc, effective, project, inspect_doc) -> dict[str, dict]:
    """Validate every mount against the inspected container; return the external named volumes to declare."""
    mounts = inspect_doc.get("Mounts") or []
    vols = {m["Name"] for m in mounts if m.get("Type") == "volume"}
    binds = {m.get("Source") for m in mounts if m.get("Type") == "bind"}
    out: dict[str, dict] = {}
    for entry in svc.get("volumes") or []:
        source, kind = (entry.split(":", 1)[0], None) if isinstance(entry, str) else \
            (entry.get("source", ""), entry.get("type"))
        if kind == "bind" or source.startswith(("/", ".", "~")):
            if not source.startswith("/"):
                raise Deferral(Reason.input, "relative or home-relative bind source cannot be projected")
            if source not in binds:
                raise Deferral(Reason.ownership, "bind source differs from the running container")
            continue
        real = ((effective.get("volumes") or {}).get(source) or {}).get("name") or f"{project}_{source}"
        if real not in vols:
            raise Deferral(Reason.ownership, "named volume differs from the running container")
        out[source] = {"external": True, "name": real}
    return out


def build_projection(effective, service, inspect_doc, image, project):
    if service not in (effective.get("services") or {}):
        raise Deferral(Reason.ownership, f"effective compose has no service {service}")
    base = to_runtime_literal(copy.deepcopy(effective["services"][service]), inspect_doc)
    for key in _NAMESPACE_KEYS:
        if key in base:
            raise Deferral(Reason.input, f"unsupported {key} on {service}")
    inspected_nets = (inspect_doc.get("NetworkSettings") or {}).get("Networks") or {}
    key_to_name = _resolved_networks(base, effective, project)
    if set(key_to_name.values()) != set(inspected_nets):
        raise Deferral(Reason.ownership, "network attachments differ from the running container")
    volumes = _mounts(base, effective, project, inspect_doc)
    svc = copy.deepcopy(base)
    svc["image"] = image
    svc.pop("depends_on", None)
    aliases = {key_to_name[k]: (v or {}) for k, v in (base.get("networks") or {}).items()} \
        if isinstance(base.get("networks"), dict) else {}
    svc["networks"] = {name: aliases.get(name, {}) for name in sorted(inspected_nets)}
    return {"services": {service: _escape(svc)},
            "networks": {name: {"external": True, "name": name} for name in sorted(inspected_nets)},
            "volumes": volumes}


def verify_projection(effective, service, inspect_doc, image, project, projection_path, provider, run) -> None:
    baseline = to_runtime_literal(copy.deepcopy(effective["services"][service]), inspect_doc)
    again = effective_config(provider, project, projection_path, run)  # the provider's own reading of the file
    if list(again.get("services") or {}) != [service]:
        raise Deferral(Reason.ownership, "provider resolves a different service set")
    svc = again["services"][service]
    if not isinstance(svc, dict) or svc.get("image") != image or svc.get("depends_on"):
        raise Deferral(Reason.ownership, "provider resolves a different image or still has a dependency")
    if any(key in svc for key in _NAMESPACE_KEYS):
        raise Deferral(Reason.input, "provider output shares a namespace")
    resolved = to_runtime_literal(copy.deepcopy(svc), inspect_doc)
    if not projection_matches(baseline, resolved):
        raise Deferral(Reason.ownership, "provider reads the projection differently from the effective service")
    if set(_resolved_networks(resolved, again, project).values()) != \
            set((inspect_doc.get("NetworkSettings") or {}).get("Networks") or {}):
        raise Deferral(Reason.ownership, "provider attaches different networks")
    _mounts(resolved, again, project, inspect_doc)


def verify_commit(run, out_dir, service, old_id, config_id, timeout, *, expect_no_deps=False,
                  env=os.environ, sleep=time.sleep, clock=time.monotonic,
                  api_ok: Callable[[], bool] | None = None) -> str:
    """Wait for exactly one replacement. Ready = Podman `healthy`, or running with `api_ok()` true (the same rule
    preflight applies to a running target whose exec-based Podman healthcheck cannot succeed on this host)."""
    baseline = Snapshot.from_json(Path(out_dir, "snapshot.json").read_text())
    targets = json.loads(Path(out_dir, "target.json").read_text())
    target = targets[0] if targets else None  # None: the target was absent, so there is no prior container to match
    if target is not None:
        project = ((target.get("Config") or {}).get("Labels") or {}).get(LABEL_PROJECT)
        want_nets = sorted(v["NetworkID"] for v in (target["NetworkSettings"]["Networks"]).values())
        want_vols = sorted(m["Name"] for m in target.get("Mounts") or [] if m.get("Type") == "volume")
    else:
        project = json.loads(Path(out_dir, "deployment.json").read_text())["project"]
        want_nets = want_vols = None
    if config_id:
        want_image = config_id
    elif target is not None:
        want_image = canon_image_id(target["Image"])
    else:
        raise UpdateError("an absent target needs the resolved config id")
    deadline, new_id, problem = clock() + timeout, "", "not replaced"
    bounded = with_deadline(run, deadline, clock)  # slow-but-successful commands are discarded too
    while True:
        if clock() >= deadline:  # ruling: checked before sampling so the loop never reports "inventory unavailable"
            raise UpdateError(    # for a budget that is merely exhausted
                f"new {service} {problem} within {timeout}s; old={old_id[:12]} new={new_id[:12] or 'none'}")
        try:
            snap = take_snapshot(bounded, env)
        except Deferral as exc:
            raise UpdateError(
                f"inventory unavailable during verification: {exc.reason.value}; old={old_id[:12]}") from exc
        cands = [c for c, d in snap.docs.items()
                 if ((d.get("Config") or {}).get("Labels") or {}).get(LABEL_SERVICE) == service
                 and ((d.get("Config") or {}).get("Labels") or {}).get(LABEL_PROJECT) == project
                 and c != old_id]
        changed = sorted(c[:12] for c, h in baseline.containers.items() if c != old_id and snap.containers.get(c) != h)
        if changed:
            raise UpdateError(f"sibling containers changed during replacement: {', '.join(changed)}; old={old_id[:12]}")
        if len(cands) == 1:
            new_id, doc = cands[0], snap.docs[cands[0]]
            nets = sorted(v["NetworkID"] for v in doc["NetworkSettings"]["Networks"].values())
            vols = sorted(m["Name"] for m in doc.get("Mounts") or [] if m.get("Type") == "volume")
            if canon_image_id(doc["Image"]) != want_image:
                raise UpdateError(f"new {service} runs an unexpected image; old={old_id[:12]} new={new_id[:12]}")
            if want_nets is not None and (nets != want_nets or vols != want_vols):
                raise UpdateError(f"new {service} resource attachments differ; old={old_id[:12]} new={new_id[:12]}")
            if expect_no_deps and doc["Dependencies"]:
                raise UpdateError(f"new {service} still has native dependencies; old={old_id[:12]} new={new_id[:12]}")
            state = doc.get("State") or {}
            if (state.get("Health") or {}).get("Status") == "healthy" or \
                    (state.get("Running") is True and api_ok is not None and api_ok()):
                if clock() >= deadline:
                    raise UpdateError(
                        f"new {service} became healthy only after the {timeout}s budget; old={old_id[:12]} new={new_id[:12]}")
                return new_id
            problem = "not healthy"
        elif len(cands) > 1:
            raise UpdateError(f"more than one replacement {service} container; old={old_id[:12]}")
        if clock() >= deadline:
            raise UpdateError(
                f"new {service} {problem} within {timeout}s; old={old_id[:12]} new={new_id[:12] or 'none'}")
        sleep(1)
