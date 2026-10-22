#!/bin/sh
set -eu
image=${1:?Usage: sh tests/smoke.sh IMAGE}
container=
cleanup() {
    if [ -n "$container" ]; then
        docker logs "$container"
        docker rm -f "$container" >/dev/null
    fi
}
trap cleanup EXIT
trap 'exit 1' INT TERM

for opcache in enabled disabled; do
    toggle=
    [ "$opcache" = enabled ] || toggle=1
    container=$(docker run -d -e TZ=Asia/Shanghai -e "OPcache=$toggle" "$image")
    docker exec -i "$container" sh -c 'cat > /var/www/html/upgrade-smoke.php' <<'PHP'
<?php
$extensions = ['sodium', 'gd', 'mysqli', 'pdo_mysql', 'xsl', 'zip', 'redis', 'mongodb'];
foreach ($extensions as $extension) {
    if (!extension_loaded($extension)) {
        http_response_code(500);
        exit("Missing extension: $extension");
    }
}
if (PHP_VERSION !== '8.2.35' || PHP_SAPI !== 'fpm-fcgi'
    || date_default_timezone_get() !== 'Asia/Shanghai') {
    http_response_code(500);
    exit('Unexpected PHP runtime');
}
echo PHP_VERSION . ':' . (extension_loaded('Zend OPcache') ? 'enabled' : 'disabled');
PHP
    ready=false
    for attempt in $(seq 1 30); do
        if response=$(docker exec "$container" curl -fsS --max-time 2 http://127.0.0.1/upgrade-smoke.php 2>/dev/null); then
            ready=true
            break
        fi
        sleep 1
    done
    [ "$ready" = true ]
    [ "$response" = "8.2.35:$opcache" ]
    docker exec "$container" php-fpm -t --fpm-config /usr/local/etc/php-fpm.d/www.conf
    docker exec "$container" sh -ec '
        grep -q "^3\.24\." /etc/alpine-release
        certbot --version
        certbot plugins --text --non-interactive | grep -q webroot
    '
    docker exec -i "$container" sh -c 'cat > /etc/nginx/sites-enabled/upgrade-lua.conf' <<'NGINX'
server {
    listen 127.0.0.1:8081;
    location / {
        content_by_lua_block {
            require "resty.core"
            ngx.say("lua-ok")
        }
    }
}
NGINX
    docker exec "$container" nginx -t
    docker exec "$container" nginx -s reload
    ready=false
    for attempt in $(seq 1 30); do
        if response=$(docker exec "$container" curl -fsS --max-time 2 http://127.0.0.1:8081/ 2>/dev/null); then
            ready=true
            break
        fi
        sleep 1
    done
    [ "$ready" = true ]
    [ "$response" = lua-ok ]
    docker exec "$container" supervisorctl status
    docker restart "$container" >/dev/null
    ready=false
    for attempt in $(seq 1 30); do
        if response=$(docker exec "$container" curl -fsS --max-time 2 http://127.0.0.1/upgrade-smoke.php 2>/dev/null); then
            ready=true
            break
        fi
        sleep 1
    done
    [ "$ready" = true ]
    [ "$response" = "8.2.35:$opcache" ]
    docker rm -f "$container" >/dev/null
    container=
    echo "PASS $image: HTTP/FPM, PHP 8.2.35, extensions, timezone, OPcache $opcache, restart, Alpine 3.24, Lua, Supervisor, Certbot"
done
