"""HoloLab gateway — control-plane backend, static server, preview proxy."""

__all__ = ["JobState", "JobStateMachine", "NodeRegistry", "create_app"]


def __getattr__(name):
    if name == "create_app":
        from hololab.gateway.app import create_app

        return create_app
    if name in {"JobState", "JobStateMachine"}:
        from hololab.gateway import jobs

        return getattr(jobs, name)
    if name == "NodeRegistry":
        from hololab.gateway.registry import NodeRegistry

        return NodeRegistry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
