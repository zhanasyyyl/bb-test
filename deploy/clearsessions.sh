#!/bin/sh
# Deletes expired Django sessions. Run daily from cron (see deploy/README.md).
set -e
cd "$(dirname "$0")/.."

if [ -x venv/bin/python ]; then
    PY=venv/bin/python
elif [ -x env/bin/python ]; then
    PY=env/bin/python
elif [ -x ENV/bin/python ]; then
    PY=ENV/bin/python
else
    PY=python3
fi

exec "$PY" manage.py clearsessions
