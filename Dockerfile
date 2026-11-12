FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/
ENV PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
# The workflow launches headed Chromium (no HeadlessChrome user agent for reCAPTCHA),
# so it runs on an Xvfb virtual display; pydub needs ffmpeg/ffprobe to decode the
# audio challenge. This layer stays cached across src changes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb xauth ffmpeg \
    && patchright install --with-deps --no-shell chromium \
    && rm -rf /var/lib/apt/lists/*
COPY src ./src
RUN uv sync --locked --no-dev --no-editable
# Bake the Vosk model so the first real solve does not download 39 MB mid-race.
RUN python -c "import vosk; vosk.Model(model_name='vosk-model-small-en-us-0.15')"
CMD ["xvfb-run", "--auto-servernum", "bcn-half"]
