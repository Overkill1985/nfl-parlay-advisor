FROM python:3.12-slim

# No pip dependencies - the app is stdlib-only (see README).
WORKDIR /app
COPY . .

# Docker's port-forwarding targets the container's own network namespace, not
# 127.0.0.1 inside it, so the process must bind 0.0.0.0 here regardless of
# the app's normal loopback-only default (see server.py). Real exposure
# should still be controlled by how you publish the port at `docker run`
# time - e.g. `-p 127.0.0.1:8787:8787` to keep it local-only, since this app
# has no authentication.
ENV HOST=0.0.0.0 \
    PORT=8787 \
    PYTHONUNBUFFERED=1

# Created here so they're owned by the non-root user below rather than
# appearing as root-owned once the app writes to them at runtime.
RUN mkdir -p cache data && \
    useradd --create-home --uid 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/', timeout=3)" || exit 1

CMD ["python", "server.py"]
