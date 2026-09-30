"""`bench_golden.py` 측정 인프라 (v1.2-S0b) — 판정 기준이 아니라 **측정 조건**을 고정한다.

session-17 §1·§2가 찾은 빈틈은 대부분 "사람의 기억에만 있는 조건"이었다: 동시 실행 금지,
queue == 문서 수, 덮어쓰기 금지, 층 분리. 여기서는 그 조건이 **코드로 집행되는지**를 본다.
거부 경로는 전부 "LLM을 한 번도 부르지 않았다"까지 확인한다 — 거부가 호출 뒤에
일어나면 할당 자원을 이미 쓴 뒤다.

엔드포인트는 부르지 않는다. 가짜 프로바이더·가짜 임베딩·임시 인덱스를 쓴다.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import bench_golden, compare_bench_runs  # noqa: E402
from src.obs.tracer import LocalJsonlTracer, NullTracer  # noqa: E402
from src.orchestrator import prompts  # noqa: E402
from src.orchestrator.state import Citation, Finding  # noqa: E402
from src.providers import ChatMessage, LLMResponse  # noqa: E402
from src.providers.config import TracingSettings, VectorStoreSettings  # noqa: E402
from src.tools.corpus import CorpusDoc  # noqa: E402
from src.tools.retrieval import ChromaRetriever  # noqa: E402

_DIM = 8


class FakeEmbeddings:
    model_name = "fake/bench-embeddings"

    @property
    def dimension(self) -> int:
        return _DIM

    def _vector(self, text: str) -> list[float]:
        seed = sum(ord(c) for c in text) or 1
        return [((seed * (i + 1)) % 97) / 97.0 for i in range(_DIM)]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class RoleProvider:
    """시스템 프롬프트로 노드를 알아보고 정해진 응답을 돌려준다."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        system = messages[0].content
        if system == prompts.OUTLINER_SYSTEM:
            text = '["항목 하나", "항목 둘"]'
        elif system == prompts.RESEARCHER_SYSTEM:
            text = '{"supporting": [1, 2, 3, 4]}'
        elif system == prompts.VERIFIER_SYSTEM:
            text = '{"verdict": "covered"}'
        else:
            text = "초안"
        return LLMResponse(
            text=text, model="fake", finish_reason="stop",
            prompt_tokens=10, completion_tokens=5, latency_s=0.01,
        )


DOC_IDS = [f"arXiv:0000.{n:04d}v1" for n in range(1, 5)]

GOLDEN = {
    "version": "9.9-test",
    "cases": [
        {"id": "GS-013", "query": "위험군 질의", "stratum": "A",
         "expect": {"expect_uncovered": False, "min_citations": 1,
                    "expected_doc_ids": [DOC_IDS[0]]}},
        {"id": "GS-003", "query": "두 문서 질의", "stratum": "B",
         "expect": {"expect_uncovered": False, "min_citations": 9,
                    "expected_doc_ids": [DOC_IDS[1]]}},
        {"id": "GS-028", "query": "음성 질의", "stratum": "C",
         "expect": {"expect_uncovered": True, "min_citations": 0, "expected_doc_ids": []}},
    ],
}


def _build_index(persist_dir: Path, collection: str, *, twice: bool = False) -> None:
    settings = VectorStoreSettings(persist_dir=persist_dir, collection=collection)
    retriever = ChromaRetriever(embeddings=FakeEmbeddings(), settings=settings)
    docs = [
        CorpusDoc(doc_id=d, source="arxiv", title=f"문서 {i}", text=f"본문 {i}",
                  locator="abstract", url="", published="2026-01-01")
        for i, d in enumerate(DOC_IDS, start=1)
    ]
    retriever.index(docs)
    if twice:  # session-15 §6의 결함 재현: --reset 없이 다시 쓴다
        retriever.index(docs)


@pytest.fixture
def bench(tmp_path, monkeypatch):
    persist_dir = tmp_path / "chroma"
    trace_dir = tmp_path / "traces"
    out_dir = tmp_path / "eval"
    golden_path = tmp_path / "golden.json"
    golden_path.write_text(json.dumps(GOLDEN, ensure_ascii=False), encoding="utf-8")
    store = VectorStoreSettings(persist_dir=persist_dir, collection="bench_test")
    provider = RoleProvider()
    litellm_warmed: list[bool] = []

    monkeypatch.setattr(bench_golden, "load_settings",
                        lambda: SimpleNamespace(model="fake", redacted=lambda: {"model": "fake"}))
    monkeypatch.setattr(bench_golden, "load_embedding_settings",
                        lambda: SimpleNamespace(model_name=FakeEmbeddings.model_name))
    monkeypatch.setattr(bench_golden, "load_tracing_settings",
                        lambda: TracingSettings(local_trace_dir=trace_dir))
    monkeypatch.setattr(bench_golden, "load_vectorstore_settings", lambda: store)
    monkeypatch.setattr(bench_golden, "get_embedding_provider", FakeEmbeddings)
    monkeypatch.setattr(bench_golden, "get_provider", lambda cache=False: provider)
    monkeypatch.setattr(bench_golden, "get_tracer", lambda: LocalJsonlTracer(trace_dir))
    monkeypatch.setattr(bench_golden, "_warm_up_litellm",
                        lambda: litellm_warmed.append(True) or 0.0)

    def run(*flags: str) -> int:
        return bench_golden.main(
            ["--out-dir", str(out_dir), "--golden-set", str(golden_path), *flags]
        )

    return SimpleNamespace(run=run, persist_dir=persist_dir, out_dir=out_dir,
                           provider=provider, litellm_warmed=litellm_warmed,
                           monkeypatch=monkeypatch)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- 실행 1회: 결과 형태 -----------------------------------------------------


def test_result_records_version_strata_items_and_no_combined_score(bench) -> None:
    """#2 버전 · #3 행 층 · #5 층별 집계 · #4 min_citations 별도 열 · 항목별 후보/supporting."""
    _build_index(bench.persist_dir, "bench_test")
    assert bench.run("--label", "one") == 0

    result = _load(bench.out_dir / "bench-one.json")
    assert result["golden_set_version"] == "9.9-test"
    assert result["cache_enabled"] is False
    assert result["candidate_score_exposed"] is False  # S0c(ADR-025) 이후 = 점수 제거
    assert result["index_check"]["queue_rows"] == result["index_check"]["indexed_docs"] == 4

    # 합산 pass/fail 키가 없다. 층별만 있고 n이 붙는다.
    assert "scoring" not in result
    assert result["scoring_by_stratum"] == {
        "A": {"n": 1, "passed": 1, "failed": 0, "unscored": 0},
        "B": {"n": 1, "passed": 1, "failed": 0, "unscored": 0},
        "C": {"n": 1, "passed": 0, "failed": 1, "unscored": 0},
    }

    rows = {r["case_id"]: r for r in result["rows"]}
    assert [r["stratum"] for r in result["rows"]] == ["A", "B", "C"]
    # min_citations는 기록만 — GS-003은 미충족인데 판정은 pass다.
    assert rows["GS-003"]["score"]["min_citations_met"] is False
    assert rows["GS-003"]["score"]["passed"] is True
    assert result["min_citations_unmet"] == ["GS-003"]

    gs013 = rows["GS-013"]
    assert gs013["topic_count"] == 2 and gs013["revisions"] == 1
    assert gs013["local_trace"] is True
    assert len(gs013["items"]) == 2
    first = gs013["items"][0]
    assert first["topic"] == "항목 하나" and first["revision"] == 0
    assert [c["rank"] for c in first["candidates"]] == [1, 2, 3, 4]
    assert set(first["supporting"]) == set(DOC_IDS)
    for key in ("wall_clock_s", "llm_latency_s", "other_latency_s"):
        assert key in gs013
    assert set(gs013["by_node"]) == {"outliner", "researcher", "verifier", "writer"}

    risk = result["risk_selection"]
    assert (risk["selected"], risk["eligible"]) == (2, 2)
    assert risk["per_case"].keys() == {"GS-013"}
    assert result["calls"]["endpoint_requests_upper_bound"] == result["calls"]["total"] * 3


def test_warmup_covers_chroma_and_litellm_before_cases(bench) -> None:
    """#9 — 워밍업 기록에 Chroma 첫 질의와 litellm 임포트가 있고, 실제로 불렸다."""
    _build_index(bench.persist_dir, "bench_test")
    assert bench.run("--label", "warm") == 0
    warmup = _load(bench.out_dir / "bench-warm.json")["warmup"]
    assert {"embedding_cold_load_s", "chroma_first_query_s", "litellm_import_s"} <= warmup.keys()
    assert bench.litellm_warmed == [True]


def test_warm_up_litellm_only_imports(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(bench_golden.importlib, "import_module", lambda name: seen.append(name))
    bench_golden._warm_up_litellm()
    assert seen == ["litellm"]


# --- #6 반복 -----------------------------------------------------------------


def test_repeat_writes_one_file_per_run(bench) -> None:
    _build_index(bench.persist_dir, "bench_test")
    assert bench.run("--label", "v1.2-s0b", "--repeat", "2") == 0
    run1 = _load(bench.out_dir / "bench-v1.2-s0b-run1.json")
    run2 = _load(bench.out_dir / "bench-v1.2-s0b-run2.json")
    assert (run1["run_index"], run2["run_index"]) == (1, 2)
    assert run1["label"] == "v1.2-s0b-run1"
    assert not (bench.out_dir / "bench-v1.2-s0b.json").exists()


def test_run_labels() -> None:
    assert bench_golden.run_labels("x", 1) == ["x"]
    assert bench_golden.run_labels("x", 2) == ["x-run1", "x-run2"]
    with pytest.raises(ValueError):
        bench_golden.run_labels("x", 0)


# --- #7 덮어쓰기 거부 --------------------------------------------------------


def test_existing_label_is_refused_before_any_call(bench) -> None:
    _build_index(bench.persist_dir, "bench_test")
    assert bench.run("--label", "dup") == 0
    path = bench.out_dir / "bench-dup.json"
    before = path.read_bytes()
    calls = bench.provider.calls

    assert bench.run("--label", "dup") == bench_golden.EXIT_EXISTS
    assert path.read_bytes() == before
    assert bench.provider.calls == calls


def test_repeat_refuses_if_any_run_file_exists(bench) -> None:
    """2회차 파일만 있어도 1회차를 시작하지 않는다."""
    _build_index(bench.persist_dir, "bench_test")
    bench.out_dir.mkdir(parents=True)
    (bench.out_dir / "bench-r-run2.json").write_text("{}", encoding="utf-8")
    assert bench.run("--label", "r", "--repeat", "2") == bench_golden.EXIT_EXISTS
    assert bench.provider.calls == 0
    assert not (bench.out_dir / "bench-r-run1.json").exists()


# --- #8 인덱스 무결성 --------------------------------------------------------


def test_dirty_write_log_is_refused_before_any_call(bench) -> None:
    _build_index(bench.persist_dir, "bench_test", twice=True)
    assert bench.run("--label", "dirty") == bench_golden.EXIT_INDEX
    assert bench.provider.calls == 0
    assert not (bench.out_dir / "bench-dirty.json").exists()


def test_missing_index_is_refused(bench) -> None:
    assert bench.run("--label", "none") == bench_golden.EXIT_INDEX
    assert bench.provider.calls == 0


# --- 잠금 --------------------------------------------------------------------


def test_concurrent_run_on_same_index_is_refused(bench) -> None:
    _build_index(bench.persist_dir, "bench_test")
    lock = bench_golden.lock_path(bench.persist_dir)
    lock.write_text("pid=1 started=earlier\n", encoding="utf-8")

    assert bench.run("--label", "locked") == bench_golden.EXIT_LOCKED
    assert bench.provider.calls == 0
    assert lock.exists()  # 남의 잠금을 지우지 않는다


def test_lock_is_released_after_run_and_lives_outside_index(bench) -> None:
    _build_index(bench.persist_dir, "bench_test")
    lock = bench_golden.lock_path(bench.persist_dir)
    assert lock.parent == bench.persist_dir.parent
    assert bench.run("--label", "unlock") == 0
    assert not lock.exists()


def test_lock_released_even_when_refused_inside(tmp_path) -> None:
    persist = tmp_path / "chroma"
    with pytest.raises(bench_golden.BenchRefused):
        with bench_golden.index_lock(persist):
            raise bench_golden.BenchRefused("x", 9)
    assert not bench_golden.lock_path(persist).exists()


# --- #11 트레이스 없음 -------------------------------------------------------


def test_missing_local_trace_warns_and_is_not_counted_as_zero(bench, capsys) -> None:
    _build_index(bench.persist_dir, "bench_test")
    bench.monkeypatch.setattr(bench_golden, "get_tracer", lambda: NullTracer())
    assert bench.run("--label", "notrace") == 0

    out = capsys.readouterr().out
    assert "트레이싱이 꺼져 있습니다" in out
    assert "로컬 트레이스가 없습니다" in out
    result = _load(bench.out_dir / "bench-notrace.json")
    assert result["rows_without_local_trace"] == ["GS-003", "GS-013", "GS-028"]
    assert result["rows"][0]["items"] is None
    # 효과 지표는 0/0이 아니라 "기록 없음"으로 남는다.
    assert result["risk_selection"]["missing_items"] == ["GS-013"]
    assert result["risk_selection"]["rate"] is None


def test_require_trace_refuses_null_tracer_before_any_call(bench) -> None:
    _build_index(bench.persist_dir, "bench_test")
    bench.monkeypatch.setattr(bench_golden, "get_tracer", lambda: NullTracer())
    assert bench.run("--label", "strict", "--require-trace") == bench_golden.EXIT_TRACE
    assert bench.provider.calls == 0


def test_require_trace_stops_on_row_without_trace_and_saves_nothing(bench) -> None:
    """트레이스 파일이 사라진 행이 나오면 그 자리에서 멈추고 부분 결과를 남기지 않는다."""
    _build_index(bench.persist_dir, "bench_test")
    bench.monkeypatch.setattr(bench_golden, "_read_trace", lambda path: None)
    assert bench.run("--label", "strict2", "--require-trace") == bench_golden.EXIT_TRACE
    assert 0 < bench.provider.calls <= 12  # 첫 케이스만 돌고 멈췄다 (4T+2, T=2)
    assert not (bench.out_dir / "bench-strict2.json").exists()
    assert not bench_golden.lock_path(bench.persist_dir).exists()


# --- 항목 짝짓기 · 선택률 ----------------------------------------------------


def _span(topic: str, doc_ids: list[str] | None) -> dict:
    return {
        "name": "researcher_retrieve",
        "input": {"topic": topic},
        "output": None if doc_ids is None else [{"doc_id": d, "score": 0.8} for d in doc_ids],
    }


def _cite(doc_id: str) -> Citation:
    return Citation(doc_id=doc_id, locator="abstract", snippet="")


def test_item_records_pairs_in_order_and_refuses_mismatch() -> None:
    findings = [
        Finding(topic="a", citations=(_cite("d2"),), revision=0),
        Finding(topic="b", citations=(), revision=0),
    ]
    records = [{"name": "researcher_call"}, _span("a", ["d1", "d2"]), _span("b", None)]
    items = bench_golden.item_records(findings, records)
    assert items[0]["candidates"] == [
        {"doc_id": "d1", "rank": 1, "score": 0.8},
        {"doc_id": "d2", "rank": 2, "score": 0.8},
    ]
    assert items[0]["supporting"] == ["d2"]
    assert items[1]["retrieval_error"] is True and items[1]["candidates"] == []

    # 이름이 어긋나거나 개수가 다르면 추정하지 않는다.
    assert bench_golden.item_records(findings, [_span("b", []), _span("a", [])]) is None
    assert bench_golden.item_records(findings, [_span("a", [])]) is None
    assert bench_golden.item_records(findings, None) is None


def _row(case_id: str, items, *, expected=("gold",), stratum="A", passed=True,
         cited=("gold",), topic_count=2, revisions=1, calls=6, tokens=(100, 20)) -> dict:
    return {
        "case_id": case_id, "stratum": stratum, "items": items,
        "topic_count": topic_count, "revisions": revisions, "llm_calls": calls,
        "prompt_tokens": tokens[0], "completion_tokens": tokens[1], "billed_tokens": sum(tokens),
        "wall_clock_s": 1.0, "llm_latency_s": 0.8, "other_latency_s": 0.1,
        "retrieval_latency_s": 0.1, "cache_hits": 0, "local_trace": items is not None,
        "by_node": {"researcher": {"calls": calls}},
        "retry_evidence": {"retry_citations_total": 0, "new_citations": 0},
        "score": {"passed": passed, "expected_doc_ids": list(expected),
                  "cited_doc_ids": list(cited), "min_citations_met": True},
    }


def _item(candidates, supporting, revision=0) -> dict:
    return {"candidates": [{"doc_id": d} for d in candidates],
            "supporting": list(supporting), "revision": revision}


def test_risk_selection_counts_only_calls_with_gold_in_candidates() -> None:
    rows = [
        _row("GS-013", [
            _item(["gold", "x"], ["gold"]),          # 분모·분자
            _item(["x", "y"], []),                  # 정답이 후보에 없음 → 제외
            _item(["gold"], [], revision=1),        # 재시도 회차, 고르지 않음
        ]),
        _row("GS-017", [_item(["gold"], ["x"])]),
        _row("GS-001", [_item(["gold"], ["gold"])]),  # 위험군 아님 → 무시
    ]
    risk = bench_golden.risk_selection(rows)
    assert (risk["selected"], risk["eligible"], risk["rate"]) == (1, 3, round(1 / 3, 4))
    assert (risk["first_pass_selected"], risk["first_pass_eligible"]) == (1, 2)
    assert set(risk["per_case"]) == {"GS-013", "GS-017"}
    assert risk["per_case"]["GS-013"]["calls"] == 3


def test_summarize_reports_per_item_calls_and_distributions() -> None:
    rows = [_row("GS-001", [], topic_count=3, calls=9), _row("GS-002", [], topic_count=4, calls=12)]
    summary = bench_golden.summarize(rows)
    assert summary["calls"]["per_item"]["mean"] == 3.0
    assert summary["topic_count_distribution"] == {"3": 1, "4": 1}
    assert summary["latency"]["wall"]["p50"] == 1.0
    assert "scoring" not in summary


def test_candidate_score_exposed_reads_real_formatter(monkeypatch) -> None:
    assert bench_golden.candidate_score_exposed() is False  # S0c(ADR-025) 이후
    monkeypatch.setattr(
        bench_golden,
        "_format_candidates",
        lambda chunks: f"[1] doc_id=probe (유사도 {chunks[0].score:.3f})",  # S0b 형식
    )
    assert bench_golden.candidate_score_exposed() is True


# --- 회차 간 대조 ------------------------------------------------------------


def _run_summary(label: str, rows: list[dict]) -> dict:
    return {"label": label, **{k: "same" for k in compare_bench_runs.CONDITION_KEYS},
            **bench_golden.summarize(rows), "rows": rows}


def test_compare_counts_flips_by_stratum_and_citation_diffs() -> None:
    run1 = _run_summary("r1", [
        _row("GS-013", [_item(["gold"], ["gold"])], stratum="A"),
        _row("GS-006", [], stratum="B", passed=False, cited=()),
        _row("GS-028", [], stratum="C", passed=True, cited=(), tokens=(100, 20)),
    ])
    run2 = _run_summary("r2", [
        _row("GS-013", [_item(["gold"], [])], stratum="A", passed=False, cited=("x",)),
        _row("GS-006", [], stratum="B", passed=False, cited=()),
        _row("GS-028", [], stratum="C", passed=True, cited=(), tokens=(100, 35), topic_count=3),
    ])
    result = compare_bench_runs.compare(run1, run2)
    assert result["verdict_flips"]["total"] == 1
    assert result["verdict_flips"]["by_stratum"]["A"] == {"flips": 1, "n": 1}
    assert result["verdict_flips"]["by_stratum"]["B"] == {"flips": 0, "n": 1}
    assert result["citation_set_diffs"]["cases"] == [
        {"case_id": "GS-013", "stratum": "A", "only_run1": ["gold"], "only_run2": ["x"]}
    ]
    assert result["topic_count_changed"] == ["GS-028"]
    assert result["tokens_changed"] == ["GS-028"]
    assert result["risk_selection"]["run1"]["rate"] == 1.0
    assert result["risk_selection"]["run2"]["rate"] == 0.0
    assert result["risk_selection"]["rate_delta"] == -1.0


def test_compare_refuses_different_conditions_or_cases() -> None:
    rows = [_row("GS-001", [])]
    base = _run_summary("r1", rows)
    other = {**_run_summary("r2", rows), "prompt_version": "changed"}
    with pytest.raises(ValueError, match="측정 조건"):
        compare_bench_runs.compare(base, other)
    with pytest.raises(ValueError, match="케이스 구성"):
        compare_bench_runs.compare(base, _run_summary("r2", [_row("GS-002", [])]))


def test_compare_main_refuses_to_overwrite(tmp_path) -> None:
    rows = [_row("GS-001", [])]
    a, b, out = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "cmp.json"
    a.write_text(json.dumps(_run_summary("r1", rows)), encoding="utf-8")
    b.write_text(json.dumps(_run_summary("r2", rows)), encoding="utf-8")
    assert compare_bench_runs.main([str(a), str(b), "--out", str(out)]) == 0
    first = out.read_bytes()
    assert compare_bench_runs.main([str(a), str(b), "--out", str(out)]) == 2
    assert out.read_bytes() == first


# --- 노드 안 동시 호출 (ADR-026) ---------------------------------------------


@pytest.mark.parametrize("mode", ["off", "on"])
def test_bench_records_concurrency_and_pairs_items_in_both_modes(bench, mode) -> None:
    _build_index(bench.persist_dir, "bench_test")
    assert bench.run("--label", f"p1-{mode}", "--parallel", mode, "--max-concurrency", "3",
                     "--require-trace") == 0
    result = _load(bench.out_dir / f"bench-p1-{mode}.json")
    assert result["parallel"] is (mode == "on")
    assert result["max_concurrency"] == 3
    assert result["effective_concurrency"] == (3 if mode == "on" else 1)
    assert result["endpoint_errors"] == {"total": 0, "rate_limit": 0, "timeout": 0, "other": 0}
    for row in result["rows"]:
        assert row["items"] is not None
        assert [i["topic"] for i in row["items"] if i["revision"] == 0] == ["항목 하나", "항목 둘"]
        assert all(len(i["input_hash"]) == 64 for i in row["items"] if i["candidates"])


def test_bench_concurrency_defaults_come_from_env(bench, monkeypatch) -> None:
    _build_index(bench.persist_dir, "bench_test")
    monkeypatch.setattr(bench_golden, "load_concurrency_settings",
                        lambda: SimpleNamespace(parallel=True, max_concurrency=2))
    assert bench.run("--label", "p1-env", "--only", "GS-013") == 0
    result = _load(bench.out_dir / "bench-p1-env.json")
    assert (result["parallel"], result["effective_concurrency"]) == (True, 2)


def test_bench_rejects_nonpositive_cap_before_any_call(bench) -> None:
    assert bench.run("--label", "p1-bad", "--parallel", "on", "--max-concurrency", "0") == 2
    assert bench.provider.calls == 0


def test_p1_judge_logic_identity_defect_and_outline_variation(bench, tmp_path) -> None:
    from scripts import compare_p1_concurrency as p1

    _build_index(bench.persist_dir, "bench_test")
    assert bench.run("--label", "j-off", "--parallel", "off", "--require-trace") == 0
    assert bench.run("--label", "j-on", "--parallel", "on", "--require-trace") == 0
    off = _load(bench.out_dir / "bench-j-off.json")
    on = _load(bench.out_dir / "bench-j-on.json")

    result = p1.judge(off, on, off)
    assert result["logic"]["logic_identical"] is True
    assert result["logic"]["first_pass_calls_compared"] > 0
    assert result["batching_nondeterminism"]["cases_with_any_output_diff"] == []
    assert [r["label"] for r in result["run_order"]] == ["j-off", "j-on"]

    # 같은 Outliner인데 입력 해시가 다르면 결함이다
    broken = json.loads(json.dumps(on))
    broken["rows"][0]["items"][0]["input_hash"] = "0" * 64
    assert p1.judge(off, broken, None)["logic"]["defects"][0]["case_id"] == on["rows"][0]["case_id"]

    # Outliner가 달라진 케이스는 결함이 아니라 시간 간 변동이다
    moved = json.loads(json.dumps(on))
    for item in moved["rows"][0]["items"]:
        item["topic"] = "다른 " + item["topic"]
        item["input_hash"] = "1" * 64
    judged = p1.judge(off, moved, None)
    assert judged["logic"]["logic_identical"] is True
    assert judged["logic"]["time_variation_outline_changed"]["total"] == 1

    # 입력이 같은데 supporting이 다르면 배칭 비결정성
    drift = json.loads(json.dumps(on))
    drift["rows"][0]["items"][0]["supporting"] = ["다른 문서"]
    b = p1.judge(off, drift, None)["batching_nondeterminism"]
    assert b["first_pass_supporting_diffs"]["total"] == 1

    # off/on이 뒤바뀌면 거부
    with pytest.raises(ValueError):
        p1.judge(on, off, None)
    out = tmp_path / "p1.json"
    assert p1.main([str(bench.out_dir / "bench-j-off.json"), str(bench.out_dir / "bench-j-on.json"),
                    "--out", str(out)]) == 0
    assert p1.main([str(bench.out_dir / "bench-j-off.json"), str(bench.out_dir / "bench-j-on.json"),
                    "--out", str(out)]) == 2  # 덮어쓰기 거부
