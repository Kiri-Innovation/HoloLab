import subprocess
import sys

import pytest

from hololab.file_lock import lock_exclusive


def test_exclusive_lock_serializes_two_processes(tmp_path):
    path = tmp_path / "lock"
    contender = None
    try:
        with path.open("a") as owner:
            lock_exclusive(owner)
            contender = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import sys\nfrom hololab.file_lock import lock_exclusive\n"
                    "with open(sys.argv[1], 'a') as stream:\n"
                    "    print('ready', flush=True)\n"
                    "    lock_exclusive(stream)\n",
                    str(path),
                ],
                stdout=subprocess.PIPE,
                text=True,
            )
            assert contender.stdout is not None
            assert contender.stdout.readline() == "ready\n"
            with pytest.raises(subprocess.TimeoutExpired):
                contender.wait(timeout=0.2)
        assert contender.wait(timeout=10) == 0
    finally:
        if contender is not None:
            if contender.poll() is None:
                contender.kill()
            contender.communicate(timeout=10)


def test_install_cli_import_keeps_gateway_app_lazy():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import hololab.cli; assert 'hololab.gateway.app' not in sys.modules",
        ],
        check=True,
    )
