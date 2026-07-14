import sys


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if "--version" in argv:
        print("sasha 0.1.0")
        return 0
    print("[sasha] not wired yet — REPL lands in Task 3.2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
