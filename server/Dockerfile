FROM python:3.12-slim

# Modified for Ram0; see NOTICE and repository history.

ARG VCS_REF
ARG SOURCE_URL=https://github.com/olhapi/ram0
LABEL org.opencontainers.image.source=$SOURCE_URL \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.licenses=Apache-2.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ram0

COPY pyproject.toml poetry.lock README.md LICENSE NOTICE ./
COPY mem0 ./mem0

RUN pip install --no-cache-dir .

WORKDIR /app

COPY server/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

COPY server/ .

RUN mkdir -p /usr/share/licenses/ram0 \
    && cp /opt/ram0/LICENSE /opt/ram0/NOTICE /usr/share/licenses/ram0/

EXPOSE 8000

ENV PYTHONUNBUFFERED=1

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
