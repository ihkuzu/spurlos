import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    # keep tests away from a real ~/.spurlos
    monkeypatch.setenv("SPURLOS_HOME", str(tmp_path / "home"))
