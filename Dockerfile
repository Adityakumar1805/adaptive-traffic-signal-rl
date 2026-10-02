# Container image for the live dashboard.
#
# Written for Hugging Face Spaces (SDK: docker, app_port: 7860); works unchanged on
# Fly.io, Railway, Cloud Run or plain `docker run`, because the port comes from $PORT
# with 7860 as the default.
#
#   docker build -t atsc .
#   docker run --rm -p 7860:7860 atsc          # then open http://localhost:7860
#
# Render does not need this file - it uses render.yaml and its native Python runtime.
# torch is not installed: rl.nn_backend: auto uses the from-scratch NumPy dueling network,
# which loads the same shipped checkpoint (see requirements-deploy.txt).

# same interpreter as render.yaml's PYTHON_VERSION
FROM python:3.12.6-slim

ENV PORT=7860 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# dependencies first, so a code-only change reuses the cached layer
COPY requirements-deploy.txt .
RUN pip install --only-binary=:all: -r requirements-deploy.txt

# only what the server reads at runtime (see .dockerignore for the rest)
COPY asgi.py config.yaml ./
COPY src/ ./src/
COPY models/pretrained/atsc_2x2.pt ./models/pretrained/

# Spaces runs containers as uid 1000; the app writes no files, so read-only is fine
RUN useradd --create-home --uid 1000 atsc && chown -R atsc:atsc /app
USER atsc

EXPOSE 7860

# the slim image has no curl: ask /healthz with the standard library
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '7860'), timeout=4)"

# shell form so $PORT is expanded at container start; `exec` makes uvicorn PID 1, so it
# receives SIGTERM and shuts down cleanly. One worker: the race is one shared simulation.
CMD exec uvicorn asgi:app --host 0.0.0.0 --port ${PORT:-7860} \
    --ws-max-size 65536 --ws-ping-interval 20 --ws-ping-timeout 20
