"""Disposable rootless Podman backend for the opt-in updater proofs. Every call is ownership-checked."""
from __future__ import annotations

import contextlib
import json
import pathlib
import shutil
import sys
import threading
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from omniroute_fakes import free_port

from pylib.omniroute_characterize import BackendUnavailable, IsolatedBackend

REGISTRY_IMAGE = "docker.io/library/registry:2"
BASE_IMAGE = "docker.io/library/python:3.12-slim"
PROJECT = "proofproj"


ProofSkip = BackendUnavailable  # a host prerequisite is missing; the message is the exact command that fixes it


class ProofBackend(IsolatedBackend):
    """The product's isolated backend plus a local registry, fixture image builds and argv-recording shims."""

    project = PROJECT
    prefix = "proof-"

    def __init__(self) -> None:
        super().__init__(REPO / "tmp")
        self.reg_port = free_port()
        self.ref = f"127.0.0.1:{self.reg_port}/proof/omniroute:latest"          # the configured, mutable reference
        (self.root / "shim").mkdir()
        # Ruling: skopeo 1.22.3 on this host ignores CONTAINERS_REGISTRIES_CONF and
        # REGISTRIES_CONFIG_LOOKUP for docker:// transports, but does read
        # $HOME/.config/containers/registries.conf (verified: ping via https fails otherwise).
        # The proof home gets that file; the env var is kept for podman's own use.
        (self.root / "registries.conf").write_text(
            f'[[registry]]\nprefix = "127.0.0.1:{self.reg_port}"\n'
            f'location = "127.0.0.1:{self.reg_port}"\ninsecure = true\n')
        self.home_registries_conf = self.root / "registries.conf"
        self.argv_log = self.root / "argv.log"
        self.config_id: str = ""
        self.omni_port: int = 0
        self.old_id: str = ""
        self.new_id: str = ""
        self.shim: pathlib.Path = self.root / "shim"

    def env(self) -> dict[str, str]:
        return {**super().env(), "CONTAINERS_REGISTRIES_CONF": str(self.root / "registries.conf")}

    # --- guards and fixtures ----------------------------------------------------
    def assert_pristine(self) -> None:
        self.assert_owned()
        assert self.podman("ps", "--all", "--quiet").stdout.strip() == ""
        assert self.podman("image", "list", "--quiet").stdout.strip() == ""

    def start_registry(self) -> None:
        self.podman("run", "-d", "--name", "proof-registry", "-p", f"127.0.0.1:{self.reg_port}:5000", REGISTRY_IMAGE)
        end = time.monotonic() + 30
        while time.monotonic() < end:
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{self.reg_port}/v2/", timeout=2).status == 200:
                    return
            except OSError:
                time.sleep(0.5)
        raise AssertionError("proof registry did not start")

    def build(self, variant: str) -> str:
        """Build the fixture image; returns its config id (`sha256:<hex>`)."""
        ctx = self.root / f"ctx-{variant}"
        ctx.mkdir()
        shutil.copy(REPO / "tests/fixtures/proof/Containerfile", ctx / "Containerfile")
        shutil.copy(REPO / "tests/fixtures/fake_omniroute.py", ctx / "fake_omniroute.py")
        self.podman("build", "--format", "oci", "--build-arg", f"VARIANT={variant}",
                    "-t", f"localhost/proof/omniroute:{variant}", str(ctx))
        return "sha256:" + self.podman("image", "inspect", "--format", "{{.Id}}",
                                       f"localhost/proof/omniroute:{variant}").stdout.strip()

    def publish(self, variant: str) -> None:
        """Move the registry tag `latest` to `variant` (the agreed mutable-tag transition). `podman push SRC DEST`
        does not retag locally, so the proof backend's own `self.ref` tag only moves when the updater moves it."""
        self.podman("push", "--format", "oci", "--tls-verify=false",
                    f"localhost/proof/omniroute:{variant}", f"docker://{self.ref}")

    @contextlib.contextmanager
    def health_pump(self, interval: float = 2.0):
        """Simulate the engine's automatic healthchecks for this backend (see wait_healthy).

        Runs `podman healthcheck run` for every project container until the context exits, so an
        updater readiness wait sees healthy status the way it would on the production backend."""
        stop = threading.Event()

        def loop():
            while not stop.is_set():
                for name in self.project_containers():
                    try:
                        self._run(["podman", "healthcheck", "run", name], check=False)
                    except OSError:
                        pass
                stop.wait(interval)

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=10)

    def snapshot(self) -> dict:
        """Everything the updater must leave alone: ids and StartedAt per container, volume names, network ids."""
        containers = {}
        for item in json.loads(self.podman("ps", "--all", "--format", "json").stdout):
            containers[item["Names"][0]] = (item["Id"], item["StartedAt"])
        return {"containers": containers,
                "volumes": sorted(self.podman("volume", "ls", "--format", "{{.Name}}").stdout.split()),
                "networks": sorted(self.podman("network", "ls", "--format", "{{.ID}}").stdout.split())}

    def reset_deployment(self) -> None:
        """Between tests: remove the project's containers, volumes and network; the registry stays."""
        for name in self.project_containers():
            self.podman("rm", "--force", "--time", "1", name)
        for volume in self.podman("volume", "ls", "--filter", f"label=io.podman.compose.project={PROJECT}",
                                  "--format", "{{.Name}}").stdout.split():
            self.podman("volume", "rm", "--force", volume)
        self.podman("network", "rm", "--force", f"{PROJECT}_default", check=False)
        # A previous test's updater run re-tags the local copy of the mutable reference; without dropping
        # it here, the next deploy would reuse that stale digest instead of the freshly published tag.
        self.podman("rmi", "--force", self.ref, check=False)

    # --- argv recording (PATH shims) -------------------------------------------
    def install_shims(self) -> pathlib.Path:
        shim = self.root / "shim"
        for tool in ("podman", "podman-compose"):
            real = shutil.which(tool)
            (shim / tool).write_text(
                f"#!/usr/bin/env bash\nprintf '%s\\037' \"${{0##*/}}\" \"$@\" >> '{self.argv_log}'\n"
                f"printf '\\n' >> '{self.argv_log}'\nexec '{real}' \"$@\"\n")
            (shim / tool).chmod(0o755)
        return shim

    def recorded_argv(self) -> list[list[str]]:
        if not self.argv_log.exists():
            return []
        return [line.split("\x1f")[:-1] for line in self.argv_log.read_text().splitlines() if line]

    def clear_argv_log(self) -> None:
        self.argv_log.write_text("")
