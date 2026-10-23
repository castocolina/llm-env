"""Disposable rootless Podman backend for the opt-in updater proofs. Every call is ownership-checked."""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from omniroute_fakes import free_port

REGISTRY_IMAGE = "docker.io/library/registry:2"
BASE_IMAGE = "docker.io/library/python:3.12-slim"
PROJECT = "proofproj"


class ProofSkip(Exception):
    """A host prerequisite is missing; the message is the exact command that fixes it."""


class ProofBackend:
    def __init__(self) -> None:
        (REPO / "tmp").mkdir(exist_ok=True)
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="proof-", dir=REPO / "tmp")).resolve()
        self.reg_port = free_port()
        self.ref = f"127.0.0.1:{self.reg_port}/proof/omniroute:latest"          # the configured, mutable reference
        for name in ("graph", "run", "netconf", "shim"):
            (self.root / name).mkdir()
        (self.root / "storage.conf").write_text(
            f'[storage]\ndriver = "vfs"\ngraphroot = "{self.root}/graph"\nrunroot = "{self.root}/run"\n')
        # Ruling: pasta cannot open its rootless-netns PID file under the repo's ./tmp mount
        # ("Permission denied"), so the proof backend pins slirp4netns for rootless networking.
        (self.root / "containers.conf").write_text(
            f'[containers]\nlog_driver = "journald"\n'
            f'[network]\nnetwork_config_dir = "{self.root}/netconf"\ndefault_rootless_network_cmd = "slirp4netns"\n')
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
        self._health_stop: threading.Event | None = None
        self._health_thread: threading.Thread | None = None

    # --- environment and guards -------------------------------------------------
    def env(self) -> dict[str, str]:
        return {"CONTAINERS_STORAGE_CONF": str(self.root / "storage.conf"),
                "CONTAINERS_CONF": str(self.root / "containers.conf"),
                "CONTAINERS_REGISTRIES_CONF": str(self.root / "registries.conf")}

    def _run(self, argv, check: bool = True, **kw) -> subprocess.CompletedProcess:
        return subprocess.run(argv, env={**os.environ, **self.env()}, text=True, capture_output=True, check=check, **kw)

    def assert_owned(self) -> None:
        info = json.loads(self._run(["podman", "info", "--format", "json"], check=True).stdout)
        for key in ("graphRoot", "runRoot"):
            assert os.path.realpath(info["store"][key]).startswith(str(self.root) + os.sep), "refusing: not the proof backend"

    def assert_pristine(self) -> None:
        self.assert_owned()
        assert self.podman("ps", "--all", "--quiet").stdout.strip() == ""
        assert self.podman("image", "list", "--quiet").stdout.strip() == ""

    def podman(self, *args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
        self.assert_owned()
        return self._run(["podman", *args], check=check, **kw)

    # --- fixtures ---------------------------------------------------------------
    def import_host_image(self, ref: str) -> None:
        if subprocess.run(["podman", "image", "exists", ref], check=False).returncode != 0:
            raise ProofSkip(f"host image store lacks {ref}; run: podman pull {ref}")
        self.assert_owned()
        save = subprocess.Popen(["podman", "save", ref], stdout=subprocess.PIPE)  # host store: no proof env
        subprocess.run(["podman", "load"], stdin=save.stdout, env={**os.environ, **self.env()},
                       check=True, capture_output=True)
        assert save.wait() == 0

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

    def deploy(self, compose_path: pathlib.Path) -> None:
        self.assert_owned()
        # The loop must run *during* `up -d`: a `depends_on: service_healthy` edge makes podman-compose
        # wait for the parent to report healthy, which only happens while the loop drives the checks.
        self.start_health_loop()
        subprocess.run([shutil.which("podman-compose") or "podman-compose", "--in-pod", "false", "--env-file", "/dev/null",
                        "-p", PROJECT, "-f", str(compose_path), "up", "-d"],
                       env={**os.environ, **self.env()}, check=True, capture_output=True, text=True, timeout=300)
        for name in self.project_containers():
            self.wait_healthy(name)

    def start_health_loop(self, interval: float = 2.0) -> None:
        """Drive the healthchecks explicitly. Ruling: conmon sanitizes its environment, so with an isolated
        CONTAINERS_STORAGE_CONF its automatic timer execs `podman` against the default store and can never find the
        proof containers (journal: "no container with name or ID ... found"). `podman healthcheck run` under the
        proof env performs the same CMD on the same cadence the timer would."""
        if self._health_stop is not None:
            return
        stop = threading.Event()
        self._health_stop = stop

        def loop() -> None:
            while not stop.wait(interval):
                # The containers churn (created/removed between tests) and `podman healthcheck run`
                # exits 125 while one is mid-creation; a transient failure must not kill the loop,
                # or a `depends_on: service_healthy` deploy would wait forever for a healthy parent.
                try:
                    names = self.project_containers()
                except (OSError, AssertionError, subprocess.SubprocessError):  # transient store churn; the next tick retries
                    continue
                for name in names:
                    try:
                        self._run(["podman", "healthcheck", "run", name], check=False)
                    except (subprocess.SubprocessError, OSError):  # transient; the next tick retries
                        pass

        self._health_thread = threading.Thread(target=loop, daemon=True)
        self._health_thread.start()

    def stop_health_loop(self) -> None:
        stop, thread = self._health_stop, self._health_thread
        if stop is not None and thread is not None:
            stop.set()
            thread.join(timeout=30)
        self._health_stop = None
        self._health_thread = None

    def project_containers(self) -> list[str]:
        out = self.podman("ps", "--all", "--filter", f"label=io.podman.compose.project={PROJECT}",
                          "--format", "{{.Names}}").stdout
        return sorted(out.split())

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

    def wait_healthy(self, name: str, timeout: int = 90) -> None:
        # Ruling: automatic conmon healthcheck timers do not fire in this disposable backend
        # (State.Health stays "starting" with an empty log), so the harness runs the check itself.
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.podman("inspect", name, "--format", "{{.State.Health.Status}}").stdout.strip() == "healthy":
                return
            self.podman("healthcheck", "run", name, check=False)
            time.sleep(1)
        raise AssertionError(f"{name} did not become healthy")

    def image_of(self, name: str) -> str:
        return "sha256:" + self.podman("inspect", name, "--format", "{{.Image}}").stdout.strip()

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

    def close(self) -> None:
        self.stop_health_loop()
        try:
            self.assert_owned()
            self._run(["podman", "rm", "--force", "--all", "--time", "1"])
            self._run(["podman", "unshare", "rm", "-rf", str(self.root)])
        finally:
            shutil.rmtree(self.root, ignore_errors=True)
            if self.root.exists():
                # Image content extracted by a previous run can belong to the subuid range, which plain
                # rmtree cannot unlink. Retry through podman's own uid-mapped namespace, without the
                # (possibly already deleted) proof storage conf.
                env = {k: v for k, v in os.environ.items() if k != "CONTAINERS_STORAGE_CONF"}
                subprocess.run(["podman", "unshare", "rm", "-rf", str(self.root)], env=env, check=False,
                               capture_output=True, text=True, timeout=120)
                shutil.rmtree(self.root, ignore_errors=True)
