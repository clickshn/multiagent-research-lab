# syntax=docker/dockerfile:1.7
#
# multiagent-research-lab — 오케스트레이터 이미지 (ADR-010)
#
# 지켜야 하는 성질 세 가지. 이 파일을 고칠 때 이것부터 확인한다.
#
#   1. **시크릿이 레이어에 남지 않는다.** `VLLM_BASE`는 인증이 없어 URL 자체가
#      자격증명이다 (docs/governance.md "시크릿 취급"). ARG·ENV·COPY 어디로도
#      넣지 않는다 — 레이어는 지워도 이미지 히스토리에 남는다.
#      설정은 전부 **런타임**에 docker-compose의 env_file/environment로만 들어온다.
#   2. **평문 산출물이 이미지에 들어가지 않는다.** `var/`(traces·llm_cache·chroma)는
#      .dockerignore로 빌드 컨텍스트에서 빠지고, 컨테이너에서는 볼륨으로 마운트된다.
#   3. **임베딩 가중치는 고정 리비전으로 빌드 시점에 굽는다.** 런타임에 HuggingFace
#      `main`을 받으면 재현성과 공급망 무결성이 둘 다 깨진다 (ADR-011).
#
# 베이스는 Python 3.14 — 로컬 검증 환경과 같은 버전이다. 컨테이너와 로컬이 다르면
# 측정치를 비교할 수 없다(ADR-002의 모델 고정 원칙이 런타임에도 그대로 적용된다).

# ===========================================================================
# builder — 의존성 설치 + 가중치 다운로드. 이 스테이지는 최종 이미지에 남지 않는다.
# ===========================================================================
FROM python:3.14-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# venv에 넣어두고 runtime 스테이지로 통째로 옮긴다. 빌드 도구가 최종 이미지에
# 따라오지 않게 하려는 것이다.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# torch를 **CPU 전용 인덱스에서 먼저** 설치한다. 기본 PyPI 인덱스는 CUDA 런타임을
# 통째로 끌고 와 이미지가 수 GB 커지는데, 임베딩은 CPU로 돌린다 (ADR-005).
# 버전은 로컬 검증 환경(torch 2.9.0)과 맞춘다.
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.9.0+cpu"

# 의존성만 먼저 복사한다 — 소스가 바뀌어도 이 레이어 캐시가 살아남는다.
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

# --- 임베딩 가중치: 고정 리비전 + sha256 검증 (ADR-011) --------------------
# 실패하면 빌드가 실패한다. 검증을 통과하지 못한 가중치로 도는 이미지를 만드는 것보다
# 빌드가 깨지는 편이 낫다 — 가중치가 다르면 에러 없이 검색 품질만 나빠진다.
COPY infra/model-pin.json ./infra/model-pin.json
COPY scripts/fetch_model.py ./scripts/fetch_model.py
RUN python scripts/fetch_model.py --dest /opt/models/multilingual-e5-small

# ===========================================================================
# runtime
# ===========================================================================
FROM python:3.14-slim AS runtime

# PYTHONPATH: scripts/가 sys.path를 직접 손보지만, `python -m pytest`로도 돌려야 해서
# (컨테이너 스모크 테스트) 레포 루트를 명시한다.
#
# EMBEDDING_LOCAL_PATH: 이미지에 구운 가중치를 가리킨다. **모델 이름이 아니라 경로**라
# 런타임에 네트워크를 타지 않는다. HF_HUB_OFFLINE으로 한 번 더 막는다 —
# 설정 실수로 조용히 온라인 다운로드로 떨어지는 것을 막으려는 것이다.
#
# 여기에 있는 값은 전부 **비시크릿**이다. VLLM_BASE 같은 값은 절대 여기 오지 않는다.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    EMBEDDING_LOCAL_PATH=/opt/models/multilingual-e5-small \
    EMBEDDING_DEVICE=cpu \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/models /opt/models

# root로 돌 이유가 없다. 볼륨으로 붙는 var/ 만 쓰기 권한이 필요하다.
RUN useradd --create-home --uid 10001 app

WORKDIR /app

# 소스 트리. `.dockerignore`가 .env / var/ / .git 을 이미 걷어냈다.
# 그래도 개별 경로를 명시해서 복사한다 — 나중에 레포 루트에 무언가 생겼을 때
# 자동으로 이미지에 딸려 들어가지 않게 하려는 것이다.
COPY --chown=app:app src/ ./src/
COPY --chown=app:app scripts/ ./scripts/
COPY --chown=app:app tests/ ./tests/
COPY --chown=app:app data/ ./data/
COPY --chown=app:app infra/model-pin.json ./infra/model-pin.json
COPY --chown=app:app requirements.txt requirements-dev.txt ./

# 볼륨 마운트 지점. 컨테이너가 non-root라 미리 만들어 소유권을 준다 —
# 없으면 docker가 root 소유로 만들어 쓰기가 실패한다.
RUN mkdir -p /app/var/traces /app/var/llm_cache /app/var/chroma \
    && chown -R app:app /app/var

USER app

# 엔드포인트 도달 여부가 아니라 **프로바이더 계층을 태워서** 확인한다.
# 설정 주입이 잘못되면 여기서 먼저 드러난다.
HEALTHCHECK --interval=60s --timeout=30s --start-period=10s --retries=2 \
    CMD ["python", "scripts/healthcheck.py"]

# 이 컨테이너는 데몬이 아니라 **작업 컨테이너**다. 기본 명령은 설정·엔드포인트
# 점검이고, 실제 리서치는 `docker compose run --rm orchestrator python scripts/run_research.py "질의"`로 돈다.
CMD ["python", "scripts/healthcheck.py"]
