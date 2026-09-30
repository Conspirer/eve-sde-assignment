FROM python:3.12-slim

# Don't write .pyc files; print logs immediately.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Run as a normal user, not root: if the app were ever compromised,
# the attacker wouldn't have root inside the container.
RUN useradd --create-home appuser
WORKDIR /app
RUN chown appuser:appuser /app

# Install dependencies first: Docker caches this layer, so rebuilding
# after a code change is fast.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser . .
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
