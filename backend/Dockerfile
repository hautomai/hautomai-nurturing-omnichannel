FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "sqlalchemy>=2.0.30" \
        "alembic>=1.13" \
        "psycopg[binary]>=3.1" \
        "pydantic>=2.7" \
        "pydantic-settings>=2.3" \
        "email-validator>=2.1" \
        "celery[redis]>=5.4" \
        "redis>=5.0" \
        "httpx>=0.27" \
        "jinja2>=3.1" \
        "pyjwt>=2.8" \
        "python-multipart>=0.0.9" \
        "pytest>=8.2"

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
