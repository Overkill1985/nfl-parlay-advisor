# Matches the Python the app is developed and tested against, so a stdlib
# behavior difference can't hide between host and container. The tag floats
# within 3.14, so each rebuild picks up upstream Python/Debian patches -
# but Docker Hub doesn't rebuild the instant a fix lands upstream, so a
# patched Debian package can sit unpicked-up in the floating tag for days
# (hit this directly: CVE-2026-13221/-42496/-8376 in perl-base, fixed in
# Debian's repos but not yet in the image Docker Hub was serving). Applying
# `apt-get upgrade` at build time closes that gap regardless of when the
# base image itself gets rebuilt.
FROM python:3.14-slim

RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

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
