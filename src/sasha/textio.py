"""Text-handling policy in one place.

Two rules the whole codebase needs, both found by probing rather than reading
(see scratchpad/string-handling-findings.md for the reproductions):

**1. File and stream text I/O is always UTF-8, explicitly.**
Never rely on ``locale.getpreferredencoding()``. Under ``PYTHONUTF8=0`` or a
non-UTF-8, non-C locale that resolves to ASCII or latin-1, so writing an
accented character raises and reading a UTF-8 file mangles it. Plain
``LC_ALL=C`` is safe (Python auto-enables UTF-8 mode, PEP 540) which is exactly
why this is easy to miss.

**2. Anything on its way to the screen must go through :func:`printable`.**
A ``str`` on Linux may contain *surrogates* (U+DC80-U+DCFF). Python uses
``surrogateescape`` to represent filename bytes that are not valid UTF-8, so any
path that came from the filesystem can hold one -- and so can any string from a
JSON payload containing a lone ``\\udXXX`` escape, which is legal JSON and
therefore reachable from model output. Such a ``str`` cannot be encoded to
UTF-8 strictly, and ``sys.stdout.errors`` is ``'strict'``, so printing it
RAISES. A surrogate is not exotic; it is one `cd` away.
"""

from __future__ import annotations


def printable(text: str) -> str:
    """A str safe to write to a UTF-8 stream: surrogates become U+FFFD.

    FOR DISPLAY ONLY. Keep the original str for filesystem work -- round-tripping
    through surrogateescape is precisely how ``os`` represents path bytes that
    are not valid UTF-8, so converting a path early would break it.

    Normal text is returned unchanged; the common case costs one failed encode.
    The substitution is done on the *str* rather than by encoding and decoding
    again, because both byte-level routes are wrong: ``surrogateescape`` cannot
    encode a lone ``\\ud800`` at all (it only covers U+DC80-U+DCFF), and
    ``surrogatepass`` turns one surrogate into three bytes that then decode to
    three U+FFFD. Surrogates are the only code points that fail UTF-8, so the
    per-character test below is exact as well as simple.
    """
    if not text:
        return text
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return "".join(
            "\ufffd" if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in text)
    return text
