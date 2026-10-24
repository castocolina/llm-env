"""Shared builder for render_compose tests."""


def limits_of(cfg):
    """`limits` for render_compose built from a test cfg's `resources` section (test input only)."""
    from pylib.resources import effective_policy

    resources = cfg.get("resources", {})
    return {
        "llm_server": resources.get("llm_server", {}),
        "omniroute": resources.get("omniroute", {}),
        "remote_setup": resources.get("remote_setup", {}),
        "system_one": effective_policy("system_one", resources.get("overrides")),
    }
