#!/usr/bin/env python3
"""The glob-deciding probe: find files by NAME in a deep tree.

The open question after the tooling review is whether Sasha needs a filename-glob
tool. Capability is not in doubt -- `find` is on the auto-approve allowlist, so
`run_command` can do it with no prompt. What is in doubt is whether the model
reaches for that, or flails with list_dir/read_file instead.

So: a tree ~5 levels deep with ~45 files, four of which are named settings.py,
and a task whose locations are discoverable only by name. If the model issues one
`find`/`grep -r` and finishes, glob earns nothing. If it wanders and misses some,
glob earns its place.

    PYTHONPATH=src python3 docs/analysis/probes/probe_glob_task.py
"""
from __future__ import annotations

import dataclasses
import os
import pathlib
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ.setdefault("XDG_STATE_HOME", tempfile.mkdtemp())
sys.path.insert(0, "src")

from sasha.agent import run_consult                      # noqa: E402
from sasha.config import Profile                        # noqa: E402
from sasha.executor import Executor                     # noqa: E402
from sasha.llm import ChatClient                        # noqa: E402
from sasha.session import Session                       # noqa: E402
import sasha.tools as T                                 # noqa: E402

# Where the four settings.py files live, and which one already has TIMEOUT.
SETTINGS = [
    "project/app/core/settings.py",
    "project/app/services/billing/settings.py",
    "project/app/services/auth/settings.py",
    "project/app/services/notify/settings.py",
]
ALREADY_SET = SETTINGS[0]

TASK = (
    "Several modules in this project are named settings.py. Add a constant "
    "`TIMEOUT = 30` to EVERY settings.py that does not already define it. "
    "Then run the test suite to confirm, and tell me which files you changed."
)

CALLS: list[tuple[str, str, str]] = []


def _spy() -> None:
    for name in list(T.REGISTRY):
        tool = T.REGISTRY[name]
        original = tool.handler

        def make(n, o):
            def wrapped(args, ex):
                result = o(args, ex)
                CALLS.append((n, str(args.get("path", args.get("command", "")))[:60],
                              result.text[:50].replace("\n", " ")))
                return result
            return wrapped

        T.REGISTRY[name] = dataclasses.replace(tool, handler=make(name, original))


class AutoConfirm:
    def ask(self, summary, description):
        return ("yes", "")


def build_tree(root: pathlib.Path) -> None:
    """~45 files, five levels, with decoys whose names are close but not equal."""
    files = {
        "project/README.md": "# Project\n",
        "project/pyproject.toml": "[project]\nname='demo'\n",
        "project/tests/test_settings_contract.py": (
            "import pathlib, sys\n\n\n"
            "def test_every_settings_module_defines_timeout():\n"
            "    root = pathlib.Path(__file__).resolve().parents[1]\n"
            "    found = sorted(root.rglob('settings.py'))\n"
            "    assert found, 'no settings modules found'\n"
            "    for path in found:\n"
            "        text = path.read_text()\n"
            "        assert 'TIMEOUT = 30' in text, f'{path} lacks TIMEOUT'\n"),
    }
    packages = ["app", "app/core", "app/services", "app/services/billing",
                "app/services/auth", "app/services/notify",
                "app/services/notify/templates", "app/web",
                "app/web/middleware", "tests",
                "project", "project/app", "project/tests"]
    for package in packages:
        files[f"{package}/__init__.py"] = ""
    for relative in SETTINGS:
        body = "# settings\n"
        if relative == ALREADY_SET:
            body += "TIMEOUT = 30\n"
        files[relative] = body
    # decoys: same directory, adjacent names
    for decoy in ("config.py", "defaults.py", "settings_base.py",
                  "local_settings.py.bak"):
        files[f"project/app/core/{decoy}"] = "# not a settings module\n"
    # bulk, so the tree is genuinely too big to list by hand
    for index in range(30):
        files[f"project/app/services/billing/module_{index:02d}.py"] = (
            f"VALUE_{index} = {index}\n")
    for index in range(8):
        files[f"project/app/web/middleware/handler_{index:02d}.py"] = "# handler\n"
    for relative, body in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def main() -> int:
    _spy()
    workdir = pathlib.Path(tempfile.mkdtemp()) / "work"
    build_tree(workdir)
    total = sum(1 for _ in workdir.rglob("*") if _.is_file())
    profile = Profile(name="local", base_url="http://192.168.8.253:11434/v1",
                      model="local", api_key="", api_key_required=False)
    session = Session()
    session.add_user(TASK)
    print(f"  workdir: {workdir}   files: {total}   depth: 5")
    print(f"  task: {TASK[:76]}...\n")
    started = time.time()
    try:
        run_consult(session, ChatClient(profile, timeout_s=200.0,
                                        max_think_seconds=180),
                    Executor(str(workdir), {}), AutoConfirm(),
                    out=lambda t: None, max_tool_turns=50)
    except Exception as exc:                       # noqa: BLE001
        print(f"  !! the loop raised {type(exc).__name__}: {str(exc)[:70]}")
    elapsed = time.time() - started

    orientation = sum(1 for n, _, _ in CALLS if n in ("list_dir", "read_file",
                                                      "search_files"))
    used_find = any("find" in t for _, t, _ in CALLS)
    print(f"  elapsed: {elapsed:.0f}s   tool calls: {len(CALLS)}   "
          f"orientation calls: {orientation}   used find/grep-by-name: {used_find}")
    for name, target, result in CALLS:
        print(f"    {name:12} {target:62} -> {result}")

    print()
    missing = []
    for relative in SETTINGS:
        text = (workdir / relative).read_text()
        ok = "TIMEOUT = 30" in text
        print(f"  {relative:46} TIMEOUT set: {ok}")
        if not ok:
            missing.append(relative)
    suite = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                           cwd=workdir, capture_output=True, text=True, timeout=180)
    print(f"\n  pytest (run by the probe): rc={suite.returncode}  "
          f"{(suite.stdout.strip().splitlines() or [''])[-1]}")
    print(f"  VERDICT: {'all four found and set' if not missing else 'MISSED ' + str(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
