from hololab.node.file_tunnel import _terminal_tunnel_auth_status


def test_terminal_tunnel_auth_statuses_do_not_retry() -> None:
    assert _terminal_tunnel_auth_status(401)
    assert _terminal_tunnel_auth_status(403)
    assert not _terminal_tunnel_auth_status(429)
    assert not _terminal_tunnel_auth_status(503)
