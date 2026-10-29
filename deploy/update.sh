#!/bin/sh
# Pulls the latest main and applies it: requirements, migrations, static
# files, Django checks, then a graceful gunicorn reload and a health check.
#
#   deploy/update.sh
#
# Options (environment variables):
#   GUNICORN_SERVICE=name  restart this systemd service (with sudo) instead of
#                          sending HUP to the running gunicorn master
#   PORT=8000              port gunicorn listens on, for the health check
set -eu

cd "$(dirname "$0")/.."
PORT="${PORT:-8000}"

if [ -x venv/bin/python ]; then
    PY=venv/bin/python
elif [ -x env/bin/python ]; then
    PY=env/bin/python
elif [ -x ENV/bin/python ]; then
    PY=ENV/bin/python
else
    PY=python3
fi

step() { printf '\n==> %s\n' "$1"; }

step "Pulling latest main"
before=$(git rev-parse HEAD)
if ! git pull --ff-only; then
    echo "git pull failed. If this server has local changes or commits, check 'git status' and 'git log origin/main..HEAD' first." >&2
    exit 1
fi
after=$(git rev-parse HEAD)
if [ "$before" = "$after" ]; then
    echo "Already up to date ($(git log -1 --format='%h %s'))."
else
    git log --oneline "$before..$after"
fi

step "Installing requirements"
"$PY" -m pip install -q --disable-pip-version-check -r requirements.txt

step "Running migrations"
"$PY" manage.py migrate --noinput

step "Collecting static files"
"$PY" manage.py collectstatic --noinput -v 0

step "Checking the project"
"$PY" manage.py check

step "Reloading gunicorn"
if [ -n "${GUNICORN_SERVICE:-}" ]; then
    sudo systemctl restart "$GUNICORN_SERVICE"
    echo "Restarted $GUNICORN_SERVICE."
else
    # The oldest matching process is the gunicorn master; HUP makes it start
    # fresh workers with the new code and let the old ones finish their requests
    master=$(pgrep -o -f 'gunicorn.*bluebook_project.wsgi' || true)
    if [ -z "$master" ]; then
        echo "No running gunicorn found. Start it, or set GUNICORN_SERVICE to restart its systemd service." >&2
        exit 1
    fi
    kill -HUP "$master"
    echo "Sent HUP to gunicorn master (pid $master)."
fi

step "Health check"
code=000
for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    # Host and X-Forwarded-Proto as nginx sends them, so ALLOWED_HOSTS and
    # the HTTPS redirect behave as in production
    code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: bbtest.shop' \
        -H 'X-Forwarded-Proto: https' "http://127.0.0.1:$PORT/cb_login/" || true)
    [ "$code" = "200" ] && break
done
if [ "$code" = "200" ]; then
    echo "Site is answering (HTTP 200)."
else
    echo "Health check failed (HTTP $code). Check gunicorn's logs." >&2
    exit 1
fi

if [ "$before" != "$after" ] && ! git diff --quiet "$before" "$after" -- deploy/nginx deploy/clearsessions.sh; then
    step "Note"
    echo "Files in deploy/ changed. nginx config and cron aren't applied by this script; see deploy/README.md:"
    git diff --name-only "$before" "$after" -- deploy/nginx deploy/clearsessions.sh
fi

printf '\nDone: %s\n' "$(git log -1 --format='%h %s')"
