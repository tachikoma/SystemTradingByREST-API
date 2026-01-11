FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# system deps for building wheels and general utilities
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
ENV POETRY_VERSION=1.6.1
RUN curl -sSL https://install.python-poetry.org | POETRY_HOME=/opt/poetry python3 - \
    && ln -s /opt/poetry/bin/poetry /usr/local/bin/poetry

# Copy only lock/project files first for better cache
COPY pyproject.toml poetry.lock* /app/

# Install dependencies (disable virtualenv creation so deps are installed into system python)
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root --only main || poetry install --no-interaction --no-ansi --no-root

# Copy application code
COPY . /app

# Create non-root user
RUN useradd -m appuser || true
USER appuser

CMD ["poetry", "run", "python", "main.py"]
