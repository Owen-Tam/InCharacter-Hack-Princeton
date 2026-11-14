from companion import awake


class FakeProcess:
    def __init__(self, args, **kwargs):
        self.args = args
        self.terminated = False
        FakeProcess.last = self

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_runs_caffeinate_on_macos_and_stops_it_after(monkeypatch):
    monkeypatch.setattr(awake.sys, "platform", "darwin")
    monkeypatch.setattr(awake.shutil, "which", lambda name: "/usr/bin/caffeinate")
    monkeypatch.setattr(awake.subprocess, "Popen", FakeProcess)
    with awake.keep_awake() as on:
        assert on is True
        assert FakeProcess.last.args == ["/usr/bin/caffeinate", "-dims"]
        assert not FakeProcess.last.terminated
    assert FakeProcess.last.terminated


def test_stops_caffeinate_even_when_the_import_fails(monkeypatch):
    monkeypatch.setattr(awake.sys, "platform", "darwin")
    monkeypatch.setattr(awake.shutil, "which", lambda name: "/usr/bin/caffeinate")
    monkeypatch.setattr(awake.subprocess, "Popen", FakeProcess)
    try:
        with awake.keep_awake():
            raise RuntimeError("import failed")
    except RuntimeError:
        pass
    assert FakeProcess.last.terminated


def test_does_nothing_elsewhere_or_without_the_tool(monkeypatch):
    monkeypatch.setattr(awake.sys, "platform", "linux")
    with awake.keep_awake() as on:
        assert on is False
    monkeypatch.setattr(awake.sys, "platform", "darwin")
    monkeypatch.setattr(awake.shutil, "which", lambda name: None)
    with awake.keep_awake() as on:
        assert on is False

    def broken(*args, **kwargs):
        raise OSError("no")

    monkeypatch.setattr(awake.shutil, "which", lambda name: "/usr/bin/caffeinate")
    monkeypatch.setattr(awake.subprocess, "Popen", broken)
    with awake.keep_awake() as on:
        assert on is False
