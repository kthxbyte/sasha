MAX_BURST_LINES = 1000


def read_joined_input(prompt: str, read_line, has_pending) -> str:
    lines = [read_line(prompt)]
    while len(lines) < MAX_BURST_LINES:
        if not has_pending():
            break
        try:
            lines.append(read_line(""))
        except EOFError:
            break
    return "\n".join(lines)
