import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))      # same first lines as tests/test_remote_setup.py

from pylib.omniroute_update import (
    Deferral,
    Reason,
    UpdateError,
    canon_image_id,
    parse_reference,
    resolve_image,
    verify_staged,
)


def sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()

CONFIG = "sha256:" + "c" * 64
PLAT = ("linux", "amd64", "")

def make_runner(raw_by_ref: dict[str, bytes]):
    def run(argv, timeout=None):
        # argv: ["skopeo", "inspect", "--raw", "docker://REF"]
        assert argv[:3] == ["skopeo", "inspect", "--raw"]
        return raw_by_ref[argv[3].removeprefix("docker://")]
    return run

def single_manifest() -> bytes:
    return json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": CONFIG, "mediaType": "application/vnd.oci.image.config.v1+json", "size": 1},
        "layers": [],
    }).encode()

def test_single_manifest_resolves_to_same_fields_as_index():
    m = single_manifest()
    ident = resolve_image("docker.io/x/y:latest", PLAT, make_runner({"docker.io/x/y:latest": m}))
    assert ident.kind == "single"
    assert ident.manifest_digest == sha(m) == ident.source_digest
    assert ident.immutable_ref == f"docker.io/x/y@{sha(m)}"
    assert ident.config_id == CONFIG

def test_index_selects_matching_platform_and_ignores_attestations():
    m = single_manifest()
    index = json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {"digest": sha(b"att"), "mediaType": "application/vnd.oci.image.manifest.v1+json",
             "platform": {"os": "unknown", "architecture": "unknown"}},
            {"digest": sha(m), "mediaType": "application/vnd.oci.image.manifest.v1+json",
             "platform": {"os": "linux", "architecture": "amd64"}},
        ],
    }).encode()
    ident = resolve_image("docker.io/x/y:latest", PLAT, make_runner({
        "docker.io/x/y:latest": index, f"docker.io/x/y@{sha(m)}": m}))
    assert ident.kind == "index"
    assert ident.source_digest == sha(index) and ident.manifest_digest == sha(m)
    assert ident.config_id == CONFIG

def test_index_without_matching_platform_is_an_error():
    index = json.dumps({"schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{"digest": sha(b"a"), "mediaType": "application/vnd.oci.image.manifest.v1+json",
                       "platform": {"os": "linux", "architecture": "arm64"}}]}).encode()
    with pytest.raises(UpdateError, match="no runnable manifest"):
        resolve_image("docker.io/x/y:latest", PLAT, make_runner({"docker.io/x/y:latest": index}))

def test_malformed_digests_are_rejected():
    for bad in ("sha256:abc", "sha256:" + "G" * 64, "md5:" + "c" * 32):
        doc = json.loads(single_manifest())
        doc["config"]["digest"] = bad
        with pytest.raises(UpdateError):
            resolve_image("docker.io/x/y:latest", PLAT,
                          make_runner({"docker.io/x/y:latest": json.dumps(doc).encode()}))
    index = json.dumps({"schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{"digest": "sha256:short", "mediaType": "application/vnd.oci.image.manifest.v1+json",
                       "platform": {"os": "linux", "architecture": "amd64"}}]}).encode()
    with pytest.raises(UpdateError, match="descriptor digest"):
        resolve_image("docker.io/x/y:latest", PLAT, make_runner({"docker.io/x/y:latest": index}))

def test_verify_staged_checks_config_id_and_platform():
    ident = resolve_image("docker.io/x/y:latest", PLAT,
                          make_runner({"docker.io/x/y:latest": single_manifest()}))
    good = {"Id": CONFIG.removeprefix("sha256:"), "Os": "linux", "Architecture": "amd64", "Variant": ""}
    verify_staged(ident, good)                                     # no raise
    with pytest.raises(UpdateError, match="config id"):
        verify_staged(ident, {**good, "Id": "d" * 64})
    with pytest.raises(UpdateError, match="platform"):
        verify_staged(ident, {**good, "Architecture": "arm64"})

def test_schema1_and_missing_config_are_rejected():
    bad = json.dumps({"schemaVersion": 1}).encode()
    with pytest.raises(UpdateError):
        resolve_image("docker.io/x/y:latest", PLAT, make_runner({"docker.io/x/y:latest": bad}))
    nocfg = json.dumps({"schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json"}).encode()
    with pytest.raises(UpdateError):
        resolve_image("docker.io/x/y:latest", PLAT, make_runner({"docker.io/x/y:latest": nocfg}))

def test_parse_reference_normalizes_aliases_and_preserves_digest_pins():
    d = "sha256:" + "a" * 64
    assert parse_reference("diegosouzapw/omniroute") == ("docker.io/diegosouzapw/omniroute", "latest", "")
    assert parse_reference("redis") == ("docker.io/library/redis", "latest", "")
    assert parse_reference("127.0.0.1:5000/a/b:t") == ("127.0.0.1:5000/a/b", "t", "")
    assert parse_reference("localhost/x") == ("localhost/x", "latest", "")
    assert parse_reference(f"docker.io/x/y@{d}") == ("docker.io/x/y", "", d)           # no default tag next to a digest
    assert parse_reference(f"x/y:t@{d}") == ("docker.io/x/y", "t", d)
    assert parse_reference(f"x/y@{d}") != parse_reference("x/y@sha256:" + "b" * 64)    # different pins never compare equal

def test_canon_image_id_accepts_bare_and_prefixed_hex_only():
    assert canon_image_id("c" * 64) == canon_image_id("sha256:" + "c" * 64) == CONFIG
    for bad in ("", "c" * 63, "sha256:" + "G" * 64, "md5:" + "c" * 32, "C" * 64):
        with pytest.raises(UpdateError):
            canon_image_id(bad)

def test_pinned_index_reference_defers():
    m = single_manifest()
    index = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{"digest": sha(m), "mediaType": "application/vnd.oci.image.manifest.v1+json",
                       "platform": {"os": "linux", "architecture": "amd64"}}]}).encode()
    ref = f"docker.io/x/y@{sha(index)}"
    with pytest.raises(Deferral) as exc:
        resolve_image(ref, PLAT, make_runner({ref: index, f"docker.io/x/y@{sha(m)}": m}))
    assert exc.value.reason is Reason.input

def test_pinned_single_manifest_resolves():
    m = single_manifest()
    ref = f"docker.io/x/y@{sha(m)}"
    ident = resolve_image(ref, PLAT, make_runner({ref: m}))
    assert ident.manifest_digest == sha(m) and ident.kind == "single"

import argparse
import time

from pylib.omniroute_cli import cmd_resolve
from pylib.omniroute_update import (
    backend_env,
    default_runner,
    scrubbed_runner,
    with_deadline,
)


def test_with_deadline_refuses_expired_budget():
    run = with_deadline(lambda argv, timeout=None: b"x", time.monotonic() - 1)
    with pytest.raises(UpdateError, match="deadline"):
        run(["true"])

def test_with_deadline_rejects_results_completed_after_the_deadline():
    now = {"t": 0.0}
    def slow(argv, timeout=None):
        now["t"] = 10.0                                     # the command "ran" past the deadline and then succeeded
        return b"ok"
    run = with_deadline(slow, 5.0, clock=lambda: now["t"])
    with pytest.raises(UpdateError, match="deadline"):
        run(["x"])

def test_default_runner_converts_failure_and_timeout():
    with pytest.raises(UpdateError):
        default_runner(["false"])
    with pytest.raises(UpdateError):
        default_runner(["sleep", "5"], timeout=0.1)

def test_backend_env_keeps_only_the_allowlist():
    env = {"PATH": "/usr/bin:/bin", "HOME": "/h", "XDG_RUNTIME_DIR": "/run/u", "CONTAINERS_CONF": "/c", "CONTAINERS_STORAGE_CONF": "",
           "HTTP_PROXY": "p", "CONTAINER_HOST": "ssh://x", "COMPOSE_FILE": "f"}
    assert backend_env(env) == {"PATH": "/usr/bin:/bin", "HOME": "/h", "XDG_RUNTIME_DIR": "/run/u", "CONTAINERS_CONF": "/c"}

def test_backend_env_requires_path_and_home():
    with pytest.raises(UpdateError):
        backend_env({"PATH": "/bin"})

def test_scrubbed_runner_children_see_only_the_allowlist():
    run = scrubbed_runner({"PATH": "/usr/bin:/bin", "HOME": "/h", "SECRET": "x"})
    out = run(["env"]).decode()
    assert "HOME=/h" in out and "SECRET" not in out

def test_omniroute_update_resolve_failure_is_sanitized(tmp_path, capsys):
    def failing(argv, timeout=None):
        raise UpdateError("skopeo failed: CalledProcessError")
    args = argparse.Namespace(reference="docker.io/x/y:latest", os="linux", arch="amd64",
                              variant="", json=str(tmp_path / "r.json"))
    assert cmd_resolve(args, run=failing) == 1
    assert "skopeo failed" in capsys.readouterr().err
    assert not (tmp_path / "r.json").exists()

def test_omniroute_update_resolve_writes_private_json(tmp_path, capsys):
    m = single_manifest()
    args = argparse.Namespace(reference="docker.io/x/y:latest", os="linux", arch="amd64",
                              variant="", json=str(tmp_path / "r.json"))
    assert cmd_resolve(args, run=make_runner({"docker.io/x/y:latest": m})) == 0
    assert json.loads((tmp_path / "r.json").read_text())["config_id"] == CONFIG
    assert (tmp_path / "r.json").stat().st_mode & 0o777 == 0o600
    assert f"config_id={CONFIG}" in capsys.readouterr().out

def test_omniroute_update_resolve_defers_on_a_pinned_index(tmp_path, capsys):
    m = single_manifest()
    index = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{"digest": sha(m), "mediaType": "application/vnd.oci.image.manifest.v1+json",
                       "platform": {"os": "linux", "architecture": "amd64"}}]}).encode()
    ref = f"docker.io/x/y@{sha(index)}"
    args = argparse.Namespace(reference=ref, os="linux", arch="amd64", variant="", json=str(tmp_path / "r.json"))
    assert cmd_resolve(args, run=make_runner({ref: index})) == 75
    assert capsys.readouterr().out == "deferred: input\n"

from omniroute_fakes import (
    IMG,
    OMNI,
    PASSWORD,
    PROJECT,
    RS,
    SIB,
    fake_backend,
    inspect_doc,
    omni,
    preflight_world,
    with_dependent,
    with_port_changed,
    without_sibling,
)

from pylib.omniroute_update import (
    Snapshot,
    assert_unchanged,
    fingerprint,
    fingerprint_parts,
    incoming,
    preflight,
    recheck,
    take_snapshot,
    write_preflight_artifacts,
)


@pytest.fixture(autouse=True)
def _no_remote_podman_in_the_ambient_environment(monkeypatch):
    for name in ("CONTAINER_HOST", "CONTAINER_CONNECTION"):
        monkeypatch.delenv(name, raising=False)

def test_top_level_dependencies_detected_even_for_stopped_cross_project_container():
    run = fake_backend({OMNI: omni(), RS: inspect_doc(RS, deps=[OMNI], state="exited", project="other")})
    assert incoming(take_snapshot(run), OMNI) == [RS]

def test_missing_dependencies_key_is_schema_deferral():
    doc = inspect_doc(RS, deps=[OMNI]); del doc["Dependencies"]
    with pytest.raises(Deferral) as e:
        take_snapshot(fake_backend({OMNI: omni(), RS: doc}))
    assert e.value.reason is Reason.schema

@pytest.mark.parametrize("bad", ["short", None, 7, "G" * 64])
def test_malformed_dependency_id_defers(bad):
    doc = inspect_doc(RS); doc["Dependencies"] = [bad]
    with pytest.raises(Deferral) as e:
        take_snapshot(fake_backend({OMNI: omni(), RS: doc}))
    assert e.value.reason is Reason.schema

def test_uninspectable_container_defers_inventory():
    with pytest.raises(Deferral) as e:
        take_snapshot(fake_backend({OMNI: omni(), RS: UpdateError("inspect failed")}))
    assert e.value.reason is Reason.io

def test_non_rootless_or_remote_backend_defers():
    with pytest.raises(Deferral) as e:
        take_snapshot(fake_backend({OMNI: omni()}, rootless=False))
    assert e.value.reason is Reason.ownership
    with pytest.raises(Deferral) as e:
        take_snapshot(fake_backend({OMNI: omni()}, remote=True))
    assert e.value.reason is Reason.ownership
    for var in ("CONTAINER_HOST", "CONTAINER_CONNECTION"):
        with pytest.raises(Deferral) as e:
            take_snapshot(fake_backend({OMNI: omni()}), {var: "ssh://x"})
        assert e.value.reason is Reason.input

def test_assert_unchanged_detects_new_dependent_added_container_removed_and_config_change():
    base = take_snapshot(fake_backend({OMNI: omni(), SIB: inspect_doc(SIB, name="sib")}))
    assert_unchanged(base, take_snapshot(fake_backend({OMNI: omni(), SIB: inspect_doc(SIB, name="sib")})))
    for mutated in (with_dependent(), without_sibling(), with_port_changed(SIB)):
        with pytest.raises(Deferral) as e:
            assert_unchanged(base, take_snapshot(mutated))
        assert e.value.reason is Reason.ownership

def _mutate_config(**changes):
    doc = omni()
    doc["Config"].update(changes)
    return doc

@pytest.mark.parametrize("changed", [
    _mutate_config(Env=["INITIAL_PASSWORD=other", "REQUIRE_API_KEY=true"]),
    _mutate_config(Entrypoint=["node", "other.js"]),
    _mutate_config(Cmd=["--other"]),
    _mutate_config(WorkingDir="/elsewhere"),
    _mutate_config(Healthcheck={"Test": ["CMD", "false"], "Interval": 1}),
])
def test_every_create_configuration_field_is_part_of_the_container_hash(changed):
    base = take_snapshot(fake_backend({OMNI: omni(), SIB: inspect_doc(SIB, name="sib")}))
    after = take_snapshot(fake_backend({OMNI: changed, SIB: inspect_doc(SIB, name="sib")}))
    with pytest.raises(Deferral) as e:
        assert_unchanged(base, after)
    assert e.value.reason is Reason.ownership

def test_snapshot_json_roundtrip_drops_raw_docs():
    snap = take_snapshot(fake_backend({OMNI: omni()}))
    text = snap.to_json()
    assert PASSWORD not in text
    assert Snapshot.from_json(text) == snap

COMPOSE = ('services:\n  omniroute:\n    image: docker.io/diegosouzapw/omniroute:latest\n'
           '    ports: ["0.0.0.0:${PORT}:20128"]\n')
EFFECTIVE = {"services": {"omniroute": {"image": "docker.io/diegosouzapw/omniroute:latest",
                                        "container_name": "omniroute", "ports": ["0.0.0.0:20128:20128"],
                                        "deploy": {"resources": {"limits": {"memory": "2g"}}}}},
             "networks": {"default": {}}, "volumes": {"omniroute-data": {}}}

def fp_world(tmp_path, *, config="a: 1\n", compose=COMPOSE, provider=b"#!/bin/sh\n", effective=None,
             env=None, containers=None):
    cfg = tmp_path / "models.yml"; cfg.write_text(config)
    comp = tmp_path / "docker-compose.yml"; comp.write_text(compose)
    prov = tmp_path / "podman-compose"; prov.write_bytes(provider); prov.chmod(0o755)
    run = fake_backend(containers or {OMNI: omni()}, extra=[((str(prov), "--version"), b"podman-compose 1.6.0\n")])
    snap = take_snapshot(run)
    parts = fingerprint_parts(str(cfg), str(comp), str(prov), effective or EFFECTIVE,
                              env or {"PORT": "20128"}, snap, run)
    return fingerprint(parts), parts

def test_fingerprint_is_stable(tmp_path):
    assert fp_world(tmp_path)[0] == fp_world(tmp_path)[0]

def _mut(**kw):
    return kw

@pytest.mark.parametrize("mutate", [
    _mut(config="a: 2\n"),
    _mut(compose=COMPOSE + "# changed\n"),
    _mut(env={"PORT": "20129"}),
    _mut(provider=b"#!/bin/sh\n# other\n"),
    _mut(containers={OMNI: omni(net="f" * 64)}),
    _mut(containers={OMNI: omni(), SIB: inspect_doc(SIB, name="sib")}),
    _mut(effective={**EFFECTIVE, "volumes": {"other-data": {}}}),
    _mut(effective={**EFFECTIVE, "services": {"omniroute": {**EFFECTIVE["services"]["omniroute"],
                                                             "ports": ["0.0.0.0:20129:20128"]}}}),
    _mut(effective={**EFFECTIVE, "services": {"omniroute": {**EFFECTIVE["services"]["omniroute"],
                                                             "deploy": {"resources": {"limits": {"memory": "1g"}}}}}}),
])
def test_fingerprint_changes_for_every_input_class(tmp_path, mutate):
    assert fp_world(tmp_path)[0] != fp_world(tmp_path, **mutate)[0]

def test_fingerprint_output_never_contains_secret_bytes(tmp_path):
    digest, parts = fp_world(tmp_path, effective={**EFFECTIVE, "x-secret": PASSWORD})
    assert PASSWORD not in digest and all(PASSWORD not in k for k in parts)
    assert digest.startswith("sha256:") and len(digest) == 71

@pytest.mark.parametrize("key", ["include", "extends", "env_file", "secrets", "configs", "build",
                                 "network_mode", "pod", "volumes_from", "links"])
def test_unsupported_compose_inputs_defer_input(tmp_path, key):
    svc = {**EFFECTIVE["services"]["omniroute"], key: "x"}
    with pytest.raises(Deferral) as e:
        fp_world(tmp_path, effective={**EFFECTIVE, "services": {"omniroute": svc}})
    assert e.value.reason is Reason.input

def test_compose_env_variables_defer_input(tmp_path):
    with pytest.raises(Deferral) as e:
        fp_world(tmp_path, env={"PORT": "1", "COMPOSE_PROFILES": "x"})
    assert e.value.reason is Reason.input

def test_preflight_happy_path_returns_deployment_without_secrets(tmp_path):
    cfg, cp, comp, prov, env, run = preflight_world(tmp_path)
    dep, _snap, fp, _eff = preflight(cfg, cp, comp, env, run, provider=prov)
    assert dep.container_id == OMNI and dep.project == PROJECT and dep.port == 20128
    assert dep.stop_seconds == 40 and dep.pinned is False and (dep.os, dep.architecture) == ("linux", "amd64")
    assert dep.image_id == "sha256:" + IMG
    assert PASSWORD not in repr(dep) and fp.startswith("sha256:")

PIN_A, PIN_B = "docker.io/diegosouzapw/omniroute@sha256:" + "a" * 64, "docker.io/diegosouzapw/omniroute@sha256:" + "b" * 64

@pytest.mark.parametrize("name,kw,reason", [
    ("image-mismatch", {"omni_kw": {"image_name": "docker.io/other/img:latest"}}, Reason.ownership),
    ("docker-hub-alias-ok", {"image": "diegosouzapw/omniroute:latest"}, None),
    ("digest-form-running-reference-ok", {"omni_kw": {"image_name": "docker.io/diegosouzapw/omniroute@sha256:" + "9" * 64}}, None),
    ("pinned-config-running-other-digest", {"image": PIN_A, "svc_patch": {"image": PIN_A}, "omni_kw": {"image_name": PIN_B}}, Reason.ownership),
    ("pinned-config-effective-other-digest", {"image": PIN_A, "svc_patch": {"image": PIN_B}, "omni_kw": {"image": PIN_A}}, Reason.ownership),
    ("effective-image-differs-from-config", {"svc_patch": {"image": "docker.io/other/img:latest"}}, Reason.ownership),
    ("effective-port-differs-from-config", {"svc_patch": {"ports": ["0.0.0.0:9999:9999"]}}, Reason.ownership),
    ("effective-password-differs-from-config", {"svc_patch": {"environment": {"INITIAL_PASSWORD": "wrong", "REQUIRE_API_KEY": "true"}}}, Reason.ownership),
    ("effective-require-key-off", {"svc_patch": {"environment": {"INITIAL_PASSWORD": PASSWORD, "REQUIRE_API_KEY": "false"}}}, Reason.ownership),
    ("port-mismatch", {"omni_kw": {"port": 9999}}, Reason.ownership),
    ("password-mismatch", {"omni_kw": {"env": ["INITIAL_PASSWORD=wrong", "REQUIRE_API_KEY=true"]}}, Reason.ownership),
    ("dollar-password-ok", {"password": "pa$s", "omni_kw": {"env": ["INITIAL_PASSWORD=pa$s", "REQUIRE_API_KEY=true"]}}, None),
    ("require-key-off", {"omni_kw": {"env": [f"INITIAL_PASSWORD={PASSWORD}", "REQUIRE_API_KEY=false"]}}, Reason.ownership),
    ("unhealthy", {"omni_kw": {"health": "unhealthy"}}, Reason.ownership),
    ("stopped", {"omni_kw": {"state": "exited"}}, Reason.ownership),
    ("no-project-label", {"omni_kw": {"project": ""}}, Reason.ownership),
    ("incoming-dependent", {"extra_containers": {RS: inspect_doc(RS, name="remote-setup", deps=[OMNI])}}, Reason.ownership),
    ("scale-2", {"svc_patch": {"scale": 2}}, Reason.input),
    ("compose-env", {"env": {"COMPOSE_PROFILES": "x"}}, Reason.input),
])
def test_agreement_checks(tmp_path, name, kw, reason):
    cfg, cp, comp, prov, env, run = preflight_world(tmp_path, **kw)
    if reason is None:
        preflight(cfg, cp, comp, env, run, provider=prov)
        return
    with pytest.raises(Deferral) as e:
        preflight(cfg, cp, comp, env, run, provider=prov)
    assert e.value.reason is reason
    assert PASSWORD not in str(e.value)

def test_recheck_defers_on_new_dependent_and_succeeds_when_unchanged(tmp_path):
    cfg, cp, comp, prov, env, run = preflight_world(tmp_path)
    out = tmp_path / "out"; out.mkdir(mode=0o700)
    dep, snap, fp, eff = preflight(cfg, cp, comp, env, run, provider=prov)
    write_preflight_artifacts(str(out), dep, snap, fp, eff, snap.docs[OMNI])
    recheck(str(out), cp, comp, env, run)
    _, _, _, _, _, run2 = preflight_world(tmp_path, extra_containers={RS: inspect_doc(RS, name="remote-setup", deps=[OMNI])})
    with pytest.raises(Deferral) as e:
        recheck(str(out), cp, comp, env, run2)
    assert e.value.reason is Reason.ownership

def test_recheck_turns_a_command_failure_into_a_deferral_never_an_error(tmp_path):
    cfg, cp, comp, prov, env, run = preflight_world(tmp_path)
    out = tmp_path / "out"; out.mkdir(mode=0o700)
    dep, snap, fp, eff = preflight(cfg, cp, comp, env, run, provider=prov)
    write_preflight_artifacts(str(out), dep, snap, fp, eff, snap.docs[OMNI])
    def failing(argv, timeout=None):
        if argv[0] == prov and "config" in argv:
            raise UpdateError("provider failed")
        return run(argv, timeout=timeout)
    with pytest.raises(Deferral) as e:
        recheck(str(out), cp, comp, env, failing)
    assert e.value.reason is Reason.io


# ---- Task 6: projection, provider round trip, commit verification ----

import yaml
from omniroute_fakes import NET, NEW, OMNI, PASSWORD, RS, SIB, inspect_doc, omni

from pylib.omniroute_update import (
    _unescape,
    build_projection,
    projection_matches,
    verify_commit,
    verify_projection,
    write_private,
)

SVC = {"image": "docker.io/diegosouzapw/omniroute:latest", "container_name": "omniroute",
       "ports": ["0.0.0.0:20128:20128"], "restart": "unless-stopped", "stop_grace_period": "40s",
       "environment": {"PORT": "20128", "INITIAL_PASSWORD": "pa$$s", "REQUIRE_API_KEY": "true"},
       "volumes": ["omniroute-data:/app/data"], "healthcheck": {"test": ["CMD", "true"]},
       "depends_on": {"llm-server": {"condition": "service_healthy"}}}
EFF = {"services": {"omniroute": SVC}, "networks": {"default": {}}, "volumes": {"omniroute-data": {}}}
IMAGE = "docker.io/diegosouzapw/omniroute@sha256:" + "9" * 64


def target(password="pa$s", networks=None):
    d = omni(env=[f"INITIAL_PASSWORD={password}", "PORT=20128", "REQUIRE_API_KEY=true", "NODE_ENV=production"])
    d["Mounts"] = [{"Type": "volume", "Name": "llm-env_omniroute-data"}]
    d["NetworkSettings"]["Networks"] = networks or {"llm-env_default": {"NetworkID": NET}}
    return d


def with_volumes(entries):
    return {**EFF, "services": {"omniroute": {**SVC, "volumes": entries}}}


def test_projection_drops_dependency_escapes_dollars_and_pins_external_resources():
    out = build_projection(EFF, "omniroute", target(), IMAGE, "llm-env")
    svc = out["services"]["omniroute"]
    assert list(out["services"]) == ["omniroute"] and svc["image"] == IMAGE and "depends_on" not in svc
    assert svc["environment"]["INITIAL_PASSWORD"] == "pa$$s"  # runtime pa$s, doubled for the provider
    assert svc["networks"] == {"llm-env_default": {}}
    assert out["networks"] == {"llm-env_default": {"external": True, "name": "llm-env_default"}}
    assert out["volumes"] == {"omniroute-data": {"external": True, "name": "llm-env_omniroute-data"}}
    assert svc["container_name"] == "omniroute" and svc["stop_grace_period"] == "40s" and svc["ports"] == SVC["ports"]


def test_plain_provider_output_form_gives_the_same_projection():
    plain = {**EFF, "services": {"omniroute": {**SVC, "environment": {**SVC["environment"], "INITIAL_PASSWORD": "pa$s"}}}}
    assert build_projection(plain, "omniroute", target(), IMAGE, "llm-env") == \
        build_projection(EFF, "omniroute", target(), IMAGE, "llm-env")


def test_literal_double_dollar_password_round_trips():
    escaped = {**EFF, "services": {"omniroute": {**SVC, "environment": {**SVC["environment"],
                                                                        "INITIAL_PASSWORD": "a$$$$b"}}}}
    out = build_projection(escaped, "omniroute", target(password="a$$b"), IMAGE, "llm-env")
    assert out["services"]["omniroute"]["environment"]["INITIAL_PASSWORD"] == "a$$$$b"


def test_mixed_dollar_forms_defer_input():
    mixed = {**EFF, "services": {"omniroute": {**SVC, "environment": {"INITIAL_PASSWORD": "x$$y", "OTHER": "p$q"}}}}
    d = target()
    d["Config"]["Env"] += ["OTHER=p$q"]
    d["Config"]["Env"][0] = "INITIAL_PASSWORD=x$y"
    with pytest.raises(Deferral) as e:
        build_projection(mixed, "omniroute", d, IMAGE, "llm-env")
    assert e.value.reason is Reason.input


def test_environment_value_differing_from_runtime_defers_ownership():
    with pytest.raises(Deferral) as e:
        build_projection(EFF, "omniroute", target(password="other"), IMAGE, "llm-env")
    assert e.value.reason is Reason.ownership and "other" not in str(e.value)


def test_extra_inspected_network_defers_ownership():
    nets = {"llm-env_default": {"NetworkID": NET}, "llm-env_extra": {"NetworkID": "f" * 64}}
    with pytest.raises(Deferral) as e:
        build_projection(EFF, "omniroute", target(networks=nets), IMAGE, "llm-env")
    assert e.value.reason is Reason.ownership


def test_explicit_network_resolves_through_top_level_name():
    eff = {**EFF, "services": {"omniroute": {**SVC, "networks": ["app"]}},
           "networks": {"app": {"name": "llm-env_default"}}}
    assert build_projection(eff, "omniroute", target(), IMAGE, "llm-env")["services"]["omniroute"]["networks"] == \
        {"llm-env_default": {}}


def test_volume_not_present_in_inspect_defers_ownership():
    d = target()
    d["Mounts"] = [{"Type": "volume", "Name": "someone-else"}]
    with pytest.raises(Deferral) as e:
        build_projection(EFF, "omniroute", d, IMAGE, "llm-env")
    assert e.value.reason is Reason.ownership


@pytest.mark.parametrize("source", ["./data", "../data", "~/data"])
def test_relative_bind_source_defers_input(source):
    d = target()
    d["Mounts"] = [{"Type": "bind", "Source": "/srv/x", "Destination": "/app/data"}]
    with pytest.raises(Deferral) as e:
        build_projection(with_volumes([f"{source}:/app/data"]), "omniroute", d, IMAGE, "llm-env")
    assert e.value.reason is Reason.input


def test_absolute_bind_source_must_equal_the_inspected_mount():
    d = target()
    d["Mounts"] = [{"Type": "bind", "Source": "/srv/omni", "Destination": "/app/data"}]
    out = build_projection(with_volumes(["/srv/omni:/app/data:z"]), "omniroute", d, IMAGE, "llm-env")
    assert out["services"]["omniroute"]["volumes"] == ["/srv/omni:/app/data:z"] and out["volumes"] == {}
    d["Mounts"] = [{"Type": "bind", "Source": "/srv/other", "Destination": "/app/data"}]
    with pytest.raises(Deferral) as e:
        build_projection(with_volumes(["/srv/omni:/app/data:z"]), "omniroute", d, IMAGE, "llm-env")
    assert e.value.reason is Reason.ownership


@pytest.mark.parametrize("key", ["pod", "network_mode", "links", "volumes_from"])
def test_projection_rejects_namespace_sharing(key):
    eff = {**EFF, "services": {"omniroute": {**SVC, key: "x"}}}
    with pytest.raises(Deferral) as e:
        build_projection(eff, "omniroute", target(), IMAGE, "llm-env")
    assert e.value.reason is Reason.input


def test_projection_matches_detects_any_field_drift_except_image_depends_and_networks():
    base = dict(SVC)
    assert projection_matches(base, {**base, "image": "other", "depends_on": {}, "networks": {"x": {}}})
    assert not projection_matches(base, {**base, "ports": ["1:1"]})


def fake_provider(mutate=lambda doc: doc):
    """Answers `<provider> ... -f FILE config` the way podman-compose does: it interpolates the file once ($$ -> $)."""
    def run(argv, timeout=None):
        assert argv[-1] == "config"
        doc = _unescape(json.loads(Path(argv[argv.index("-f") + 1]).read_text()))
        return yaml.safe_dump(mutate(doc)).encode()
    return run


def written_projection(tmp_path):
    path = tmp_path / "projection.yml"
    write_private(str(path), json.dumps(build_projection(EFF, "omniroute", target(), IMAGE, "llm-env")))
    return str(path)


def test_verify_projection_accepts_a_faithful_provider_round_trip(tmp_path):
    verify_projection(EFF, "omniroute", target(), IMAGE, "llm-env", written_projection(tmp_path),
                      "podman-compose", fake_provider())


@pytest.mark.parametrize("mutate", [
    lambda d: d["services"]["omniroute"].update(ports=["0.0.0.0:9999:9999"]),  # semantic drift
    lambda d: d["services"]["omniroute"]["environment"].update(INITIAL_PASSWORD="pas"),  # provider ate a "$"
    lambda d: d["services"]["omniroute"].update(depends_on={"llm-server": {"condition": "service_healthy"}}),
    lambda d: d["services"]["omniroute"].update(image="docker.io/other/img:latest"),
    lambda d: d["services"].update(extra={"image": "x"}),  # extra service
    lambda d: d["services"]["omniroute"]["networks"].update({"llm-env_extra": {}}),  # extra network
    lambda d: d["services"]["omniroute"].update(volumes=["elsewhere:/app/data"]),  # different volume
])
def test_verify_projection_defers_when_the_provider_reads_it_differently(tmp_path, mutate):
    def mutated(doc):
        mutate(doc)
        return doc
    with pytest.raises(Deferral) as e:
        verify_projection(EFF, "omniroute", target(), IMAGE, "llm-env", written_projection(tmp_path),
                          "podman-compose", fake_provider(mutated))
    assert e.value.reason is Reason.ownership and "pa$s" not in str(e.value)


def stepping_backend(*states):
    state = {"i": 0}
    def run(argv, timeout=None):
        if argv[:2] == ["podman", "info"]:
            state["i"] += 1
        current = states[min(max(state["i"] - 1, 0), len(states) - 1)]
        return fake_backend(current)(argv, timeout=timeout)
    return run


def art(tmp_path, containers, target_id=OMNI):
    snap = take_snapshot(fake_backend(containers))
    d = tmp_path / "art"
    d.mkdir(mode=0o700)
    write_private(str(d / "snapshot.json"), snap.to_json())
    write_private(str(d / "target.json"), json.dumps([containers[target_id]]))
    return str(d)


def new_omni(**kw):
    kw.setdefault("image", "2" * 64)
    doc = inspect_doc(NEW, name="omniroute", **kw)
    doc["Mounts"] = omni()["Mounts"]
    return doc


def ticking():
    t = {"now": 0.0}
    def clock():
        return t["now"]
    def sleep(s):
        t["now"] += s
    return clock, sleep


def test_verify_commit_success_waits_for_health(tmp_path):
    sib = {SIB: inspect_doc(SIB, name="sib")}
    d = art(tmp_path, {OMNI: omni(), **sib})
    clock, sleep = ticking()
    run = stepping_backend({NEW: new_omni(health="starting"), **sib}, {NEW: new_omni(), **sib})
    assert verify_commit(run, d, "omniroute", OMNI, "sha256:" + "2" * 64, 30, clock=clock, sleep=sleep) == NEW


def test_verify_commit_fails_on_same_id_wrong_image_or_changed_sibling(tmp_path):
    sib = {SIB: inspect_doc(SIB, name="sib")}
    d = art(tmp_path, {OMNI: omni(), **sib})
    clock, sleep = ticking()
    for states, text in (
        ([{OMNI: omni(), **sib}], "not replaced"),
        ([{NEW: new_omni(image="3" * 64), **sib}], "image"),
        ([{NEW: new_omni(), SIB: inspect_doc(SIB, name="sib", port=9999)}], "sibling"),
    ):
        with pytest.raises(UpdateError, match=text) as e:
            verify_commit(stepping_backend(*states), d, "omniroute", OMNI, "sha256:" + "2" * 64, 3,
                          clock=clock, sleep=sleep)
        assert OMNI[:12] in str(e.value) and PASSWORD not in str(e.value)


def test_verify_commit_times_out_when_never_healthy(tmp_path):
    d = art(tmp_path, {OMNI: omni()})
    clock, sleep = ticking()
    with pytest.raises(UpdateError, match="not healthy within 5s"):
        verify_commit(stepping_backend({NEW: new_omni(health="starting")}), d, "omniroute", OMNI,
                      "sha256:" + "2" * 64, 5, clock=clock, sleep=sleep)


def test_verify_commit_rejects_a_slow_successful_command(tmp_path):
    sib = {SIB: inspect_doc(SIB, name="sib")}
    d = art(tmp_path, {OMNI: omni(), **sib})
    t = {"now": 0.0}
    inner = stepping_backend({NEW: new_omni(), **sib})
    def slow(argv, timeout=None):
        t["now"] += 10.0  # each command "takes" 10 s of fake time and then succeeds
        return inner(argv, timeout=timeout)
    with pytest.raises(UpdateError, match="inventory unavailable"):
        verify_commit(slow, d, "omniroute", OMNI, "sha256:" + "2" * 64, 5,
                      clock=lambda: t["now"], sleep=lambda s: None)


def test_verify_commit_expect_no_deps_and_missing_config_id(tmp_path):
    rs = inspect_doc(RS, name="remote-setup", deps=[OMNI])
    d = art(tmp_path, {OMNI: omni(), RS: rs}, target_id=RS)  # the migration target is remote-setup, not OmniRoute
    clock, sleep = ticking()
    fresh_rs = inspect_doc(NEW, name="remote-setup", deps=[])
    fresh_rs["Mounts"], fresh_rs["Image"] = rs["Mounts"], rs["Image"]
    ok = stepping_backend({OMNI: omni(), NEW: fresh_rs})
    assert verify_commit(ok, d, "remote-setup", RS, None, 5, expect_no_deps=True, clock=clock, sleep=sleep) == NEW
    bad = inspect_doc(NEW, name="remote-setup", deps=[OMNI])
    bad["Mounts"], bad["Image"] = rs["Mounts"], rs["Image"]
    with pytest.raises(UpdateError, match="dependenc"):
        verify_commit(stepping_backend({OMNI: omni(), NEW: bad}), d, "remote-setup", RS, None, 5,
                      expect_no_deps=True, clock=clock, sleep=sleep)


# ---- Task 7: preflight --service remote-setup (generic checks only) ----

def _remote_setup_effective():
    return {"services": {"remote-setup": {"image": "x", "container_name": "remote-setup",
                                          "volumes": ["remote-setup-data:/app/data"]}},
            "networks": {"default": {}}, "volumes": {"remote-setup-data": {}}}


def test_preflight_remote_setup_service_passes_generic_checks_only(tmp_path):
    eff = _remote_setup_effective()
    cfg, cp, comp, prov, env, run = preflight_world(
        tmp_path, effective=eff,
        extra_containers={RS: inspect_doc(RS, name="remote-setup", deps=[OMNI])})
    dep, _snap, _fp, _out = preflight(cfg, cp, comp, env, run, provider=prov, service="remote-setup")
    assert dep.container_id == RS and dep.stop_seconds == 10        # default grace for non-OmniRoute services
    # OmniRoute-only agreement checks are not applied: a wrong dashboard password on RS is irrelevant
    rs_doc = inspect_doc(RS, name="remote-setup", deps=[OMNI], env=["INITIAL_PASSWORD=wrong"])
    cfg2, cp2, comp2, prov2, env2, run2 = preflight_world(
        tmp_path, effective=eff, extra_containers={RS: rs_doc})
    preflight(cfg2, cp2, comp2, env2, run2, provider=prov2, service="remote-setup")


def test_preflight_remote_setup_defers_on_incoming_dependent(tmp_path):
    third = "7" * 64
    cfg, cp, comp, prov, env, run = preflight_world(
        tmp_path, effective=_remote_setup_effective(),
        extra_containers={RS: inspect_doc(RS, name="remote-setup", deps=[OMNI]),
                          third: inspect_doc(third, name="client", deps=[RS])})
    with pytest.raises(Deferral) as e:
        preflight(cfg, cp, comp, env, run, provider=prov, service="remote-setup")
    assert e.value.reason is Reason.ownership
