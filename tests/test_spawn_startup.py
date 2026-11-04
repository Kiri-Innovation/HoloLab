import pytest

from hololab.node.config import NodeConfig
from hololab.node.runtime import NodeRuntime


@pytest.mark.asyncio
async def test_spawn_validation_rechecks_fingerprint_without_reprobing_environment(tmp_path):
    """Spawn must validate cached evidence, not blindly trust the TTL cache."""

    class Runtime:
        mode = "host"
        env = "host"

    class Manifest:
        runtime = Runtime()
        requires = type("Requires", (), {})()

    fresh_flags: list[bool] = []

    class Checker:
        def check(self, *_args, fresh=False, **_kwargs):
            fresh_flags.append(fresh)
            return {"status": "satisfied"}

    runtime = NodeRuntime(NodeConfig(workspace_root=tmp_path / "workspace"))
    runtime._capability_checker = Checker()

    await runtime._environment_report(Manifest(), fresh=True)
    await runtime._environment_report(Manifest(), validate_cached=True)

    # The second call reaches CapabilityChecker (so it validates the cached
    # fingerprint) but does not launch a fresh environment probe when unchanged.
    assert fresh_flags == [True, False]
