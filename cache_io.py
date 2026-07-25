"""Atomic JSON cache-file writes, shared by espn_client.py and weather.py.

Both modules write cache files from request-handler threads (ThreadingHTTPServer
gives every request its own thread) as well as the background refresh loop in
server.py, so two writers can race on the same path. A plain `open(path, "w")`
+ `json.dump` leaves a window where a reader can see a truncated/partial file
if it lands between the write and the flush, or if two writers interleave.
Writing to a temp file in the same directory and using os.replace() avoids
that: the replace is atomic on both POSIX and Windows, and a reader always
sees either the old complete file or the new complete file, never a partial
one.
"""
import json
import os
import tempfile


def atomic_write_json(path, data):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
