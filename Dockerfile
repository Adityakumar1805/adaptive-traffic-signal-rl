# Container image for the live dashboard.
#
# Written for Hugging Face Spaces (SDK: docker, app_port: 7860), and works unchanged on
# Fly.io, Railway, Cloud Run or plain `docker run` because the port comes from $PORT
# with 7860 as the default.
#
#   docker build -t atsc .
#   docker run --rm -p 7860:7860 atsc          # then open http://localhost:7860
#
# Render does not need this file - it uses render.yaml and the native Python runtime.
#
# torch is not installed: rl.nn_backend: auto uses the from-scratch NumPy dueling
# network, which loads the same shipped checkpoint. The image is ~180 MB instead of
# ~2.5 GB and the process peaks around 41 MiB of RSS.

FROM python:3.12-slim

# uvicorn reads $PORT; 7860 is what Hugging Face Spaces expects by default
ENV PORT=7860 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# dependencies first, so a code-only change reuses the cached layer
COPY requirements-deploy.txt .
RUN pip install --no-cache-dir -r requirements-deploy.txt

# only what the server actually needs at runtime (see .dockerignore for the rest)
COPY asgi.py config.yaml ./
COPY src/ ./src/
COPY models/pretrained/ ./models/pretrained/

# Spaces runs containers as uid 1000; the app writes no files, so read-only is fine
RUN useradd --create-home --uid 1000 atsc && chown -R atsc:atsc /app
USER atsc

EXPOSE 7860

# shell form on purpose: $PORT must be expanded at container start, not at build time
CMD uvicorn asgi:app --host 0.0.0.0 --port ${PORT:-7860}
