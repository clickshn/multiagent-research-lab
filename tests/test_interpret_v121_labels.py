"""v1.2.1 해석 분류(사전 등록 `9454c23` §8.2) — 실제 감사 파일로 네 분류를 고정한다 (LLM·인덱스 없음)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import interpret_v121_labels as iv  # noqa: E402

AUDIT = json.loads(iv.AUDIT.read_text(encoding="utf-8"))
TABLES, UNCLEAR, WRONG = iv.audit_tables(AUDIT)
LABELS = iv.load_labels(iv.NEW_EXPORT, iv.NEW_ID)


def _cls(doc: str, value: str, *, harm: bool) -> str | None:
    item = {"value": value, "outcome": "chosen", "eligible": True, "harm": harm}
    return iv.classify(item, [doc], LABELS, TABLES, UNCLEAR, WRONG)


def test_missing_value_harm_is_label_minus():
    # 3CB: 감사가 Safety/Alignment를 빠진 값으로 판정
    assert _cls("arXiv:2410.09114v2", "Safety/Alignment", harm=True) == "L-"
    # GS-018 앵커: 보충 감사가 Multimodal 탈락을 '빠짐'으로 판정
    assert _cls("arXiv:2609.19093v1", "Multimodal", harm=True) == "L-"


def test_unclear_or_unaudited_harm_is_u():
    # GS-012 앵커: Training/Finetuning 탈락은 애매
    assert _cls("arXiv:2609.19145v1", "Training/Finetuning", harm=True) == "U"
    # GS-001 앵커는 감사 대상이 아니다
    assert _cls("arXiv:2412.05449v1", "Hardware/Chip", harm=True) == "U"


def test_other_harm_is_selection():
    assert _cls("arXiv:2412.20138v7", "Hardware/Chip", harm=True) == "S"


def test_survival_only_via_wrong_label_is_label_plus():
    # Robotouille의 Embodied/Robotics는 틀림 — 그 값으로 살아남으면 라벨 오류가 해악을 가린 것
    assert _cls("arXiv:2502.05227v1", "Embodied/Robotics", harm=False) == "L+"
    assert _cls("arXiv:2502.05227v1", "Agent", harm=False) is None


def test_not_eligible_or_abstain_is_unclassified():
    item = {"value": "Agent", "outcome": "chosen", "eligible": False, "harm": False}
    assert iv.classify(item, ["arXiv:2502.05227v1"], LABELS, TABLES, UNCLEAR, WRONG) is None
    item = {"value": "없음", "outcome": "abstain", "eligible": True, "harm": False}
    assert iv.classify(item, ["arXiv:2502.05227v1"], LABELS, TABLES, UNCLEAR, WRONG) is None
