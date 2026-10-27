"""Internal helper: stop a process group when the owning app's pipe closes.

Run by absolute filename with isolated system Python. It deliberately shares
its process group with the command, so even grandchildren are cleaned up.
"""

import contextlib
import os
import selectors
import signal
import subprocess
import sys


def supervise(lifeline, arguments):
    if os.getpgrp() != os.getpid():
        raise RuntimeError("The command supervisor requires its own process group")
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    process = None
    try:
        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL)
        with selectors.DefaultSelector() as selector:
            selector.register(lifeline, selectors.EVENT_READ)
            while not stopping and process.poll() is None:
                for _key, _events in selector.select(timeout=0.1):
                    if not os.read(lifeline, 1):
                        stopping = True
    finally:
        os.close(lifeline)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        os.killpg(os.getpid(), signal.SIGTERM)
        if process:
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2)
        # Include this supervisor and every remaining member of the command group.
        os.killpg(os.getpid(), signal.SIGKILL)


if __name__ == "__main__":
    supervise(int(sys.argv[1]), sys.argv[2:])
