#!/usr/bin/env python3
"""Foreground-group keystroke relay helper (ISSUE-040, Task 24.1).

Invoked by Executor.run()'s wrapped bash script as:
    python3 _kb_relay_helper.py <err_master_fd> [<out_master_fd>]

Runs in the same process group as the interactive command it accompanies
(started via bash '&', which does not create a new pgrp for a
non-interactive script), so it can read fd 0 where Sasha's own
(background, post-tcsetpgrp) process cannot. See
docs/superpowers/specs/2026-07-21-issue-040-foreground-relay-helper-design.md
for the full design and rationale.
"""

import os
import select
import signal
import sys
import termios

signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTSTP, signal.SIG_IGN)

_ORIGINAL_PPID = os.getppid()


def _is_raw(fd: int) -> bool | None:
    try:
        attrs = termios.tcgetattr(fd)
    except (OSError, termios.error):
        return None
    return not (attrs[3] & termios.ICANON)


def main() -> None:
    master_fds = [int(a) for a in sys.argv[1:]]
    triggered: set[int] = set()
    fd0_added = False

    while master_fds:
        if os.getppid() != _ORIGINAL_PPID:
            return

        for fd in list(master_fds):
            if fd in triggered:
                continue
            raw = _is_raw(fd)
            if raw is None:
                master_fds.remove(fd)
                triggered.discard(fd)
                continue
            if raw:
                triggered.add(fd)
                if not fd0_added:
                    try:
                        termios.tcsetattr(0, termios.TCSANOW,
                                           termios.tcgetattr(fd))
                    except (OSError, termios.error):
                        pass
                    fd0_added = True

        if not master_fds:
            return

        watch = [0] if fd0_added else []
        try:
            r, _, _ = select.select(watch, [], [], 0.05)
        except OSError:
            return

        if 0 in r:
            try:
                chunk = os.read(0, 4096)
            except OSError:
                return
            if not chunk:
                return
            for fd in list(triggered):
                try:
                    os.write(fd, chunk)
                except (OSError, BlockingIOError):
                    pass


if __name__ == "__main__":
    main()
