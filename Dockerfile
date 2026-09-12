FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY quorum ./quorum
RUN uv sync --frozen --no-dev
RUN mkdir -p /app/data /app/records
CMD ["python", "-m", "quorum.app"]
