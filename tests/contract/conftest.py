import pytest


@pytest.fixture(autouse=True)
def _isolated_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
