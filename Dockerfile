FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install build deps, Poetry, install project dependencies, then remove build deps
# This keeps build tools out of the final image layer and improves caching.
ENV POETRY_VERSION=2.2.1
ENV POETRY_VIRTUALENVS_CREATE=false
COPY pyproject.toml poetry.lock* /app/
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl ca-certificates git \
    && curl -sSL https://install.python-poetry.org | POETRY_HOME=/opt/poetry python3 - \
    && ln -s /opt/poetry/bin/poetry /usr/local/bin/poetry \
    && poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root --only main || poetry install --no-interaction --no-ansi --no-root \
    && apt-get remove --purge -y build-essential git curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip

# Copy application code and entrypoint
COPY . /app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Create non-root user and ensure ownership of app dir
RUN useradd -m appuser || true \
    && chown -R appuser:appuser /app

USER appuser

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "main.py"]
