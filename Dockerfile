FROM rust:1.89-slim-bookworm AS rustdx-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-dev python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/maturin
ARG PIP_INDEX_URL
RUN /opt/maturin/bin/pip install --no-cache-dir "maturin==1.9.6"
WORKDIR /build/rustdx-native
COPY native/rustdx_native ./
RUN /opt/maturin/bin/maturin build --locked --release --interpreter python3 --out /wheels

FROM python:3.12.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system --gid 10001 banxia \
    && useradd --system --uid 10001 --gid banxia --create-home banxia

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=rustdx-builder /wheels /tmp/rustdx-wheels
ARG PIP_INDEX_URL
RUN python -m pip install --no-cache-dir /tmp/rustdx-wheels/*.whl ".[production]" \
    && rm -rf /tmp/rustdx-wheels

COPY config ./config

RUN mkdir -p /app/data/wal /app/reports /app/scheduled_reports \
    && chown -R banxia:banxia /app

USER banxia

ENTRYPOINT ["banxia-service"]
