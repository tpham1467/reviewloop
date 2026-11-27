import pytest

from reviewloop.config import Config, load_config
from reviewloop.errors import ConfigError


def test_load_config_defaults_when_missing():
    for text in (None, "", "# nothing\n"):
        c = load_config(text)
        assert c == Config()
    c = Config()
    assert c.agent == "claude-code"
    assert c.model is None
    assert c.language == "en"
    assert c.paths.exclude == []
    assert c.rules == []
    assert c.tracker is None
    assert c.security.enabled is True
    assert c.security.scanners == ["opengrep", "gitleaks"]
    assert c.suggestions is True
    assert c.fail_on == "none"
    assert c.large_diff_lines == 3000
    assert c.review_drafts is False
    assert c.version == 1


def test_load_config_full_example():
    c = load_config(
        """
version: 1
agent: codex
model: sonnet
paths: {exclude: ["**/*.lock"]}
rules:
  - {path: "app/**", file: .reviewloop/rules/api.md}
tracker: {type: jira, url: "https://j.example.com", key_pattern: "[A-Z]+-\\\\d+", require_context: true}
security: {enabled: false, scanners: [gitleaks]}
fail_on: high
"""
    )
    assert c.agent == "codex"
    assert c.rules[0].file == ".reviewloop/rules/api.md"
    assert c.tracker is not None and c.tracker.type == "jira"
    assert c.security.scanners == ["gitleaks"]


def test_load_config_rejects_unknown_key():
    with pytest.raises(ConfigError, match="bogus"):
        load_config("bogus: 1")
    with pytest.raises(ConfigError, match=r"tracker\.urll"):
        load_config("tracker: {type: jira, urll: x}")


def test_load_config_rejects_bad_value_and_yaml():
    with pytest.raises(ConfigError, match="fail_on"):
        load_config("fail_on: sometimes")
    with pytest.raises(ConfigError):
        load_config("a: [")
    with pytest.raises(ConfigError):
        load_config("- a\n- b")
    with pytest.raises(ConfigError):
        load_config("5")


@pytest.mark.parametrize(
    "text",
    [
        "token: x",
        "tracker: {type: jira, url: u, API_KEY: x}",
        "extra:\n  - nested:\n      db_Password: x",
        "secrets_file: x",
    ],
)
def test_load_config_rejects_secret_like_keys(text):
    with pytest.raises(ConfigError, match="CI variable"):
        load_config(text)


@pytest.mark.parametrize("value", [0, -5])
def test_large_diff_lines_must_be_positive(value: int) -> None:
    with pytest.raises(ConfigError, match="large_diff_lines"):
        load_config(f"large_diff_lines: {value}\n")
    assert load_config("large_diff_lines: 1\n").large_diff_lines == 1
