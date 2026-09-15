"""LLM 응답 캐시 (ADR-008).

**왜 프로바이더 계층인가.** 캐시는 노드가 아는 개념이 아니다. Researcher가
"이 항목은 전에 본 것 같으니 캐시를 보자"라고 판단하기 시작하면, 캐시 정책이
네 노드에 흩어지고 하네스 비교 실험에서 무엇이 바뀐 건지 알 수 없게 된다
(ADR-002 모델 고정 원칙과 같은 이유다 — 변인을 하나씩만 움직인다).
`LLMProvider` 프로토콜을 그대로 구현하는 데코레이터로 두면 캐시 온/오프가
노드 코드 변경 없이 **설정 하나**로 바뀐다.

**왜 디스크인가.** 프로세스 1건 = 질의 1건인 현재 실행 방식에서 인메모리 캐시는
프로세스와 함께 사라져 재실행에 아무 도움이 안 된다. 디스크에 두면 세션을 넘어
재현 실행(골든셋 재측정, 데모)이 캐시를 탄다. Redis 같은 공유 캐시는 배포 형태가
정해진 뒤에 볼 문제다 (ADR-008 Alternatives).

**캐시 키의 전제.** 키는 (모델, 메시지, temperature, max_tokens, stop)의 해시다.
`temperature=0.0`이 파이프라인 전 구간의 기본값이라 같은 입력에 같은 출력을
기대할 수 있다는 것이 전제다. temperature > 0 호출은 캐시하지 않는다 — 샘플링
다양성을 캐시가 조용히 없애면 실험 결과가 달라진다.

**프롬프트 버전은 키에 들어가지 않는다.** 프롬프트가 바뀌면 메시지 본문이 바뀌고
키가 자동으로 달라진다. 별도 필드로 중복해 넣으면 두 곳이 어긋날 여지만 생긴다.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .config import CacheSettings, load_cache_settings
from .llm import ChatMessage, LLMProvider, LLMResponse

# 캐시 파일 포맷 버전. 저장 스키마가 바뀌면 올린다 — 옛 파일을 읽고 엉뚱한
# 응답을 돌려주는 것보다 미스가 낫다.
CACHE_FORMAT_VERSION = 1


@dataclass
class CacheStats:
    """이번 프로세스의 캐시 적중 현황. 측정 스크립트가 읽는다."""

    hits: int = 0
    misses: int = 0
    # 캐시가 없었다면 청구됐을 토큰. 절감량을 "요청 수"가 아니라 토큰으로 센다.
    saved_prompt_tokens: int = 0
    saved_completion_tokens: int = 0
    # 적중한 호출들이 원래 걸렸던 지연의 합 (캐시에 저장된 값).
    saved_latency_s: float = 0.0

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "total": self.total,
            "hit_rate": round(self.hit_rate, 4),
            "saved_prompt_tokens": self.saved_prompt_tokens,
            "saved_completion_tokens": self.saved_completion_tokens,
            "saved_latency_s": round(self.saved_latency_s, 3),
        }


def make_cache_key(
    *,
    model: str,
    messages: Sequence[ChatMessage],
    temperature: float,
    max_tokens: int | None,
    stop: Sequence[str] | None,
) -> str:
    """호출을 식별하는 해시.

    sort_keys + ensure_ascii=False로 직렬화해 같은 입력이 항상 같은 키가 되게 한다.
    dict 순서에 키가 흔들리면 캐시가 조용히 무력해진다.
    """
    payload = {
        "v": CACHE_FORMAT_VERSION,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stop": list(stop) if stop else None,
        "messages": [[m.role, m.content] for m in messages],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class CachingLLMProvider:
    """`LLMProvider`를 감싸 응답을 디스크에 캐시한다.

    캐시 읽기/쓰기 실패는 삼킨다 — 캐시는 최적화지 정확성의 일부가 아니다.
    캐시 파일이 깨졌다고 리서치 실행이 죽으면 안 된다.
    """

    def __init__(
        self,
        inner: LLMProvider,
        settings: CacheSettings | None = None,
    ) -> None:
        self.inner = inner
        self.settings = settings or load_cache_settings()
        self.stats = CacheStats()
        self._dir = self.settings.cache_dir

    # --- 내부 ---------------------------------------------------------------

    def _path(self, key: str) -> Path:
        # 앞 2글자로 하위 디렉터리를 나눈다. 한 디렉터리에 수천 개 파일이 쌓이면
        # 윈도우 파일 탐색이 눈에 띄게 느려진다.
        return self._dir / key[:2] / f"{key}.json"

    def _read(self, key: str) -> LLMResponse | None:
        path = self._path(key)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if raw.get("v") != CACHE_FORMAT_VERSION:
            return None
        try:
            return LLMResponse(
                text=raw["text"],
                model=raw["model"],
                finish_reason=raw.get("finish_reason"),
                prompt_tokens=int(raw.get("prompt_tokens", 0)),
                completion_tokens=int(raw.get("completion_tokens", 0)),
                # 적중 시 지연은 "실제로 이번에 걸린 시간"이어야 한다. 저장된
                # 원래 지연을 그대로 돌려주면 캐시 효과가 측정에서 사라진다.
                latency_s=0.0,
                raw=None,
                cached=True,
                origin_latency_s=float(raw.get("latency_s", 0.0)),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _write(self, key: str, response: LLMResponse) -> None:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "v": CACHE_FORMAT_VERSION,
                "text": response.text,
                "model": response.model,
                "finish_reason": response.finish_reason,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "latency_s": round(response.latency_s, 4),
                "stored_at": time.time(),
            }
            # 같은 키를 동시에 쓰는 경우를 대비해 임시 파일에 쓰고 교체한다.
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            return

    # --- LLMProvider 프로토콜 ------------------------------------------------

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse:
        # 샘플링이 켜진 호출은 캐시하지 않는다 (모듈 독스트링 참조).
        if temperature != 0.0:
            return self.inner.complete(
                messages, temperature=temperature, max_tokens=max_tokens, stop=stop
            )

        model = getattr(getattr(self.inner, "settings", None), "model", "") or ""
        key = make_cache_key(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
        )

        hit = self._read(key)
        if hit is not None:
            self.stats.hits += 1
            self.stats.saved_prompt_tokens += hit.prompt_tokens
            self.stats.saved_completion_tokens += hit.completion_tokens
            self.stats.saved_latency_s += hit.origin_latency_s
            return hit

        self.stats.misses += 1
        response = self.inner.complete(
            messages, temperature=temperature, max_tokens=max_tokens, stop=stop
        )
        self._write(key, response)
        return response


def clear_cache(settings: CacheSettings | None = None) -> int:
    """캐시 파일을 전부 지우고 지운 개수를 돌려준다.

    캐시 전/후 비교 측정에서 "전" 조건을 만들려면 캐시가 비어 있어야 한다.
    """
    settings = settings or load_cache_settings()
    removed = 0
    if not settings.cache_dir.exists():
        return 0
    for path in settings.cache_dir.rglob("*.json"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed
