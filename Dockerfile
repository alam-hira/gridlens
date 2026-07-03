# 3.14 matches the interpreter requirements-lock.txt was pinned for (the package
# itself supports 3.11+; the lock's exact versions are resolved for this one).
FROM python:3.14-slim

WORKDIR /app

# Install the exact locked dependency versions first (better layer caching and a
# reproducible image), then the package itself without re-resolving.
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8000

CMD ["uvicorn", "gridlens.api:app", "--host", "0.0.0.0", "--port", "8000"]
