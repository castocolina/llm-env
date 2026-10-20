from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
from collections.abc import Mapping

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "tests" / "phoenix_browser_runtime.cjs"


def _node_json(script: str, env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    assert node
    return subprocess.run(
        [node, "-e", script, str(RUNTIME)], cwd=ROOT, env=env,
        text=True, capture_output=True, check=False,
    )


def test_playwright_uses_configured_module_and_executable_overrides(tmp_path: pathlib.Path) -> None:
    module_root = tmp_path / "playwright"
    module_root.mkdir()
    executable = tmp_path / "browser" / "chrome"
    executable.parent.mkdir()
    executable.write_text("worker-owned harmless browser stub")
    (module_root / "index.js").write_text(
        "exports.chromium = { executablePath: () => '/must/not/be/used' };\n"
    )
    result = _node_json(
        "const r=require(process.argv[1]); const x=r.resolveBrowserRuntime(); "
        "console.log(JSON.stringify({module:x.modulePath,browser:x.executablePath}));",
        os.environ | {
            "LLM_ENV_PLAYWRIGHT_ROOT": str(module_root),
            "LLM_ENV_CHROMIUM_EXECUTABLE": str(executable),
        },
    )
    assert result.returncode == 0, result.stderr
    assert str(module_root / "index.js") in result.stdout
    assert str(executable) in result.stdout


def test_normal_playwright_install_precedes_explicit_root(tmp_path: pathlib.Path) -> None:
    normal_root = tmp_path / "normal" / "node_modules" / "playwright"
    configured_root = tmp_path / "configured"
    normal_root.mkdir(parents=True)
    configured_root.mkdir()
    executable = tmp_path / "chromium"
    executable.write_text("worker-owned harmless browser stub")
    configured_executable = tmp_path / "configured-chromium"
    configured_executable.write_text("worker-owned harmless browser stub")
    (normal_root / "index.js").write_text(
        f"exports.chromium = {{ executablePath: () => {str(executable)!r} }};\n"
    )
    (configured_root / "index.js").write_text(
        f"exports.chromium = {{ executablePath: () => {str(configured_executable)!r} }};\n"
    )
    result = _node_json(
        "const r=require(process.argv[1]); console.log(r.resolveBrowserRuntime().modulePath);",
        os.environ | {
            "NODE_PATH": str(normal_root.parent),
            "LLM_ENV_PLAYWRIGHT_ROOT": str(configured_root),
            "LLM_ENV_CHROMIUM_EXECUTABLE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert str(normal_root / "index.js") in result.stdout


def test_playwright_discovers_generic_npm_npx_cache_without_machine_paths(tmp_path: pathlib.Path) -> None:
    cache = tmp_path / "npm-cache"
    package = cache / "_npx" / "cache-entry" / "node_modules" / "playwright"
    package.mkdir(parents=True)
    executable = tmp_path / "chromium"
    executable.write_text("worker-owned harmless browser stub")
    (package / "index.js").write_text(
        f"exports.chromium = {{ executablePath: () => {str(executable)!r} }};\n"
    )
    result = _node_json(
        "const r=require(process.argv[1]); console.log(r.resolveBrowserRuntime().modulePath);",
        os.environ | {
            "HOME": str(tmp_path / "home"), "NPM_CONFIG_CACHE": str(cache),
            "LLM_ENV_PLAYWRIGHT_ROOT": "", "LLM_ENV_CHROMIUM_EXECUTABLE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert str(package / "index.js") in result.stdout


def test_playwright_discovery_failure_is_actionable_without_installing(tmp_path: pathlib.Path) -> None:
    result = _node_json(
        "const r=require(process.argv[1]); try { r.resolveBrowserRuntime(); } "
        "catch(e) { console.log(e.message); process.exit(0); } process.exit(2);",
        os.environ | {
            "HOME": str(tmp_path / "empty-home"),
            "LLM_ENV_PLAYWRIGHT_ROOT": str(tmp_path / "missing-module"),
            "LLM_ENV_CHROMIUM_EXECUTABLE": str(tmp_path / "missing-browser"),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "LLM_ENV_PLAYWRIGHT_ROOT" in result.stdout
    assert "LLM_ENV_CHROMIUM_EXECUTABLE" in result.stdout


def test_browser_request_allowlist_requires_exact_configured_origin() -> None:
    result = _node_json(
        "const r=require(process.argv[1]); const base='http://127.0.0.1:6006'; "
        "console.log(JSON.stringify(['http://127.0.0.1:6006/api','http://127.0.0.1:8010/',"
        "'http://localhost:6006/','https://127.0.0.1:6006/'].map(u=>r.isAllowedOrigin(base,u))));",
        os.environ,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[true,false,false,false]"
