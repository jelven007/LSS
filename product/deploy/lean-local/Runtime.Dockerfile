# Local incremental build. The base lock/native sources MUST match this checkout.
# Supply this checkout's verified Vite output as the frontend-dist build context.
ARG BASE_IMAGE=tsp-product-runtime:v0.5.1
FROM ${BASE_IMAGE}
USER root
WORKDIR /app
RUN python /app/build_manifest.py verify --root /app --manifest /app/build-manifest.json
RUN rm -rf /app/app /app/static /app/policy-src/src
COPY product/backend/app /app/app
COPY pyproject.toml README.md /app/policy-src/
COPY src /app/policy-src/src
RUN UV_DEFAULT_INDEX=https://pypi.org/simple UV_EXTRA_INDEX_URL= UV_INDEX= \
    uv pip install --python /app/.venv/bin/python --reinstall --no-deps /app/policy-src
COPY --from=frontend-dist / /app/static/
COPY product/tiers.yaml /app/tiers.yaml
COPY product/VERSION /app/VERSION
COPY config /app/config
COPY product/scripts/build_manifest.py /app/build_manifest.py
ARG SOURCE_COMMIT
RUN python /app/build_manifest.py create --root /app \
    --include app --include static --include tiers.yaml --include VERSION \
    --include pyproject.toml --include uv.lock --include native-wheels \
    --include policy-src --include config --include build_manifest.py \
    --version-file /app/VERSION --commit "$SOURCE_COMMIT" \
    --output /app/local-build-manifest.json \
    && mv /app/local-build-manifest.json /app/build-manifest.json \
    && python /app/build_manifest.py verify --root /app --manifest /app/build-manifest.json
