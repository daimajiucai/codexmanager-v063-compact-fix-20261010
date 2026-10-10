# syntax=docker/dockerfile:1
ARG UPSTREAM_RUNTIME=ghcr.io/qxcnm/codexmanager-service@sha256:3bf5b865abe76ca2da828d82ecd5d31c20f497b9f9c67449790b5bf60270d26f
FROM rust:1-bookworm AS builder
ARG BUILD_JOBS=2
ARG UPSTREAM_SOURCE_COMMIT=764531421428e35b0fbb3aab8f4f777887e7a44a
ENV CARGO_NET_GIT_FETCH_WITH_CLI=true
WORKDIR /src
RUN apt-get update \
    && apt-get install -y --no-install-recommends git python3 ca-certificates pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global url."https://github.com/".insteadOf ssh://git@github.com/
RUN git clone --depth 1 --branch v0.6.3 https://github.com/qxcnm/Codex-Manager.git /src \
    && test "$(git rev-parse HEAD)" = "$UPSTREAM_SOURCE_COMMIT" \
    && git rev-parse HEAD > /upstream-commit.txt
COPY apply_patch.py web_search_history.rs validate_rust.py /hotfix/
RUN BUILD_JOBS=$BUILD_JOBS python3 /hotfix/validate_rust.py /src /validation
RUN CARGO_BUILD_JOBS=$BUILD_JOBS cargo build --locked -p codexmanager-service --release

FROM ${UPSTREAM_RUNTIME}
COPY --from=builder /src/target/release/codexmanager-service /usr/local/bin/codexmanager-service
COPY --from=builder /upstream-commit.txt /usr/local/share/codexmanager-hotfix/upstream-commit.txt
COPY --from=builder /src/LICENSE /usr/local/share/codexmanager-hotfix/UPSTREAM-LICENSE
COPY --from=builder /validation/TEST-RESULTS.json /usr/local/share/codexmanager-hotfix/TEST-RESULTS.json
LABEL io.codexmanager.hotfix.base-version="0.6.3" \
      io.codexmanager.hotfix.name="web-search-history-declaration" \
      io.codexmanager.hotfix.source-commit="764531421428e35b0fbb3aab8f4f777887e7a44a" \
      io.codexmanager.hotfix.validation="cargo-tests-and-isolated-startup-required"
# All runtime settings are inherited from the user's original immutable image.
