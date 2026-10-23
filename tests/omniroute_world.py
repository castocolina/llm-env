import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

import yaml
from omniroute_fakes import (
    IMG,
    NEW,
    OMNI,
    PASSWORD,
    PROJECT,
    RS,
    free_port,
    inspect_doc,
    omni,
    profile_entry,
)

REPO = pathlib.Path(__file__).resolve().parents[1]
STAGED = "sha256:" + "2" * 64


def manifest(config_digest):
    return json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                       "config": {"digest": config_digest, "mediaType": "application/vnd.oci.image.config.v1+json",
                                  "size": 1},
                       "layers": []})


def make_repo_copy(tmp_path, profile_entries):
    """A temp copy of the repo tree whose profile list is `profile_entries` (shared with the Task 8 proofs; this is the
    only way a profile is supplied -- there is no production-reachable injection seam)."""
    dst = tmp_path / "repo"
    if not dst.exists():  # a second call in the same test only rewrites the profile list
        shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns(
            ".git", ".venv", "tmp", ".docs", "__pycache__", ".pytest_cache", "node_modules", ".codegraph"),
            ignore_dangling_symlinks=True)
    (dst / "pylib/omniroute_activity_profiles.json").write_text(json.dumps({"schema": 1, "profiles": profile_entries}))
    return dst


class World:
    def __init__(self, tmp_path, *, staged=STAGED, staged_inspect_id=None, dependent=False, drift_after_observer=False,
                 up_rc=0, stop_sleep=0, prior_tag_sleep=0, env=None, busy=False):
        self.tmp, self.env_extra = tmp_path, env or {}
        self.port = free_port()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.calls, self.worldfile = self.bin / "stub-calls.jsonl", self.bin / "stub-world.json"
        self.calls.touch()
        self.home = tmp_path / "home"
        cfgdir = self.home / ".config/llm-env"
        cfgdir.mkdir(parents=True)
        (cfgdir / "models.yml").write_text(yaml.safe_dump({"version": 1, "omniroute": {
            "port": self.port, "image": "docker.io/diegosouzapw/omniroute:latest", "initial_password": PASSWORD}}))
        (cfgdir / "models.yml").chmod(0o600)
        (cfgdir / "docker-compose.yml").write_text("services:\n  omniroute: {}\n")
        self.state = tmp_path / "omni-state.json"
        self.state.write_text(json.dumps({"active": 1 if busy else 0, "cache_ms": 1000}))
        stub = REPO / "tests/fixtures/stub_tool.py"
        (tmp_path / "g").mkdir(exist_ok=True)
        (tmp_path / "r").mkdir(exist_ok=True)
        for tool in ("podman", "skopeo", "podman-compose", "journalctl"):
            (self.bin / tool).symlink_to(stub)
        self.repo = make_repo_copy(tmp_path, [profile_entry(config_id="sha256:" + IMG)])
        self.immutable_ref = "docker.io/diegosouzapw/omniroute@sha256:" + \
            hashlib.sha256(manifest(staged).encode()).hexdigest()
        inspect_id = (staged_inspect_id or staged).removeprefix("sha256:")
        old = omni(port=self.port, env=[f"INITIAL_PASSWORD={PASSWORD}", f"PORT={self.port}",
                                        "REQUIRE_API_KEY=true", "NODE_ENV=production"])
        new = inspect_doc(NEW, name="omniroute", port=self.port, image=staged.removeprefix("sha256:"),
                          env=old["Config"]["Env"])
        rs = inspect_doc(RS, name="remote-setup", deps=[OMNI])
        eff = {"services": {"omniroute": {"image": "docker.io/diegosouzapw/omniroute:latest",
                                          "container_name": "omniroute",
                                          "ports": [f"0.0.0.0:{self.port}:{self.port}"], "stop_grace_period": "40s",
                                          "volumes": ["omniroute-data:/app/data"],
                                          "environment": {"PORT": str(self.port), "INITIAL_PASSWORD": PASSWORD,
                                                          "REQUIRE_API_KEY": "true", "NODE_ENV": "production"}}},
               "networks": {"default": {}}, "volumes": {"omniroute-data": {}}}
        old["Mounts"] = [{"Type": "volume", "Name": f"{PROJECT}_omniroute-data"}]
        new["Mounts"] = old["Mounts"]
        start_ids = [OMNI] + ([RS] if dependent else [])

        def ps(ids):
            return json.dumps([{"Id": i} for i in ids])

        T = "tool"
        rules = [
            {T: "podman", "prefix": ["info"],
             "stdout": json.dumps({"store": {"graphRoot": str(tmp_path / "g"), "runRoot": str(tmp_path / "r")},
                                   "host": {"security": {"rootless": True}, "serviceIsRemote": False}})},
            {T: "podman", "prefix": ["ps", "--all", "--filter"], "stdout": ""},
            {T: "podman", "prefix": ["ps"], "phase": ["start"], "stdout": ps(start_ids)},
            {T: "podman", "prefix": ["ps"], "phase": ["post-observer"],
             "stdout": ps([OMNI, RS] if drift_after_observer else start_ids)},
            {T: "podman", "prefix": ["ps"], "phase": ["post-rm"], "stdout": ps([])},
            {T: "podman", "prefix": ["ps"], "phase": ["post-commit"], "stdout": ps([NEW])},
            {T: "podman", "prefix": ["container", "inspect"], "last": OMNI, "stdout": json.dumps([old])},
            {T: "podman", "prefix": ["container", "inspect"], "last": RS, "stdout": json.dumps([rs])},
            {T: "podman", "prefix": ["container", "inspect"], "last": NEW, "stdout": json.dumps([new])},
            {T: "podman", "prefix": ["image", "inspect", "--format", "json", IMG],
             "stdout": json.dumps([{"Id": IMG, "Os": "linux", "Architecture": "amd64", "Variant": ""}])},
            {T: "podman", "prefix": ["image", "inspect", "--format", "json"],
             "stdout": json.dumps([{"Id": inspect_id, "Os": "linux", "Architecture": "amd64", "Variant": ""}])},
            {T: "podman", "prefix": ["image", "inspect", "--format", "{{.Id}}"], "sleep": prior_tag_sleep,
             "stdout": IMG + "\n"},
            {T: "podman", "prefix": ["stop"], "sleep": stop_sleep},
            {T: "podman", "prefix": ["rm"], "set_phase": "post-rm"},
            {T: "skopeo", "prefix": ["inspect", "--raw"], "stdout": manifest(staged)},
            {T: "podman-compose", "prefix": ["--version"], "stdout": "podman-compose 1.6.0\n"},
            {T: "podman-compose", "last": "config", "arg_endswith": "projection.yml", "echo_file_after": "-f"},
            {T: "podman-compose", "last": "config", "stdout": yaml.safe_dump(eff)},
            {T: "podman-compose", "has": ["up"], "rc": up_rc, "set_phase": "post-commit" if up_rc == 0 else "start"},
            {T: "journalctl", "has": ["--follow"], "sleep": 300, "on_term_set_phase": "post-observer",
             # Ruling: the plan's follow rule printed nothing, so the barrier cursor could never be seen and
             # no sample could ever be `feed`-clean; a real follower streams records, so echo the marker line.
             "stdout": json.dumps({"__CURSOR": "s1", "_BOOT_ID": "b1",
                                   "MESSAGE": json.dumps({"method": "GET", "path": "/api/monitoring/health"})}) + "\n"},
            {T: "journalctl",
             "stdout": json.dumps({"__CURSOR": "s1", "_BOOT_ID": "b1",
                                   "MESSAGE": json.dumps({"method": "GET", "path": "/api/monitoring/health"})}) + "\n"},
        ]
        self.worldfile.write_text(json.dumps({"phase": "start", "rules": rules}))
        self.server = subprocess.Popen([sys.executable, str(REPO / "tests/fixtures/fake_omniroute.py"),
                                        "--port", str(self.port), "--state", str(self.state)],
                                       stdout=subprocess.DEVNULL)

    def env(self):
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("COMPOSE_", "PODMAN_COMPOSE_", "CONTAINER_", "STUB_"))}
        return {**base, "HOME": str(self.home), "PATH": f"{self.bin}:{base['PATH']}",
                "UV_PROJECT_ENVIRONMENT": str(REPO / ".venv"),
                "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(pathlib.Path.home() / ".cache/uv")),
                **self.env_extra}

    def run(self, **env):
        return subprocess.run(["bash", str(self.repo / "scripts/update-omniroute.sh")],
                              env={**self.env(), **env}, capture_output=True, text=True, timeout=600, check=False)

    def popen(self, **env):
        return subprocess.Popen(["bash", str(self.repo / "scripts/update-omniroute.sh")],
                                env={**self.env(), **env}, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)

    def journal(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def mutations(self):
        """Mutating invocations in order: (tool, verb, rest...)."""
        out = []
        for c in self.journal():
            a = c["argv"]
            if c["tool"] == "podman" and a[:1] in (["pull"], ["tag"], ["stop"], ["rm"]):
                out.append((c["tool"], *a))
            if c["tool"] == "podman-compose" and "up" in a:
                out.append((c["tool"], *a))
        return out

    def close(self):
        self.server.kill()
        self.server.wait()
