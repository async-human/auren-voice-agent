FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY agent.py tools.py session_state.py memory.py ./

RUN useradd --create-home --uid 10001 auren \
    && chown -R auren:auren /app
USER auren

CMD ["uv", "run", "python", "agent.py", "start"]
