FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    RECIPE_HOST=0.0.0.0 RECIPE_DATABASE_PATH=/data/recipes.sqlite3
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 recipe \
    && mkdir /data && chown recipe:recipe /data
USER recipe
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT',os.environ.get('RECIPE_PORT','8000'))+'/readyz',timeout=2)"
CMD ["recipe-mcp", "--transport", "http"]
