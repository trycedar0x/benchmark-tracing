from everybench.pricing import cost_for
from everybench.redact import apply_policy, redact, redact_text, scrub_attributes


def test_redaction_catches_common_credentials():
    text = (
        "key sk-abcdefghijklmnopqrstuvwx and sk-ant-abcdefghijklmnopqrstuv, aws AKIAABCDEFGHIJKLMNOP, "
        "gh ghp_abcdefghijklmnopqrstuvwxyz0123, Bearer abcdefghijklmnopqrstuvwxyz"
    )
    out = redact_text(text)
    for secret in ("sk-abcdefghij", "sk-ant-abcdef", "AKIAABCDEFGHIJKLMNOP", "ghp_abcdefghij", "Bearer abcdef"):
        assert secret not in out
    assert "[REDACTED:anthropic_key]" in out


def test_sensitive_keys_dropped_but_token_counts_kept():
    attrs = scrub_attributes(
        {"api_key": "x", "github_token": "y", "Authorization": "z", "gen_ai.usage.input_tokens": 12, "max_tokens": 100}
    )
    assert attrs["api_key"] == attrs["github_token"] == attrs["Authorization"] == "[REDACTED:field]"
    assert attrs["gen_ai.usage.input_tokens"] == 12 and attrs["max_tokens"] == 100
    assert redact({"nested": {"password": "hunter2"}}) == {"nested": {"password": "[REDACTED:field]"}}


def test_content_policies():
    assert apply_policy({"a": 1}, "metadata") == (None, "withheld")
    assert apply_policy({"a": "sk-abcdefghijklmnopqrstuvwx"}, "redacted")[1] == "redacted"
    assert apply_policy({"a": 1}, "full") == ({"a": 1}, "present")
    assert apply_policy(None, "full") == (None, "missing")


def test_mock_prices_and_unknown_models():
    assert cost_for("mock/strong", 1_000_000, 0) == 2.0
    assert cost_for("someprovider/unpriced", 1000, 1000) is None
