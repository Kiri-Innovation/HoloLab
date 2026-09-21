"""Regression: the per-node ▶ button must never hang silently.

Background
----------

The dispatch bridge between ``AlgorithmNode``'s Run button and the
App-level ``dispatchNode`` fetch is an event-bus rendezvous:

* ``dispatchRunNode`` creates a Promise and dispatches
  ``RUN_NODE_EVENT`` on ``window``. The event's ``detail`` carries the
  Promise's ``resolve`` and ``reject`` callbacks.
* App.tsx's ``onRun`` listener picks them up, awaits
  ``dispatchNode(wid, gnid)``, then fires the callbacks.

The failure mode this test guards against: when NO listener is
registered (App tree still mounting, StrictMode reregister races,
snapshot canvas swap unmounts App briefly) or the listener silently
fails to fire the callbacks (fetch never settles, hung gateway), the
Promise never resolves. The button in the header pins to "…" forever;
the operator has no feedback, no error, no way to retry. The user
reported this exact symptom for the ``get-index`` node on
workflow ``3711480d-…`` — the button spun, and the gateway saw zero
POST /dispatch requests.

The three defences enforced here:

1. ``RunNodeDetail.handled`` — the listener flips this to true the
   moment it accepts the event. ``dispatchRunNode`` checks it right
   after the (synchronous) ``dispatchEvent`` returns and rejects
   immediately if nothing flipped it.

2. ``DISPATCH_TIMEOUT_MS`` — a hard ceiling. Even if the listener
   accepts but the fetch never settles, the promise rejects after this
   many ms with a readable error.

3. App-level ``onRun`` wraps ``dispatchNode(...)`` in try/catch so a
   synchronous throw (URL construction, unusual fetch stack) also
   surfaces as a rejection instead of leaving the promise dangling.

If any of these regress, the "silent hang" symptom returns. The tests
are source-level greps — cheap, deterministic, and independent of the
running gateway.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ALGORITHM_NODE_TSX = REPO_ROOT / "hololab" / "frontend" / "src" / "canvas" / "AlgorithmNode.tsx"
APP_TSX = REPO_ROOT / "hololab" / "frontend" / "src" / "App.tsx"


def test_dispatch_run_node_detects_missing_listener() -> None:
    """No listener ⇒ reject immediately with a readable message.

    Without this check the button hangs in "…" forever because
    ``resolve``/``reject`` are never called. The regression the user
    hit: get-index's Run button pinned to "…", zero POSTs to the
    gateway.
    """

    body = ALGORITHM_NODE_TSX.read_text(encoding="utf-8")
    # The detail carries an ack flag.
    assert re.search(r"handled\?\s*:\s*boolean", body), (
        "RunNodeDetail must declare an optional ``handled`` flag so the "
        "dispatcher can distinguish 'no listener' from 'listener took over "
        "but the fetch is still pending'."
    )
    # The dispatcher checks it and rejects with a user-facing message.
    assert "detail.handled" in body, (
        "dispatchRunNode should inspect detail.handled after dispatchEvent"
    )
    assert "no run handler registered" in body, (
        "dispatchRunNode should reject with a readable 'no run handler "
        "registered' message when no listener flipped the ack flag — "
        "silent hangs are forbidden."
    )


def test_dispatch_run_node_has_timeout_fallback() -> None:
    """Hung fetch ⇒ reject after ``DISPATCH_TIMEOUT_MS``.

    Second-line defence. If the listener accepts the event but the
    resolve/reject never fires (fetch stuck, gateway unresponsive,
    listener bug that forgets to invoke callbacks), the timeout
    guarantees the user gets a readable error rather than a spinning
    button.
    """

    body = ALGORITHM_NODE_TSX.read_text(encoding="utf-8")
    assert "DISPATCH_TIMEOUT_MS" in body, (
        "dispatchRunNode must define a hard timeout ceiling constant"
    )
    assert "setTimeout" in body and "dispatch timed out" in body, (
        "dispatchRunNode must reject with a 'dispatch timed out' Error after DISPATCH_TIMEOUT_MS"
    )


def test_app_onrun_marks_handled_and_guards_dispatch() -> None:
    """App-level onRun must (a) ack the event, (b) survive a sync throw."""

    body = APP_TSX.read_text(encoding="utf-8")
    # Anchor to the run-node effect so we don't match some unrelated
    # ``.handled = true`` in the file. The onRun handler is unique.
    m = re.search(
        r"const onRun = \(evt: Event\) => \{(?P<h>.*?)\};\s*"
        r"window\.addEventListener\(RUN_NODE_EVENT,",
        body,
        re.DOTALL,
    )
    assert m, "couldn't locate the onRun handler for RUN_NODE_EVENT"
    handler = m.group("h")
    assert "detail.handled" in handler and "= true" in handler, (
        "onRun must flip detail.handled = true so dispatchRunNode's "
        "no-listener guard doesn't false-trigger."
    )
    assert "try" in handler and "catch" in handler and "dispatchNode(" in handler, (
        "onRun must wrap dispatchNode(...) in try/catch so a synchronous "
        "throw is surfaced as a rejection instead of leaving the caller's "
        "promise dangling."
    )
