import importlib.util
import re
from pathlib import Path

import pydantic
import pytest
import yaml

from reviewloop.skills import schemas
from reviewloop.stacks import detect_stacks, rules_for

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "reviewloop"
SKILLS = sorted((SRC / "skills").glob("*/SKILL.md"))
RULES = sorted((SRC / "rules").glob("*.md"))
RULE_LINE = re.compile(r"^- (\S+) \[(critical|high|medium|low)\] ", re.M)
ID_RE = re.compile(r"^[A-Z]{2,5}(-[A-Z]+)?-\d+$")
EMOJI = re.compile(r"[\U0001f300-\U0001faff\u2600-\u27bf]")


def _script():
    spec = importlib.util.spec_from_file_location("check_skill_length", ROOT / "scripts" / "check_skill_length.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _body_words(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").split("---", 2)[2].split())


def test_counts() -> None:
    assert len(SKILLS) == 8
    assert len(RULES) == 11


def test_every_skill_under_800_words() -> None:
    assert all(_body_words(p) <= 800 for p in SKILLS)


def test_every_rule_under_600_words() -> None:
    assert all(_body_words(p) <= 600 for p in RULES)


def test_no_emoji_in_skills_or_rules() -> None:
    assert not [p for p in SKILLS + RULES if EMOJI.search(p.read_text(encoding="utf-8"))]


def test_rule_ids_unique_and_well_formed() -> None:
    ids = [m.group(1) for p in RULES for m in RULE_LINE.finditer(p.read_text(encoding="utf-8"))]
    assert ids
    assert all(ID_RE.match(i) for i in ids)
    assert len(ids) == len(set(ids))


def test_rule_front_matter_valid() -> None:
    for p in RULES:
        meta = yaml.safe_load(p.read_text(encoding="utf-8").split("---", 2)[1])
        assert meta["stack"] == p.stem
        assert isinstance(meta["applies_to"], list) and meta["applies_to"]
        assert isinstance(meta["detect"], list)


def test_detect_stacks_laravel_from_composer() -> None:
    stacks = detect_stacks(
        ["app/Http/UserController.php"], {"composer.json": '{"require": {"laravel/framework": "^11"}}'}
    )
    assert {"common", "php", "laravel"} <= stacks
    assert "python" not in stacks


def test_detect_stacks_rules() -> None:
    assert detect_stacks(["a.py"], {}) == {"common"}
    assert "python" in detect_stacks(["pkg/a.py"], {"svc/requirements.txt": ""})
    assert "csharp" in detect_stacks(["A.cs"], {"src/App/App.csproj": "<Project Sdk='x'>"})
    assert "react" not in detect_stacks(["a.tsx"], {"package.json": '{"react-dom": "1"}'})
    assert "react" in detect_stacks(["a.tsx"], {"package.json": '{"react": "1"}'})
    assert "nestjs" not in detect_stacks(["a.py"], {"package.json": "@nestjs/core"})


def test_rules_for_puts_common_first() -> None:
    paths = rules_for({"python", "django"})
    assert [p.stem for p in paths] == ["common", "django", "python"]
    assert rules_for(set())[0].stem == "common"


def test_output_contract_mentions_every_schema_field() -> None:
    text = (SRC / "skills" / "output-contract" / "SKILL.md").read_text(encoding="utf-8")
    models = [
        schemas.ReviewOutput,
        schemas.GateOutput,
        schemas.FollowUpVerdict,
        schemas.FollowUpOutput,
        schemas.OverviewOutput,
        schemas.ContextOutput,
        schemas.SecurityVerifyOutput,
        schemas.Finding,
        schemas.FileCoverage,
    ]
    missing = {f for m in models for f in m.model_fields if f != "id" and f"`{f}`" not in text}
    assert not missing


def test_output_contract_lists_categories() -> None:
    text = (SRC / "skills" / "output-contract" / "SKILL.md").read_text(encoding="utf-8")
    assert all(c in text for c in schemas.CATEGORIES)


def _finding(category: str) -> dict[str, object]:
    return {"path": "a.py", "line": 1, "severity": "low", "category": category, "text": "t", "existing_code": "x"}


def test_review_output_rejects_unknown_category() -> None:
    with pytest.raises(pydantic.ValidationError, match="unknown category"):
        schemas.ReviewOutput.model_validate({"findings": [_finding("style")], "coverage": []})
    ok = schemas.ReviewOutput.model_validate({"findings": [_finding("null-safety")], "coverage": []})
    assert ok.findings[0].category in schemas.PROTECTED_CATEGORIES <= schemas.CATEGORIES


def test_follow_up_verdict_literal_and_gate_int_keys() -> None:
    with pytest.raises(pydantic.ValidationError):
        schemas.FollowUpVerdict(thread_id="t", verdict="maybe", reply="r")  # type: ignore[arg-type]
    assert schemas.GateOutput.model_validate({"analysis": "a", "drop_ids": [1], "rewrites": {"2": "x"}}).rewrites == {
        2: "x"
    }


def test_check_skill_length_script(tmp_path: Path) -> None:
    mod = _script()
    assert mod.main() == 0
    (tmp_path / "skills" / "big").mkdir(parents=True)
    (tmp_path / "rules").mkdir()
    (tmp_path / "skills" / "big" / "SKILL.md").write_text("---\nname: x\n---\n" + "w " * 801)
    (tmp_path / "rules" / "ok.md").write_text("---\nstack: ok\n---\n" + "w " * 600)
    bad = mod.over_limit(tmp_path)
    assert [(p.parent.name, n) for p, n, _ in bad] == [("big", 801)]


def test_detect_stacks_plain_js_gets_typescript_rules() -> None:
    manifests = {"package.json": '{"devDependencies":{"eslint":"9"}}'}
    assert "typescript" in detect_stacks(["web/a.js"], manifests)


def test_detect_stacks_django_with_split_requirements() -> None:
    stacks = detect_stacks(
        ["app/views.py"],
        {"requirements/base.txt": "Django==5", "manage.py": "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'x')"},
    )
    assert {"python", "django"} <= stacks


def test_java_rules_do_not_apply_to_kotlin() -> None:
    assert "java" not in detect_stacks(["A.kt"], {"pom.xml": "<project>"})


def test_review_output_requires_reason_for_failed_coverage() -> None:
    bad = {"findings": [], "coverage": [{"path": "a.py", "status": "failed"}]}
    with pytest.raises(pydantic.ValidationError, match="needs a reason"):
        schemas.ReviewOutput.model_validate(bad)
    good = {"findings": [], "coverage": [{"path": "a.py", "status": "failed", "reason": "timeout"}]}
    schemas.ReviewOutput.model_validate(good)


def test_security_skills_agree_on_condition_classes() -> None:
    for name in ("security-review", "security-verify"):
        text = (SRC / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert "TLS verification disabled" in text or "TLS verification" in text
