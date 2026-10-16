FROM python:3.13.0-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/levoniust/picgen-bot" \
      org.opencontainers.image.description="PIC_GEN: Midjourney-style Discord bot for your own ComfyUI" \
      org.opencontainers.image.licenses="MIT"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PICGEN_DATA=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY picgen ./picgen
# Readable by whatever user compose runs it as (the build context may have restrictive permissions).
RUN chmod -R a+rX /app
# Run as any non-root user; set it in compose (user: "UID:GID") to own the data folder.
USER 1000:1000
CMD ["python", "-m", "picgen"]
