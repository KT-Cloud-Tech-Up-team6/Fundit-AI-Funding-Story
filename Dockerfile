FROM ghcr.io/astral-sh/uv:0.12.13 AS uv
FROM python:3.12.14-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ARG PRETENDARD_VERSION=1.3.9
ARG PRETENDARD_SHA256=3090ccde0442bb347aa7685d9ba8b17436a60682df6e8f92a9a670de14056e22
RUN mkdir -p /app/fonts && PRETENDARD_VERSION="$PRETENDARD_VERSION" PRETENDARD_SHA256="$PRETENDARD_SHA256" python -c "import hashlib, os, urllib.request; version=os.environ['PRETENDARD_VERSION']; expected=os.environ['PRETENDARD_SHA256']; url=f'https://cdn.jsdelivr.net/npm/pretendard@{version}/dist/public/variable/PretendardVariable.ttf'; data=urllib.request.urlopen(url, timeout=30).read(); actual=hashlib.sha256(data).hexdigest(); assert actual == expected, (actual, expected); open('/app/fonts/PretendardVariable.ttf','wb').write(data)"
ENV PRETENDARD_FONT_PATH=/app/fonts/PretendardVariable.ttf
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
RUN useradd --create-home app && mkdir -p /app/data && chown -R app:app /app/data
USER app
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "funding_story.api:app", "--host", "0.0.0.0", "--port", "8000"]
