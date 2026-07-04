FROM python:3.12-slim

WORKDIR /code

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install "poetry==${POETRY_VERSION}"

# Copied separately so dependency install is cached unless pyproject/lock change.
# poetry.lock* matches nothing (no build failure) if no lock file has been
# committed yet - poetry will resolve straight from the pinned versions in
# pyproject.toml instead.
COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-root --only main

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
