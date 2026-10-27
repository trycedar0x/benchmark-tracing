import json

import pytest
from inspect_ai.log import EvalSample
from inspect_ai.model import ModelOutput

from everybench.inspect_convert import convert_sample, outcome_for
from everybench.scoring import ScoreSpec, normalized_score


def sample(value, *, metadata=None, output="final"):
    return EvalSample(
        id="case-1",
        epoch=1,
        input="question",
        target="reference",
        metadata=metadata or {},
        scores={"metric": {"value": value}},
        output=ModelOutput.from_content("mock/strong", output),
    )


@pytest.mark.parametrize("value", [{"score": "C", "checkpoints": 1.0}, "unknown", float("nan"), float("inf"), 80])
def test_invalid_or_unselected_scores_are_not_silently_zero(value):
    converted = convert_sample(sample(value), "run")
    assert converted["result"]["outcome"] == "unscored"
    assert converted["result"]["score"] is None
    assert "Invalid primary score" in converted["result"]["error"]


def test_explicit_structured_primary_and_all_scores_are_preserved():
    original = sample({"score": "C", "checkpoints": 0.4})
    original.scores["auxiliary"] = original.scores["metric"].model_copy(update={"value": 0.75})
    spec = ScoreSpec(scorer="metric", key="checkpoints")
    converted = convert_sample(original, "run", spec)
    assert converted["result"]["score"] == 0.4
    assert converted["result"]["outcome"] == "partial"
    raw = json.loads(converted["result"]["score_raw"])
    assert raw["values"]["metric"]["score"] == "C"
    assert raw["values"]["auxiliary"] == 0.75
    assert set(converted["evidence"]["scores"]) == {"metric", "auxiliary"}


def test_normalization_and_direction_are_explicit():
    assert normalized_score(80, ScoreSpec(maximum=100)) == 0.8
    assert normalized_score(20, ScoreSpec(maximum=100, direction="minimize")) == 0.8
    assert normalized_score("N") == 0
    assert normalized_score("0.5") == 0.5
    with pytest.raises(ValueError):
        normalized_score(101, ScoreSpec(maximum=100))
    with pytest.raises(ValueError):
        ScoreSpec(minimum=1, maximum=1)
    with pytest.raises(ValueError):
        ScoreSpec(direction="guess")
    with pytest.raises(ValueError):
        normalized_score(10**400)
    with pytest.raises(ValueError):
        ScoreSpec(maximium=100)


def test_missing_primary_scorer_is_visible():
    converted = convert_sample(sample(1), "run", ScoreSpec(scorer="missing"))
    assert converted["result"]["outcome"] == "unscored"
    assert "missing" in converted["result"]["error"]


def test_task_declared_primary_key_is_used_for_imported_logs():
    case = sample({"rubric": 0.25, "final_answer": 1}, metadata={"everybench_scoring": {"key": "rubric"}})
    assert outcome_for(case) == ("partial", 0.25)
    assert convert_sample(case, "run")["result"]["score"] == 0.25


def test_full_output_survives_separately_from_ui_preview():
    text = "start-" + "x" * 20000 + "-important-end"
    converted = convert_sample(sample(1, output=text), "run")
    assert "truncated" in converted["result"]["output"]
    assert converted["evidence"]["output"] == text
