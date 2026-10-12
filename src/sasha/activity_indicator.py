from __future__ import annotations

import sys
import threading

_HIDE = "\x1b[?25l"
_SHOW = "\x1b[?25h"
_RESET_COLOR = "\x1b]112\x07"


class ActivityIndicator:
    """Drives cursor visibility + OSC 12 color from a background thread
    to signal Sasha is busy, since native terminal cursor blink is
    neither rate-controllable nor reliably toggleable (confirmed by
    hand against a real terminal before this was designed — see
    docs/superpowers/specs/2026-07-21-issue-045-cursor-activity-indicator-design.md).
    """

    def __init__(self, colors: dict[str, str], interval_s: float = 0.05,
                 enabled: bool = True) -> None:
        self._colors = colors
        self._interval_s = interval_s
        self._enabled = enabled
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None

    def start(self, phase: str) -> None:
        if not self._enabled:
            return
        color = self._colors.get(phase)
        if color is None:
            return
        self.stop()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(color, self._stop_event), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=1)
        self._thread = None
        self._stop_event = None

    @staticmethod
    def restore_terminal() -> None:
        """Emit whatever undoes this indicator's terminal changes.

        Safe to call when it was never started, when `stop()` already ran, or
        when its thread is wedged — which is exactly the situation after an
        exception escaped mid-consult and left the cursor hidden. Never raises.
        """
        try:
            sys.stdout.write(_SHOW)
            sys.stdout.write(_RESET_COLOR)
            sys.stdout.flush()
        except Exception:
            pass

    def _run(self, color: str, stop_event: threading.Event) -> None:
        self._emit(f"\x1b]12;{color}\x07")
        visible = True
        try:
            while not stop_event.is_set():
                self._emit(_SHOW if visible else _HIDE)
                visible = not visible
                stop_event.wait(self._interval_s)
        finally:
            self._emit(_SHOW)
            self._emit(_RESET_COLOR)

    @staticmethod
    def _emit(seq: str) -> None:
        sys.stdout.write(seq)
        sys.stdout.flush()
