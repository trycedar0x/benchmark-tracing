"""Historical finance snapshot comparison remains strict."""

from historical import historical_run
from inspect_ai.event import ModelEvent
from inspect_ai.log import EvalSample
from inspect_ai.model import GenerateConfig, ModelOutput

from everybench.compare import check_compatibility
from everybench.inspect_convert import convert_sample


def test_imported_judge_role_does_not_look_like_evaluated_model_revision_drift():
    def event(model, role=None):
        return ModelEvent(
            model=model,
            role=role,
            input=[],
            tools=[],
            tool_choice="none",
            config=GenerateConfig(),
            output=ModelOutput.from_content(model, "4"),
        )

    sample = EvalSample(
        id="historical",
        epoch=1,
        input="What is 2+2?",
        target="4",
        metadata={},
        scores={"match": {"value": 1}},
        events=[event("historical-agent-model"), event("historical-judge", "finance_test_judge")],
    )
    converted = convert_sample(sample, "run")
    assert converted["resolved_models"] == {"historical-agent-model"}
    assert any(s["attributes"].get("model.role") == "finance_test_judge" for s in converted["spans"])


def test_changed_finance_snapshot_blocks_comparison():
    a, b = historical_run(model="mock/perfect"), historical_run(model="mock/strong")
    a.manifest = {**a.manifest, "finance_evaluation": {"dataset": {"revision": "one"}}}
    b.manifest = {**b.manifest, "finance_evaluation": {"dataset": {"revision": "two"}}}
    keys = {("sample", 1)}
    result = check_compatibility(a, b, keys, keys)
    assert not result.comparable
    assert any("Finance dataset" in reason for reason in result.blocking)
    b.manifest["finance_evaluation"] = a.manifest["finance_evaluation"]
    assert check_compatibility(a, b, keys, keys).comparable
