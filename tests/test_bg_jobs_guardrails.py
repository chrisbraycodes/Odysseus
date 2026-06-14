import time

from src import bg_jobs


def test_launch_blocks_recent_identical_failure(tmp_path, monkeypatch):
    store = tmp_path / "bg_jobs.json"
    monkeypatch.setattr(bg_jobs, "_STORE", store)
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(bg_jobs, "_FAIL_COOLDOWN_S", 300)

    jobs = {
        "old": {
            "id": "old",
            "session_id": "s1",
            "command": "npm start",
            "status": "failed",
            "cwd": "/workspace",
            "ended_at": time.time() - 10,
            "followed_up": True,
        }
    }
    store.write_text(__import__("json").dumps(jobs), encoding="utf-8")

    rec = bg_jobs.launch("npm start", session_id="s2", cwd="/workspace")
    assert rec.get("blocked") is True


def test_cancel_running_marks_followed_up(tmp_path, monkeypatch):
    store = tmp_path / "bg_jobs.json"
    monkeypatch.setattr(bg_jobs, "_STORE", store)
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(bg_jobs, "_kill", lambda _pid: None)
    monkeypatch.setattr(bg_jobs, "_pid_alive", lambda _pid: True)

    jobs = {
        "run1": {
            "id": "run1",
            "session_id": "s1",
            "command": "npm start",
            "status": "running",
            "pid": 99999,
            "cwd": "/workspace",
            "started_at": time.time(),
            "followed_up": False,
            "log_path": str(tmp_path / "jobs" / "run1.log"),
            "exit_path": str(tmp_path / "jobs" / "run1.exit"),
        }
    }
    store.write_text(__import__("json").dumps(jobs), encoding="utf-8")

    cancelled = bg_jobs.cancel_running(command="npm start", cwd="/workspace")
    assert cancelled == ["run1"]
    updated = bg_jobs._load()["run1"]
    assert updated["status"] == "failed"
    assert updated.get("cancelled") is True
    assert updated.get("followed_up") is True
