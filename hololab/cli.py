"""``hololab`` command-line interface.

Subcommands:
    hololab start                    — gateway + one local node (the default;
                                       one process, one Ctrl+C)
    hololab start gateway            — gateway only (supports --reload)
    hololab start node               — node only, connecting to a gateway
    hololab dev                      — split-process dev mode (reload + Vite)
    hololab status                   — probe a locally-running instance
    hololab pack init <name>@<ver>   — scaffold a new pack skeleton
    hololab pack validate <dir>      — validate a manifest without running

Design note: the CLI is intentionally thin. All logic lives in the modules
under ``hololab.gateway`` and ``hololab.node``; this file only wires them up
to typer and runs the asyncio loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

import typer
import uvicorn
import yaml

from hololab import __version__
from hololab.gateway.app import create_app
from hololab.logging import configure as configure_logging
from hololab.logging import get_logger
from hololab.manifest import load_manifest
from hololab.node.config import load_node_config
from hololab.node.runtime import NodeRuntime
from hololab.paths import default_workspace_root, frontend_dist_dir

app = typer.Typer(
    name="hololab",
    help="HoloLab — a node-graph GUI for CV research pipelines.",
    add_completion=False,
    no_args_is_help=True,
)

start_app = typer.Typer(help="Start the gateway, a node, or both.")
app.add_typer(start_app, name="start", invoke_without_command=True)

pack_app = typer.Typer(help="Author-side tools for algorithm packs.")
app.add_typer(pack_app, name="pack")

log = get_logger("cli")

# Package directory — used as the reload watch dir for uvicorn.
_PACKAGE_DIR = str(Path(__file__).resolve().parent)


@app.callback()
def _root(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG-level logs."),
) -> None:
    configure_logging(level="DEBUG" if verbose else "INFO")


@app.command()
def version() -> None:
    """Print the HoloLab version."""

    typer.echo(f"hololab {__version__}")


# ---------------------------------------------------------------------------
# `hololab start ...`
# ---------------------------------------------------------------------------


@start_app.callback(invoke_without_command=True)
def start_default(
    ctx: typer.Context,
    host: str = typer.Option(
        "0.0.0.0",
        "--host",
        help="Gateway bind host. Default is 0.0.0.0 so peers on your LAN can open the UI too.",
    ),
    port: int = typer.Option(8828, "--port", help="Gateway port."),
    node_config: Path | None = typer.Option(
        None,
        "--node-config",
        help="Path to node config.yaml (default: ~/.hololab/node/config.yaml).",
    ),
    workspace_root: Path | None = typer.Option(
        None,
        "--workspace-root",
        help=(
            "Override the node's artifact root for this run. Precedence: "
            "this flag > config file value > ~/.hololab/node/workspace default. "
            "Old artifacts under a previous root: add the old path to "
            "``legacy_workspace_roots`` in config.yaml so they stay resolvable."
        ),
    ),
) -> None:
    """Default: run gateway + one local node + a file-server subprocess.

    The one-command product experience — one process tree, one Ctrl+C.
    Gateway and node share this asyncio loop so REST/WS/scheduling stay
    trivially in sync; the file server runs as a child process (see
    :mod:`hololab.node.fileserver_main`) so ffmpeg-heavy ``/_thumb`` /
    ``/_preview`` traffic doesn't steal the scheduler's CPU time slices.
    The child sits in a fresh OS session — terminal SIGINT reaches only
    the parent, which forwards SIGTERM to the child during shutdown.
    Use ``hololab dev`` if you want gateway auto-reload split across
    processes.
    """

    if ctx.invoked_subcommand is not None:
        return
    _run_all_in_one_sync(host, port, node_config, workspace_root=workspace_root)


@start_app.command("gateway")
def start_gateway(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8828, "--port"),
    reload: bool = typer.Option(
        False,
        "--reload",
        help="Auto-reload the gateway when source files change (dev only).",
    ),
) -> None:
    """Run only the gateway."""

    _scrub_proxy_env()
    _fail_if_port_busy(host, port, label="gateway")
    _print_gateway_summary(host, port, reload=reload)

    if reload:
        # Uvicorn's reloader needs an importable factory; ``create_app`` is one.
        uvicorn.run(
            "hololab.gateway.app:create_app",
            factory=True,
            host=host,
            port=port,
            reload=True,
            reload_dirs=[_PACKAGE_DIR],
            log_level="info",
            access_log=False,
        )
    else:
        uvicorn.run(
            create_app(),
            host=host,
            port=port,
            log_level="info",
            access_log=False,
        )


@start_app.command("node")
def start_node(
    gateway: str | None = typer.Option(
        None, "--gateway", help="Gateway ws:// URL. Overrides node config."
    ),
    node_config: Path | None = typer.Option(None, "--config", help="Path to node config.yaml."),
    workspace_root: Path | None = typer.Option(
        None,
        "--workspace-root",
        help=(
            "Override the artifact root for this run. Precedence: this flag > "
            "config file > default. Non-absolute paths are rejected."
        ),
    ),
) -> None:
    """Run only a node runtime, connecting to a remote gateway."""

    _scrub_proxy_env()
    cfg = load_node_config(node_config)
    updates: dict[str, object] = {}
    if gateway:
        updates["gateway_url"] = gateway
    if workspace_root is not None:
        if not workspace_root.is_absolute():
            typer.echo(f"✗ --workspace-root must be absolute, got {workspace_root}", err=True)
            raise typer.Exit(2)
        updates["workspace_root"] = workspace_root
    if updates:
        cfg = cfg.model_copy(update=updates)
    _print_node_summary(cfg)
    asyncio.run(NodeRuntime(cfg, config_path=node_config).run())


def _run_all_in_one_sync(
    host: str,
    port: int,
    node_config_path: Path | None,
    *,
    workspace_root: Path | None = None,
) -> None:
    """Sync wrapper that owns process-wide preflight + graceful exit.

    Preflight (once, before the asyncio loop starts):
        * scrub HTTP proxy env vars so the node's WebSocket client never
          tries to route ws://127.0.0.1 through the user's proxy
          (killed us live once — see the diagnostic in the banner);
        * check the gateway port is free with a socket bind, and if not,
          look up the offending PID via ``lsof`` and print a helpful
          hint before exiting with a non-zero code.

    Runtime: hand off to :func:`_run_all_in_one_async`.

    Shutdown: swallow KeyboardInterrupt so the terminal doesn't get a
    traceback for the expected Ctrl+C. Non-KeyboardInterrupt exceptions
    propagate normally.
    """

    _scrub_proxy_env()
    _fail_if_port_busy(host, port, label="gateway")
    if workspace_root is not None and not workspace_root.is_absolute():
        typer.echo(f"✗ --workspace-root must be absolute, got {workspace_root}", err=True)
        raise typer.Exit(2)

    _print_all_in_one_banner(host, port, workspace_root_override=workspace_root)

    # asyncio.run re-raises KeyboardInterrupt after the shutdown path
    # completes — swallow it so the terminal ends without a traceback.
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            _run_all_in_one_async(host, port, node_config_path, workspace_root=workspace_root)
        )


async def _run_all_in_one_async(
    host: str,
    port: int,
    node_config_path: Path | None,
    *,
    workspace_root: Path | None = None,
) -> None:
    """Run gateway + node concurrently in one asyncio loop.

    Signal handling: SIGINT/SIGTERM tell the gateway to exit and cancel
    the node task. Uvicorn's own signal handlers are disabled so we
    aren't racing it — one signal path, one shutdown line.
    """

    cfg = load_node_config(node_config_path)
    updates: dict[str, object] = {"gateway_url": f"ws://127.0.0.1:{port}"}
    if workspace_root is not None:
        updates["workspace_root"] = workspace_root
    cfg = cfg.model_copy(update=updates)

    gateway_app = create_app()
    config = uvicorn.Config(
        gateway_app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    # We drive lifecycle ourselves, so tell uvicorn not to install its
    # own SIGINT/SIGTERM hooks (otherwise both fire on Ctrl+C and the
    # output is confusing).
    config.install_signal_handlers = False
    server = uvicorn.Server(config)

    loop = asyncio.get_running_loop()
    shutdown_started = False

    def _request_shutdown(signum: int) -> None:
        nonlocal shutdown_started
        if shutdown_started:
            return
        shutdown_started = True
        name = signal.Signals(signum).name if signum else "shutdown"
        typer.echo(f"\n· {name} received — stopping…", err=True)
        server.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_shutdown, sig)

    server_task = asyncio.create_task(server.serve(), name="hololab-gateway")
    # Small delay so the gateway is listening before the node dials in — cosmetic.
    await asyncio.sleep(0.3)
    node_task = asyncio.create_task(
        NodeRuntime(cfg, config_path=node_config_path).run(), name="hololab-node"
    )

    # Wait until either task exits (usually because the gateway shut
    # down after receiving the signal). Then drain the other one.
    _, pending = await asyncio.wait({server_task, node_task}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    for t in pending:
        with contextlib.suppress(asyncio.CancelledError):
            await t
    # Re-raise any real error the gateway hit (not the normal exit).
    if server_task.done():
        exc = server_task.exception()
        if exc is not None and not isinstance(exc, KeyboardInterrupt):
            raise exc


# ---------------------------------------------------------------------------
# `hololab dev` — split-process dev mode with reload + Vite banner
# ---------------------------------------------------------------------------


@app.command("dev")
def dev(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8828, "--port"),
    node_config: Path | None = typer.Option(
        None, "--node-config", help="Path to node config.yaml."
    ),
    frontend_url: str = typer.Option(
        "http://127.0.0.1:5173",
        "--frontend-url",
        help="URL of the Vite dev server the banner will point developers to.",
    ),
) -> None:
    """Dev mode: gateway with auto-reload + local node + Vite banner.

    Spawns the gateway and node as separate subprocesses so uvicorn's file
    watcher can restart only the gateway on Python edits. The node reconnects
    automatically via its exponential-backoff loop.

    Sets ``HOLOLAB_DEV=1`` for the gateway so it skips serving ``frontend/dist``
    (a stale bundle would silently mask the Vite dev server otherwise).
    """

    _scrub_proxy_env()
    _fail_if_port_busy(host, port, label="gateway")
    _print_dev_banner(host, port, frontend_url)

    env = {**os.environ, "HOLOLAB_DEV": "1"}

    # Gateway subprocess with reload on.
    gateway_argv = [
        sys.executable,
        "-m",
        "hololab.cli",
        "start",
        "gateway",
        "--host",
        host,
        "--port",
        str(port),
        "--reload",
    ]

    # Node subprocess dialing our just-started gateway.
    node_argv = [
        sys.executable,
        "-m",
        "hololab.cli",
        "start",
        "node",
        "--gateway",
        f"ws://{host}:{port}",
    ]
    if node_config is not None:
        node_argv += ["--config", str(node_config)]

    gateway_proc = subprocess.Popen(gateway_argv, env=env)
    # Small delay so the node's first dial-in doesn't race the gateway boot.
    _sleep_or_die(gateway_proc, 0.8)
    node_proc = subprocess.Popen(node_argv, env=env)

    procs = [gateway_proc, node_proc]

    def _terminate_all(*_: object) -> None:
        for p in procs:
            if p.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    p.terminate()

    signal.signal(signal.SIGINT, _terminate_all)
    signal.signal(signal.SIGTERM, _terminate_all)

    # Wait for either subprocess to exit; then take the other one down too.
    try:
        while True:
            for p in procs:
                rc = p.poll()
                if rc is not None:
                    _terminate_all()
                    for q in procs:
                        with contextlib.suppress(Exception):
                            q.wait(timeout=5)
                    raise typer.Exit(code=rc if rc is not None else 0)
            _sleep_or_die(gateway_proc, 0.5)
    except KeyboardInterrupt:
        _terminate_all()
        for p in procs:
            with contextlib.suppress(Exception):
                p.wait(timeout=5)


def _sleep_or_die(proc: subprocess.Popen, seconds: float) -> None:
    """Sleep, but abort early if the given subprocess has already exited."""

    end = _monotonic() + seconds
    while _monotonic() < end:
        if proc.poll() is not None:
            return
        _sleep(0.05)


def _monotonic() -> float:
    import time

    return time.monotonic()


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


# ---------------------------------------------------------------------------
# `hololab status` — quick probe of a locally-running instance
# ---------------------------------------------------------------------------


@app.command()
def status(
    url: str = typer.Option(
        "http://127.0.0.1:8828",
        "--url",
        help="Gateway URL to probe. Defaults to the local instance.",
    ),
) -> None:
    """Probe a running gateway: URL, online nodes, packs, last job.

    Exit code 0 if the gateway responded and at least reported healthy;
    non-zero otherwise. Intended as a one-liner sanity check ("is my
    hololab up?") without opening the browser.
    """

    import httpx

    # Loopback traffic must not be routed through a shell-set http proxy
    # (same trap that used to blow up node registration). Scrub before
    # any request so httpx doesn't pick them up from env.
    _scrub_proxy_env()

    try:
        h = httpx.get(f"{url}/api/health", timeout=2.0).raise_for_status().json()
    except httpx.HTTPError as exc:
        typer.echo(f"✗ gateway not reachable at {url}: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"✓ gateway {url}  v{h.get('version', '?')}  protocol v{h.get('protocol_v_max')}")

    # Nodes: how many are online, what packs each carries.
    try:
        nodes = httpx.get(f"{url}/api/nodes", timeout=2.0).raise_for_status().json()
    except httpx.HTTPError:
        nodes = []
    if not nodes:
        typer.echo("  nodes:  (none online)")
    else:
        for n in nodes:
            typer.echo(
                f"  node    {n['node_id'][:8]}  {n['node_name']}  "
                f"packs={len(n.get('packs', []))}  gpu={n.get('gpu', {}).get('name') or '(no gpu)'}"
            )
            ws = n.get("workspace_root")
            if ws:
                typer.echo(f"          workspace {ws}")
            legacy = n.get("legacy_workspace_roots") or []
            if legacy:
                typer.echo(f"          legacy    {', '.join(legacy)}")

    # Newest job — one-line summary. Useful as a smoke-check.
    try:
        jobs = httpx.get(f"{url}/api/jobs", timeout=2.0).raise_for_status().json()
    except httpx.HTTPError:
        jobs = []
    if jobs:
        j = jobs[0]
        typer.echo(f"  last    {j['job_id'][:8]}  {j['algorithm_name']}  state={j['state']}")
    else:
        typer.echo("  jobs:   (none yet)")


# ---------------------------------------------------------------------------
# `hololab pack ...`
# ---------------------------------------------------------------------------


@pack_app.command("validate")
def pack_validate(pack_dir: Path) -> None:
    """Validate a pack's ``manifest.yaml`` and print a short summary."""

    manifest_path = pack_dir / "manifest.yaml"
    if not manifest_path.exists():
        typer.echo(f"error: no manifest.yaml under {pack_dir}", err=True)
        raise typer.Exit(1)

    manifest, sha = load_manifest(manifest_path)
    typer.echo(f"✓ {manifest.name}@{manifest.version}")
    typer.echo(f"  hash:       {sha}")
    typer.echo(f"  env:        {manifest.runtime.env}")
    typer.echo(f"  category:   {'/'.join(manifest.category) if manifest.category else '(unset)'}")
    typer.echo(
        f"  docs:       {f'yes ({len(manifest.docs.splitlines())} lines)' if manifest.docs else '(unset)'}"
    )
    typer.echo(f"  inputs:     {list(manifest.inputs)}")
    typer.echo(f"  outputs:    {list(manifest.outputs)}")
    typer.echo(f"  previews:   {len(manifest.previews)}")


@pack_app.command("init")
def pack_init(
    spec: str = typer.Argument(..., help="Pack spec in the form NAME@VERSION, e.g. my-algo@0.1.0."),
    packs_dir: Path = typer.Option(
        Path.cwd() / "packs",
        "--packs-dir",
        help="Directory to create the pack under (default: ./packs).",
    ),
    env: str = typer.Option("kiri", "--env", help="Logical conda env name for runtime.env."),
) -> None:
    """Scaffold a new algorithm pack directory with a working manifest + README.

    The resulting pack passes ``hololab pack validate`` immediately. You'll
    still need to fill in your algorithm's ``exec.shell``, inputs, outputs, and
    params.
    """

    if "@" not in spec:
        typer.echo("error: spec must be NAME@VERSION (e.g. my-algo@0.1.0)", err=True)
        raise typer.Exit(2)

    name, version = spec.split("@", 1)
    target = packs_dir / f"{name}@{version}"
    if target.exists():
        typer.echo(f"error: {target} already exists", err=True)
        raise typer.Exit(1)

    target.mkdir(parents=True)

    manifest = {
        "apiVersion": "hololab.dev/v1",
        "kind": "Algorithm",
        "name": name,
        "version": version,
        "description": f"TODO: one-line summary of {name}.",
        "category": "uncategorized",
        "docs": (
            f"TODO: short Markdown blurb for {name}.\n\n"
            "- **Input:** what this pack reads.\n"
            "- **Output:** what it writes.\n"
            "- Key knobs / gotchas an operator should know.\n"
        ),
        "params": {
            "message": {"type": "string", "default": "hello", "description": "Sample param."}
        },
        "outputs": {
            "out_dir": {"tags": [name], "description": "Sample output dir."},
        },
        "runtime": {"env": env, "gpu": {"required": False}},
        "exec": {
            "shell": (
                "set -euo pipefail\n"
                'echo "{{ params.message }}" > "{{ outputs.out_dir }}/message.txt"\n'
            )
        },
        "idempotency": {"marker": "{{ outputs.out_dir }}/.hololab-done"},
    }

    (target / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    (target / "README.md").write_text(_pack_readme(name, version, env))

    # Sanity-validate what we just wrote so misconfigs surface immediately.
    load_manifest(target / "manifest.yaml")

    typer.echo(f"✓ scaffolded {target}")
    typer.echo(f"  next: edit {target / 'manifest.yaml'} then run `hololab pack validate {target}`")


def _pack_readme(name: str, version: str, env: str) -> str:
    return (
        f"# {name}\n\n"
        f"Version `{version}`. Runs in logical env `{env}`.\n\n"
        "## Inputs\n"
        "TODO: describe each input port.\n\n"
        "## Outputs\n"
        "- `out_dir` — TODO.\n\n"
        "## Env\n"
        f"Map `{env}` on your node with:\n\n"
        "```yaml\n"
        "envs:\n"
        f"  {env}: /path/to/your/conda/env\n"
        "```\n"
    )


# ---------------------------------------------------------------------------
# Startup preflight helpers
# ---------------------------------------------------------------------------


# Env vars we scrub — matches the common Unix (lowercase) and Windows
# (uppercase) conventions. All get dropped from os.environ so any
# subprocesses / clients we spawn inherit a clean environment.
_PROXY_ENV_VARS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)


def _scrub_proxy_env() -> list[str]:
    """Remove HTTP proxy env vars for this process (and its children).

    Rationale: `hololab start` binds to loopback and the embedded node
    dials `ws://127.0.0.1`, so a user's default `http_proxy` env var
    (very common on internal networks) makes the WebSocket handshake go
    through a proxy that then returns an HTML error page. Killed us
    live once — the fix is to just refuse to see the vars.

    Returns the list of names that were actually present, so the caller
    can print a one-line "we ignored X" banner.
    """

    present = [name for name in _PROXY_ENV_VARS if os.environ.get(name)]
    for name in present:
        os.environ.pop(name, None)
    if present:
        # Dedupe when both upper- and lowercase forms are present.
        pretty = ", ".join(sorted({name.lower() for name in present}))
        typer.echo(
            f"· proxy env ignored ({pretty}) — hololab runs on loopback",
            err=True,
        )
    return present


def _fail_if_port_busy(host: str, port: int, *, label: str) -> None:
    """Preflight the gateway bind so a busy port gives an actionable error.

    We attempt a real ``bind()`` on the same address family uvicorn will
    use, then close it immediately. If it fails with EADDRINUSE we shell
    out to ``lsof`` to identify the holder and print a message the user
    can act on ("kill 12345 and retry" or "--port 8830").
    """

    bind_host = "0.0.0.0" if host in ("", "0.0.0.0") else host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((bind_host, port))
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        holder = _find_port_holder(port)
        holder_hint = f" (held by PID {holder})" if holder else ""
        typer.echo(
            f"✗ {label} port {port}{holder_hint} is already in use.\n"
            f"  either stop the other process (e.g. `kill {holder}`) or pass "
            f"`--port <free-port>`.",
            err=True,
        )
        raise typer.Exit(2) from exc


def _find_port_holder(port: int) -> int | None:
    """Best-effort lookup of the PID currently listening on ``port``.

    Uses ``lsof`` because it's available on macOS + most Linux distros.
    Any failure returns None — this is diagnostics, not correctness.
    """

    try:
        proc = subprocess.run(
            ["lsof", f"-iTCP:{port}", "-sTCP:LISTEN", "-Pn", "-t"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    line = proc.stdout.strip().splitlines()
    if not line:
        return None
    try:
        return int(line[0])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Banner rendering (used by every `hololab start*` command)
# ---------------------------------------------------------------------------


def _display_urls(host: str, port: int) -> list[str]:
    """Which URLs to advertise in the banner given the bind host.

    When bound to 0.0.0.0 the user's browser can talk to either loopback
    or the LAN address — we show both so the "open this on my Mac" flow
    is one copy-paste. When bound to a specific host we just echo it.
    """

    if host in ("", "0.0.0.0"):
        urls = [f"http://127.0.0.1:{port}"]
        lan = _guess_lan_address()
        if lan and lan != "127.0.0.1":
            urls.append(f"http://{lan}:{port}")
        return urls
    return [f"http://{host}:{port}"]


def _guess_lan_address() -> str | None:
    """Best-effort primary LAN IPv4 (no external traffic sent).

    We open a UDP socket to a routable but unreachable address and read
    back the local endpoint the kernel would use — this doesn't actually
    send anything but it's the standard cross-platform trick.
    """

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.254.254.254", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def _print_all_in_one_banner(
    host: str, port: int, *, workspace_root_override: Path | None = None
) -> None:
    urls = _display_urls(host, port)
    dist = frontend_dist_dir()
    # Effective node artifact root: --workspace-root wins; then whatever
    # the config file has; then the on-disk default. We deliberately
    # don't read the config here (that's the node's job) — we just show
    # what the user asked for so the banner explains "yes, we heard you".
    effective_ws = (
        workspace_root_override if workspace_root_override is not None else default_workspace_root()
    )
    typer.echo("─" * 60)
    typer.echo("  HoloLab")
    for u in urls:
        typer.echo(f"  ↳ open {u}")
    if dist is None:
        typer.echo("  frontend    (dist not built — placeholder page will be served)")
        typer.echo("              build with: `make build-frontend`")
    else:
        typer.echo(f"  frontend    {dist}")
    typer.echo(f"  workspace   {effective_ws}")
    if workspace_root_override is not None:
        typer.echo("              (overridden via --workspace-root)")
    typer.echo("  gateway db  ~/.hololab/gateway/hololab.sqlite")
    typer.echo("  press Ctrl+C to stop")
    typer.echo("─" * 60)


def _print_gateway_summary(host: str, port: int, *, reload: bool) -> None:
    typer.echo(f"→ gateway         http://{host}:{port}    reload={'on' if reload else 'off'}")
    typer.echo(f"  api             http://{host}:{port}/api/health")
    typer.echo(f"  frontend ws     ws://{host}:{port}/ws/frontend")
    typer.echo(f"  node ws         ws://{host}:{port}/ws/node")


def _print_node_summary(cfg) -> None:  # NodeConfig
    typer.echo(f"→ node            name={cfg.node_name}  gateway={cfg.gateway_url}")
    typer.echo(f"  packs dir       {cfg.packs_dir}")
    typer.echo(f"  workspace       {cfg.workspace_root}")
    typer.echo(f"  file server     {cfg.file_server_host}:{cfg.file_server_port}")
    typer.echo(f"  envs            {', '.join(cfg.envs) if cfg.envs else '(none configured)'}")


def _print_dev_banner(host: str, port: int, frontend_url: str) -> None:
    typer.echo("─" * 60)
    typer.echo("  HoloLab dev mode")
    typer.echo(f"  gateway     http://{host}:{port}    (auto-reload on)")
    typer.echo(f"  frontend    {frontend_url}    ← open this in your browser")
    typer.echo("  start the Vite dev server in another shell:")
    typer.echo("      cd hololab/frontend && npm run dev")
    typer.echo("─" * 60)


if __name__ == "__main__":  # pragma: no cover
    app()
