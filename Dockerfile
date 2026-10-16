FROM python:3.13.0-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PICGEN_DATA=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY picgen ./picgen
# Run as any non-root user; set it in compose (user: "UID:GID") to own the data folder.
USER 1000:1000
CMD ["python", "-m", "picgen"]
