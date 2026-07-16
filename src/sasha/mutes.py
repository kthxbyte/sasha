import json
import os
import pathlib
import tempfile


class MuteStore:
    def __init__(self, path: pathlib.Path, threshold: int = 3) -> None:
        self._path = path
        self._threshold = threshold

    def is_muted(self, command: str, exit_code: int) -> bool:
        data = self._load()
        pairs = data["pairs"]
        exact_key = f"{command}:{exit_code}"
        wildcard_key = f"{command}:*"
        exact = pairs.get(exact_key)
        if exact is not None and exact["muted"]:
            return True
        wildcard = pairs.get(wildcard_key)
        if wildcard is not None and wildcard["muted"]:
            return True
        return False

    def record_ignored(self, command: str, exit_code: int) -> bool:
        key = f"{command}:{exit_code}"
        data = self._load()
        pairs = data["pairs"]
        if key not in pairs:
            pairs[key] = {"muted": False, "ignored_streak": 0}
        entry = pairs[key]
        if entry["muted"]:
            return False
        entry["ignored_streak"] += 1
        if entry["ignored_streak"] == self._threshold:
            entry["muted"] = True
            self._save(data)
            return True
        self._save(data)
        return False

    def record_acted(self, command: str, exit_code: int) -> None:
        key = f"{command}:{exit_code}"
        data = self._load()
        data["pairs"][key] = {"muted": False, "ignored_streak": 0}
        self._save(data)

    def mute(self, command: str, exit_code: int | None = None) -> None:
        key = f"{command}:{exit_code}" if exit_code is not None else f"{command}:*"
        data = self._load()
        data["pairs"][key] = {"muted": True, "ignored_streak": 0}
        self._save(data)

    def unmute(self, command: str, exit_code: int | None = None) -> None:
        data = self._load()
        pairs = data["pairs"]
        if exit_code is None:
            wildcard_key = f"{command}:*"
            pairs[wildcard_key] = {"muted": False, "ignored_streak": 0}
            prefix = f"{command}:"
            for key in list(pairs.keys()):
                if key.startswith(prefix) and key != wildcard_key:
                    pairs[key] = {"muted": False, "ignored_streak": 0}
        else:
            key = f"{command}:{exit_code}"
            pairs[key] = {"muted": False, "ignored_streak": 0}
        self._save(data)

    def muted_commands(self) -> list[str]:
        data = self._load()
        return sorted({key.rsplit(":", 1)[0]
                       for key, v in data["pairs"].items() if v["muted"]})

    def _load(self):
        try:
            text = self._path.read_text()
        except FileNotFoundError:
            return {"version": 1, "pairs": {}}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"version": 1, "pairs": {}}
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or not isinstance(data.get("pairs"), dict)
        ):
            return {"version": 1, "pairs": {}}
        for value in data["pairs"].values():
            if not isinstance(value, dict):
                return {"version": 1, "pairs": {}}
            if "muted" not in value or "ignored_streak" not in value:
                return {"version": 1, "pairs": {}}
            if not isinstance(value["muted"], bool) or not isinstance(value["ignored_streak"], int):
                return {"version": 1, "pairs": {}}
        return data

    def _save(self, data):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, separators=(",", ":"))
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".mutes-", suffix=".tmp"
        )
        try:
            os.write(fd, text.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, self._path)
