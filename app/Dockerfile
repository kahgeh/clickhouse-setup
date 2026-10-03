# syntax=docker/dockerfile:1
FROM docker.io/library/rust:1.97-bookworm AS builder
RUN apt-get update \
    && apt-get install -y --no-install-recommends protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY contract ./contract
COPY telemetry ./telemetry
COPY backend ./backend
COPY web ./web
COPY loadgen ./loadgen
COPY trace_macros ./trace_macros
RUN cargo build --release --locked -p web -p backend -p loadgen

FROM docker.io/library/debian:bookworm-slim
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=builder /src/target/release/web /usr/local/bin/web
COPY --from=builder /src/target/release/backend /usr/local/bin/backend
COPY --from=builder /src/target/release/loadgen /usr/local/bin/loadgen
USER app
WORKDIR /tmp
EXPOSE 8081 50051
# No default entrypoint: compose `command` picks web, backend or loadgen.
CMD ["web"]
