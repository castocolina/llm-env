from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phoenix_compose_uses_verified_python_readiness_healthcheck() -> None:
    import yaml

    compose = yaml.safe_load((ROOT / "docker/phoenix/compose.yml").read_text())
    healthcheck = compose["services"]["phoenix"].get("healthcheck")
    assert healthcheck == {
        "test": [
            "CMD",
            "/usr/bin/python3.13",
            "-c",
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:6006/readyz', timeout=3)",
        ],
        "interval": "10s",
        "timeout": "5s",
        "retries": 5,
        "start_period": "20s",
    }


def test_normal_docs_explain_laya_history_privacy_and_retention_limits() -> None:
    readme = (ROOT / "README.md").read_text()
    quick_start = (ROOT / "QUICK_START.md").read_text()
    for document in (readme, quick_start):
        for detail in (
            "laya", "LLM_ENV_PHOENIX_PORT", "LLM_ENV_PHOENIX_RETENTION_DAYS",
            "24,000", "64 KiB", "128", "1.5-second", "other users",
            "off-host", "not a", "confidence", "not been measured",
        ):
            assert detail.lower() in document.lower(), f"missing {detail}"
        assert "256 MiB" in document
        assert "1 GiB" in document
        assert "2 CPU" in document
        assert "256 relative" in document and "shares" in document
        assert "does not protect" in document.lower() or "can access the traces" in document.lower()
