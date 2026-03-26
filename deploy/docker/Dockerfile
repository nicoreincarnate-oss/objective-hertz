# Stage 1: Build Python package
FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml README.md ./
COPY openjarvis/ openjarvis/
COPY shared/ shared/
COPY titan/ titan/
COPY hermes/ hermes/
COPY clawdbot/ clawdbot/
COPY tools/ tools/
COPY perseus/ perseus/
COPY conway/ conway/
COPY ruflo/ ruflo/

RUN pip install --no-cache-dir uv && \
    uv pip install --system ".[server]"

# Stage 2: Runtime
FROM python:3.12-slim

COPY --from=builder /usr/local /usr/local
COPY --from=builder /app /app
WORKDIR /app

EXPOSE 8000

ENTRYPOINT ["jarvis"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
