import json
import shutil
import subprocess

import pytest

from everyeval.harbor_adapter import convert_trial, harbor_command, split_model

RESULT = {
    "id": "trial-uuid",
    "task_name": "org/fix-bug",
    "trial_name": "fix-bug__abc",
    "task_checksum": "c1",
    "agent_info": {"name": "terminus-2", "version": "2.0", "model_info": {"name": "gpt-4o", "provider": "openai"}},
    "agent_result": {"n_input_tokens": 1200, "n_output_tokens": 300, "cost_usd": 0.02},
    "verifier_result": {"rewards": {"reward": 0.0}},
    "exception_info": None,
    "started_at": "2026-10-07T05:00:00Z",
    "finished_at": "2026-10-07T05:01:00Z",
    "environment_setup": {"started_at": "2026-10-07T05:00:00Z", "finished_at": "2026-10-07T05:00:10Z"},
    "agent_execution": {"started_at": "2026-10-07T05:00:10Z", "finished_at": "2026-10-07T05:00:50Z"},
    "verifier": {"started_at": "2026-10-07T05:00:50Z", "finished_at": "2026-10-07T05:01:00Z"},
}
TRAJECTORY = {
    "schema_version": "ATIF-v1.7",
    "agent": {"name": "terminus-2", "model_name": "openai/gpt-4o"},
    "steps": [
        {"step_id": 1, "source": "user", "message": "Fix the failing test in /app."},
        {
            "step_id": 2,
            "source": "agent",
            "message": "Running tests first.",
            "tool_calls": [{"tool_call_id": "c1", "function_name": "bash", "arguments": {"cmd": "pytest"}}],
            "observation": {"results": [{"source_call_id": "c1", "content": "1 failed"}]},
            "metrics": {"prompt_tokens": 800, "completion_tokens": 100},
        },
        {"step_id": 3, "source": "agent", "message": "Done, I patched utils.py."},
    ],
}


def make_trial(tmp_path, result=RESULT, trajectory=TRAJECTORY):
    trial = tmp_path / "job" / "fix-bug__abc"
    (trial / "agent").mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps(result))
    if trajectory:
        (trial / "agent" / "trajectory.json").write_text(json.dumps(trajectory))
    return trial


def test_atif_trajectory_becomes_model_and_tool_spans(tmp_path):
    c = convert_trial(make_trial(tmp_path), "run_x")
    r = c["result"]
    assert (r["sample_id"], r["outcome"], r["score"]) == ("org/fix-bug", "incorrect", 0.0)
    assert r["input"] == "Fix the failing test in /app." and r["output"] == "Done, I patched utils.py."
    assert (r["input_tokens"], r["output_tokens"]) == (1200, 300)
    kinds = [s["kind"] for s in c["spans"]]
    assert kinds.count("model") == 2 and kinds.count("tool") == 1 and "scorer" in kinds
    tool = next(s for s in c["spans"] if s["kind"] == "tool")
    model = next(s for s in c["spans"] if s["span_id"] == tool["parent_id"])
    assert tool["content"] == {"arguments": {"cmd": "pytest"}, "result": "1 failed"}
    assert model["attributes"]["gen_ai.usage.input_tokens"] == 800
    assert c["resolved_models"] == {"openai/gpt-4o"} and c["cost_usd"] == 0.02


def test_exceptions_are_errors_not_wrong_answers(tmp_path):
    result = {
        **RESULT,
        "verifier_result": None,
        "exception_info": {"exception_type": "AgentTimeoutError", "exception_message": "timed out"},
    }
    c = convert_trial(make_trial(tmp_path, result, None), "run_x")
    assert c["result"]["outcome"] == "error" and "AgentTimeoutError" in c["result"]["error"]
    assert any(s["kind"] == "error" for s in c["spans"])


def test_model_syntax():
    assert split_model("terminus-2:openai/gpt-4o", "x") == ("terminus-2", "openai/gpt-4o")
    assert split_model("openai/gpt-4o", "terminus-2") == ("terminus-2", "openai/gpt-4o")
    assert split_model("oracle", None) == ("oracle", None)
    with pytest.raises(ValueError):
        split_model("openai/gpt-4o", None)


def docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def harbor_installed() -> bool:
    import sys
    from pathlib import Path

    return shutil.which("harbor", path=str(Path(sys.executable).parent)) is not None


@pytest.mark.skipif(not (docker_ready() and harbor_installed()), reason="needs Docker and the harbor extra")
def test_harbor_smoke_oracle_vs_nop():
    from everyeval.catalog import get_benchmark
    from everyeval.compare import compare_runs
    from everyeval.execution import execute_run
    from everyeval.service import create_runs

    oracle, nop = create_runs(get_benchmark("harbor-smoke"), ["oracle", "nop"])
    a, b = execute_run(oracle.id), execute_run(nop.id)
    assert (a.status, a.n_correct, b.status, b.n_correct) == ("succeeded", 2, "succeeded", 0)
    assert len(a.manifest["harbor"]["task_checksums"]) == 2
    result = compare_runs(a.id, b.id)
    assert result.compatibility.comparable and result.discordant["regressions"] == 2


@pytest.mark.skipif(not harbor_installed(), reason="needs the harbor extra")
def test_harbor_command_for_registry_dataset(tmp_path):
    cmd = harbor_command("swe-bench/swe-bench-verified", "mini-swe-agent", "openai/gpt-4o", tmp_path, 10, 1)
    assert cmd[1:4] == ["run", "-d", "swe-bench/swe-bench-verified"]
    assert ["-m", "openai/gpt-4o"] == cmd[cmd.index("-m") : cmd.index("-m") + 2] and "-l" in cmd
