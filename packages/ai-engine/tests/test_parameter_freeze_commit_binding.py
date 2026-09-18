"""Regression tests for source-bound validation freezes."""

import json

from scanner import parameter_freeze


def test_freeze_refuses_without_authoritative_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(parameter_freeze, "_current_commit_sha", lambda: "")
    monkeypatch.setattr(parameter_freeze, "_FREEZE_PATH", tmp_path / "parameter_freeze.json")
    monkeypatch.setattr(parameter_freeze, "_snapshot_parameters", lambda: {"ema": {"fast": 20}})

    result = parameter_freeze.ParameterFreeze().freeze()

    assert result["frozen"] is False
    assert "commit" in result["reason"].lower()
    assert not (tmp_path / "parameter_freeze.json").exists()


def test_check_rejects_legacy_freeze_without_commit(monkeypatch, tmp_path):
    freeze_path = tmp_path / "parameter_freeze.json"
    freeze_path.write_text(json.dumps({
        "frozen": True,
        "param_hash": "abc",
        "snapshot": {"ema": {"fast": 20}},
    }))
    monkeypatch.setattr(parameter_freeze, "_FREEZE_PATH", freeze_path)
    monkeypatch.setattr(parameter_freeze, "_current_commit_sha", lambda: "new-sha")
    monkeypatch.setattr(parameter_freeze, "_snapshot_parameters", lambda: {"ema": {"fast": 20}})

    result = parameter_freeze.ParameterFreeze().check()

    assert result["clean"] is False
    assert "source commit" in result["reason"].lower()


def test_check_rejects_commit_drift(monkeypatch, tmp_path):
    freeze_path = tmp_path / "parameter_freeze.json"
    freeze_path.write_text(json.dumps({
        "frozen": True,
        "code_commit_sha": "frozen-sha",
        "param_hash": "abc",
        "snapshot": {"ema": {"fast": 20}},
    }))
    monkeypatch.setattr(parameter_freeze, "_FREEZE_PATH", freeze_path)
    monkeypatch.setattr(parameter_freeze, "_current_commit_sha", lambda: "current-sha")

    result = parameter_freeze.ParameterFreeze().check()

    assert result["clean"] is False
    assert result["frozen_commit"] == "frozen-sha"
    assert result["current_commit"] == "current-sha"


def test_freeze_records_and_accepts_matching_commit(monkeypatch, tmp_path):
    freeze_path = tmp_path / "parameter_freeze.json"
    monkeypatch.setattr(parameter_freeze, "_FREEZE_PATH", freeze_path)
    monkeypatch.setattr(parameter_freeze, "_current_commit_sha", lambda: "commit-123")
    monkeypatch.setattr(parameter_freeze, "_snapshot_parameters", lambda: {"ema": {"fast": 20}})

    manager = parameter_freeze.ParameterFreeze()
    frozen = manager.freeze()
    checked = manager.check()

    assert frozen["frozen"] is True
    assert frozen["code_commit_sha"] == "commit-123"
    assert checked["clean"] is True
    assert checked["code_commit_sha"] == "commit-123"


def test_parameter_freeze_hash_uses_complete_sha256(monkeypatch, tmp_path):
    freeze_path = tmp_path / "parameter_freeze.json"
    monkeypatch.setattr(parameter_freeze, "_FREEZE_PATH", freeze_path)
    monkeypatch.setattr(parameter_freeze, "_current_commit_sha", lambda: "commit-456")
    monkeypatch.setattr(parameter_freeze, "_snapshot_parameters", lambda: {"ema": {"fast": 20}})

    frozen = parameter_freeze.ParameterFreeze().freeze()

    assert len(frozen["param_hash"]) == 64
    assert frozen["param_hash"] == parameter_freeze._compute_hash({"ema": {"fast": 20}})



def test_current_commit_uses_git_head_outside_ci(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GITHUB_SHA", "attacker-controlled")
    fake = type("Result", (), {"stdout": "real-git-head\n"})()
    monkeypatch.setattr(parameter_freeze.subprocess, "run", lambda *args, **kwargs: fake)
    assert parameter_freeze._current_commit_sha() == "real-git-head"


def test_current_commit_requires_ci_sha_to_match_git_head(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SHA", "wrong-sha")
    fake = type("Result", (), {"stdout": "real-git-head\n"})()
    monkeypatch.setattr(parameter_freeze.subprocess, "run", lambda *args, **kwargs: fake)
    assert parameter_freeze._current_commit_sha() == ""


def test_current_commit_accepts_matching_ci_sha(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SHA", "real-git-head")
    fake = type("Result", (), {"stdout": "real-git-head\n"})()
    monkeypatch.setattr(parameter_freeze.subprocess, "run", lambda *args, **kwargs: fake)
    assert parameter_freeze._current_commit_sha() == "real-git-head"
