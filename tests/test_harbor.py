import json
import shutil
import subprocess
from pathlib import Path

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


def test_existing_variant_keys_are_unchanged_by_new_catalog_fields():
    import hashlib

    from everyeval.catalog import get_benchmark

    entry = get_benchmark("terminal-bench")
    identity = {k: getattr(entry, k) for k in ("id", "version", "adapter", "task", "task_args", "grader")}
    expected = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    assert entry.variant_key == expected
    retail, banking = get_benchmark("tau3-retail"), get_benchmark("tau3-banking")
    assert retail.task == banking.task and retail.variant_key != banking.variant_key


def test_task_filter_env_and_agent_import_root_reach_harbor(tmp_path, monkeypatch):
    from everyeval.harbor_adapter import harbor_env

    monkeypatch.setattr(shutil, "which", lambda *a, **k: "/bin/harbor")
    cmd = harbor_command(
        "sierra-research/tau3-bench", "pkg.mod:Agent", "openai/gpt-5-mini", tmp_path, 3, 1, ["*retail-*"]
    )
    assert cmd[cmd.index("-a") + 1] == "pkg.mod:Agent" and cmd[cmd.index("-i") + 1] == "*retail-*"
    assert cmd.index("-i") < cmd.index("-l")  # Harbor applies the limit after filters
    monkeypatch.setenv("PYTHONPATH", "/existing")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1")
    env = harbor_env({"TAU2_USER_MODEL": "gpt-5-mini"}, "/repo", {"OPENAI_BASE_URL": "x", "OTHER": "d"})
    assert env["TAU2_USER_MODEL"] == "gpt-5-mini" and env["PYTHONPATH"].split(":") == ["/repo", "/existing"]
    assert (env["OPENAI_BASE_URL"], env["OTHER"]) == ("https://proxy.example/v1", "d")  # defaults never override


def test_environment_cost_and_benchmark_revision(tmp_path):
    metadata = {
        "environment_cost_usd": 0.005,
        "benchmark_revision": "tau2-bench@4ce7c0397c1e",
        "stop_reason": "user_stop",
        "guard_blocks": {"confirm_first": 2},
    }
    result = {**RESULT, "agent_result": {**RESULT["agent_result"], "metadata": metadata}}
    c = convert_trial(make_trial(tmp_path, result), "run_x")
    assert c["cost_usd"] == pytest.approx(0.025) and c["benchmark_revision"] == "tau2-bench@4ce7c0397c1e"
    root = c["spans"][0]["attributes"]
    assert (root["agent_cost_usd"], root["environment_cost_usd"]) == (0.02, 0.005)
    assert root["agent.stop_reason"] == "user_stop" and json.loads(root["agent.guard_blocks"]) == {"confirm_first": 2}


def test_agents_reporting_tokens_only_are_priced_from_the_price_list(tmp_path, everyeval_home):
    everyeval_home.mkdir(parents=True, exist_ok=True)
    (everyeval_home / "pricing.yaml").write_text("openai/gpt-x: {input: 1.0, output: 10.0}\n")
    tokens_only = {"n_input_tokens": 1_000_000, "n_output_tokens": 100_000, "metadata": {"environment_cost_usd": 0.5}}
    c = convert_trial(make_trial(tmp_path, {**RESULT, "agent_result": tokens_only}), "run_x", model="openai/gpt-x")
    assert c["spans"][0]["attributes"]["agent_cost_usd"] == pytest.approx(2.0) and c["cost_usd"] == pytest.approx(2.5)
    reported = convert_trial(make_trial(tmp_path / "b"), "run_x", model="openai/gpt-x")
    assert reported["cost_usd"] == 0.02  # an agent's own cost report wins


def test_runs_on_different_benchmark_code_are_blocked():
    from everyeval.compare import check_compatibility
    from everyeval.db import Run

    def run(rev):
        return Run(
            id=f"run_{rev}",
            benchmark="tau3-retail@1",
            variant_key="k",
            model="m",
            epochs=1,
            status="succeeded",
            resolved_models=[],
            manifest={"harbor": {"benchmark_revisions": {"t1": rev}}},
        )

    keys = {("t1", 1)}
    assert check_compatibility(run("a"), run("a"), keys, keys).comparable
    blocked = check_compatibility(run("a"), run("b"), keys, keys)
    assert not blocked.comparable and "Benchmark code differs" in blocked.blocking[0]


def test_custom_agent_is_validated_before_running(tmp_path):
    from everyeval.catalog import get_benchmark
    from everyeval.service import PlanError, create_runs

    (tmp_path / "myagents").mkdir()
    (tmp_path / "myagents" / "support.py").write_text("class Agent: ...\n")
    retail = get_benchmark("tau3-retail")
    with pytest.raises(PlanError, match="no default agent"):
        create_runs(retail, ["openai/gpt-5-mini"])
    with pytest.raises(PlanError, match="import path like"):
        create_runs(retail, ["openai/gpt-5-mini"], agent="myagents/support.py", agent_path=str(tmp_path))
    with pytest.raises(PlanError, match="Cannot find module"):
        create_runs(retail, ["openai/gpt-5-mini"], agent="nope.agents:Agent", agent_path=str(tmp_path))
    with pytest.raises(PlanError, match="does not accept a custom agent"):
        create_runs(get_benchmark("gsm8k"), ["openai/gpt-5-mini"], agent="myagents.support:Agent")
    (run,) = create_runs(retail, ["openai/gpt-5-mini"], agent="myagents.support:Agent", agent_path=str(tmp_path))
    assert run.agent == "myagents.support:Agent" and run.manifest["agent_path"] == str(tmp_path)
    oracle = create_runs(retail, ["oracle"])  # free pipeline check, no agent needed
    assert oracle[0].agent is None


def test_dataset_patches_apply_to_every_task_or_fail(tmp_path, monkeypatch):
    from everyeval import harbor_adapter
    from everyeval.catalog import TaskPatch

    def fake_download(cmd, **kwargs):
        out = Path(cmd[cmd.index("-o") + 1]) / "ds"
        for name, line in (("t1", "git clone X"), ("t2", "git clone X" if "ok" in cmd[2] else "curl X")):
            (out / name / "environment" / "sub").mkdir(parents=True)
            (out / name / "task.toml").write_text("")
            (out / name / "environment" / "Dockerfile").write_text(line)
            (out / name / "environment" / "sub" / "Dockerfile").write_text("git clone X")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(harbor_adapter, "_harbor_exe", lambda: "harbor")
    monkeypatch.setattr(harbor_adapter.subprocess, "run", fake_download)
    pin = [TaskPatch(files="environment/**/Dockerfile", old="git clone X", new="git clone --branch v1 X")]
    dest = harbor_adapter.prepare_patched_dataset("org/ok", pin, tmp_path / "ok")
    assert sorted(p.read_text() for p in dest.rglob("Dockerfile")) == ["git clone --branch v1 X"] * 4
    cached = harbor_adapter.patched_dataset_dir(tmp_path, "org/ok__v2", pin).name
    assert cached.startswith("org--ok--v2-") and "__" not in cached
    with pytest.raises(RuntimeError, match="did not apply to t2"):
        harbor_adapter.prepare_patched_dataset("org/changed", pin, tmp_path / "changed")
