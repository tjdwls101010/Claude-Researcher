"""Seam: ``decisions.propose`` / ``decisions.resolve`` / ``decisions.summary`` on a project under ``tmp_path`` with an injected clock."""

import pytest

from research import decisions, project
from research.errors import InputError, NotFoundError
from savepaper.frontmatter import parse

NOW = "2026-09-07T01:00:00Z"
LATER = "2026-09-07T02:00:00Z"


def good(**over):
    d = {
        "title": "어느 베이스라인을 쓸 것인가",
        "asked": True,
        "options": [
            {"label": "A", "fails_when": "A가 이미 SOTA가 아니면 비교가 무의미", "evidence": ["/papers/sources/2608.07885.md#§4.2"]},
            {"label": "B", "fails_when": "B는 재현 코드가 없어 6개월 걸릴 수 있음", "evidence": [], "evidence_gap": "재현 보고 없음"},
        ],
        "recommendation": "A",
        "body": "맥락 설명",
    }
    d.update(over)
    return d


@pytest.fixture
def lay(tmp_path):
    return project.init_project(tmp_path, "toy", question="q", now=NOW)


def test_propose_writes_options_and_recommendation_before_any_choice(lay):
    path = decisions.propose(lay, good(), now=NOW)
    assert path.name == "001-baseline.md" or path.name.startswith("001-")
    fm, body = parse(path.read_text())
    assert fm["type"] == "Decision" and fm["proposed_at"] == NOW and fm["asked"] is True
    assert fm["recommendation"] == "A" and fm["chosen"] is None and fm["decided_by"] is None and fm["resolved_at"] is None
    assert fm["options"][1]["evidence_gap"] == "재현 보고 없음" and fm["supersedes"] is None
    assert "맥락 설명" in body
    second = decisions.propose(lay, good(title="두 번째"), now=NOW)
    assert second.name.startswith("002-")


@pytest.mark.parametrize(
    "over, path",
    [
        ({"title": ""}, "title"),
        ({"asked": "yes"}, "asked"),
        ({"options": [good()["options"][0]]}, "options"),
        ({"options": good()["options"] + good()["options"] + [good()["options"][0]]}, "options"),
        ({"options": [dict(good()["options"][0], fails_when=""), good()["options"][1]]}, "options[0].fails_when"),
        ({"options": [dict(good()["options"][0], evidence=[]), good()["options"][1]]}, "options[0].evidence_gap"),
        ({"options": [good()["options"][0], dict(good()["options"][1], label="A")]}, "options[1].label"),
        ({"recommendation": "Z"}, "recommendation"),
        ({"supersedes": "D999"}, "supersedes"),
    ],
)
def test_propose_refuses_a_missing_or_wrong_cell_by_its_path(lay, over, path):
    with pytest.raises(InputError) as exc:
        decisions.propose(lay, good(**over), now=NOW)
    assert path in str(exc.value)


def test_resolve_fills_choice_after_proposal_and_records_dissent(lay):
    path = decisions.propose(lay, good(), now=NOW)
    did = decisions.decision_id(path)
    out = decisions.resolve(lay, did, chosen="B", dissent="A가 더 싸고 근거가 있다", now=LATER)
    fm, _ = parse(out.read_text())
    assert fm["chosen"] == "B" and fm["resolved_at"] == LATER and fm["decided_by"] == "human:seongjin"
    assert fm["dissent"] == "A가 더 싸고 근거가 있다" and fm["proposed_at"] == NOW
    with pytest.raises(InputError):
        decisions.resolve(lay, did, chosen="A", now=LATER)  # already resolved


def test_unasked_decision_defaults_to_claude_and_needs_no_dissent(lay):
    path = decisions.propose(lay, good(asked=False), now=NOW)
    fm, _ = parse(decisions.resolve(lay, decisions.decision_id(path), chosen="A", now=LATER).read_text())
    assert fm["decided_by"] == "claude" and fm["dissent"] is None


def test_resolve_rejects_unknown_label_or_id(lay):
    path = decisions.propose(lay, good(), now=NOW)
    with pytest.raises(InputError) as exc:
        decisions.resolve(lay, decisions.decision_id(path), chosen="Z", now=LATER)
    assert "chosen" in str(exc.value)
    with pytest.raises(NotFoundError):
        decisions.resolve(lay, "D042", chosen="A", now=LATER)


def test_supersedes_must_exist_and_not_cycle(lay):
    first = decisions.propose(lay, good(), now=NOW)
    fid = decisions.decision_id(first)
    decisions.resolve(lay, fid, chosen="A", now=LATER)
    second = decisions.propose(lay, good(title="재검토", supersedes=fid), now=LATER)
    assert parse(second.read_text())[0]["supersedes"] == fid
    with pytest.raises(InputError) as exc:
        decisions.propose(lay, good(title="자기", supersedes="D003"), now=LATER)
    assert "supersedes" in str(exc.value)


def test_summary_counts_open_and_agreement_by_decider(lay):
    a = decisions.propose(lay, good(), now=NOW)
    decisions.resolve(lay, decisions.decision_id(a), chosen="B", now=LATER)
    b = decisions.propose(lay, good(asked=False), now=NOW)
    decisions.resolve(lay, decisions.decision_id(b), chosen="A", now=LATER)
    decisions.propose(lay, good(title="열림"), now=NOW)
    s = decisions.summary(lay)
    assert [d["id"] for d in s["open"]] == ["D003"]
    assert s["asked_ratio"] == pytest.approx(2 / 3)
    assert s["agreement"]["human:seongjin"] == {"n": 1, "recommended_chosen": 0}
    assert s["agreement"]["claude"] == {"n": 1, "recommended_chosen": 1}


def test_labels_are_normalised_once_and_slug_is_validated(lay):
    d = good()
    d["options"][0]["label"] = " A "
    fm, _ = parse(decisions.propose(lay, d, now=NOW).read_text())
    assert fm["options"][0]["label"] == "A" and fm["recommendation"] == "A"
    with pytest.raises(InputError) as exc:
        decisions.propose(lay, good(slug="../x"), now=NOW)
    assert "slug" in str(exc.value)


def test_decision_ids_allocate_past_gaps(lay):
    p = decisions.propose(lay, good(), now=NOW)
    p.rename(lay.decisions / "005-moved.md")
    assert decisions.propose(lay, good(title="n"), now=NOW).name.startswith("006-")
