# Server setup

Files in this folder are installed on the production server by hand; nothing
here is applied automatically by a deploy.

## Sign-in rate limit (nginx)

Limits sign-in POSTs (`/cb_login/` and `/admin/login/`) to 10 per second per
IP, with a burst of 200 (the first 50 immediately). Each sign-in costs a
password hash, so this keeps one IP from using up the server's CPU. Page
views and everything else are not limited.

```sh
sudo cp deploy/nginx/login-rate-limit.conf /etc/nginx/conf.d/
sudo cp deploy/nginx/login-limit.conf /etc/nginx/snippets/
```

Then add this line inside your existing `location / { ... }` block, the one
that proxies to gunicorn, next to its `proxy_pass` line:

```nginx
location / {
    include snippets/login-limit.conf;
    # your existing proxy_pass / proxy_set_header lines, unchanged
}
```

```sh
sudo nginx -t && sudo systemctl reload nginx
```

The snippet only adds the limit; requests still go through your own
`proxy_pass`, so it doesn't need to know how gunicorn is reached.

The limit keys on the client IP nginx sees. That is the student's real IP
only while nothing (such as Cloudflare) sits in front of nginx; if you add a
CDN, the key has to change.

### Upgrading from the earlier version

An earlier version of these files used `snippets/login-location.conf`, which
added its own `location` blocks with a hardcoded `proxy_pass` and returned
502 on servers where gunicorn is reached differently. If you installed it:

```sh
sudo rm /etc/nginx/snippets/login-location.conf
```

and delete the `include snippets/login-location.conf;` line (or the commented
copy) from your site config, then install as above.

### Optional: allow /admin/ only from your own IP

The strongest protection for the admin is not exposing it. If you manage the
site from a fixed IP, add this inside the server block (replace the address):

```nginx
location /admin/ {
    allow 203.0.113.10;
    deny all;
    # copy the proxy_pass / proxy_set_header lines from your "location /" block
}
```

## Static file compression and caching (nginx)

nginx serves `/static/` directly. Without this, CSS, JS and SVG files are sent
uncompressed and without cache headers: about 9.2 MB per first-time visitor to
the test page instead of about 2.2 MB, and browsers keep re-checking files they
already have. With it, hashed files from `{% static %}` are cached for good and
unhashed ones for an hour.

```sh
sudo cp deploy/nginx/static-expires.conf /etc/nginx/conf.d/
sudo cp deploy/nginx/static-cache.conf /etc/nginx/snippets/
```

Then add this line inside your existing `location /static/ { ... }` block
(do not add a second `location /static/` block, nginx rejects duplicates):

```nginx
include snippets/static-cache.conf;
```

```sh
sudo nginx -t && sudo systemctl reload nginx
```

Check it worked (expect `Content-Encoding: gzip` and a `Cache-Control: max-age=` line):

```sh
curl -sI -H 'Accept-Encoding: gzip' https://bbtest.shop/static/images/q11.svg
```

The `.gz` files come from `collectstatic`, so run it on every deploy.

## Expired session cleanup (cron)

Django never deletes expired session rows on its own. Run `clearsessions`
daily as the same user that runs gunicorn (so it can write `db.sqlite3`):

```sh
crontab -e
```

and add, with the real path to the project:

```
17 3 * * * /path/to/bb-test/deploy/clearsessions.sh >> /path/to/bb-test/clearsessions.log 2>&1
```

The script uses `venv/`, `env/` or `ENV/` inside the project if one exists,
otherwise `python3`.
