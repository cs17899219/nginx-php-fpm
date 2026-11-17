#!/bin/bash
set -euo pipefail

fail() { echo "entry-security: $*" >&2; exit 1; }
webroot=${WEBROOT:-/var/www/html}
# Validate before the base startup script interpolates WEBROOT into sed.
[[ "$webroot" =~ ^/var/www/html(/[a-zA-Z0-9_-][a-zA-Z0-9_.-]*)*$ ]] || fail 'WEBROOT must be a safe path under /var/www/html'
[[ "$webroot" != */.. && "$webroot" != */../* ]] || fail 'WEBROOT traversal is forbidden'
for file in nginx.conf nginx-site.conf nginx-site-ssl.conf; do
    [[ ! -e /var/www/html/conf/nginx/$file ]] || fail "custom $file requires a reviewed single-entry migration"
done
# Never let the inherited startup recursively give PHP ownership of source code.
export SKIP_CHOWN=1

if [[ "${1:-}" != --final ]]; then
    exec /start-base.sh "$@"
fi

[[ -d "$webroot" ]] || fail 'WEBROOT does not exist'
resolved=$(readlink -f "$webroot")
[[ "$resolved" == /var/www/html || "$resolved" == /var/www/html/* ]] || fail 'WEBROOT resolves outside /var/www/html'
# Only this audited HTTP configuration is supported; do not silently erase extra sites.
sha256sum -c /opt/entry-security/config.sha256 || fail 'core Nginx/FastCGI/Supervisor configuration changed; migrate explicitly'
for file in /etc/nginx/sites-enabled/* /etc/nginx/sites-available/*; do
    [[ "$file" == /etc/nginx/sites-enabled/default.conf || "$file" == /etc/nginx/sites-available/default.conf ]] || fail "unreviewed site: $file"
done
[[ $(readlink -f /etc/nginx/sites-enabled/default.conf) == /etc/nginx/sites-available/default.conf ]] || fail 'unexpected default site link'
sed "s|@WEBROOT@|$webroot|g" /opt/entry-security/site.conf.template > /etc/nginx/sites-available/default.conf
if [[ "${REAL_IP_HEADER:-}" == 1 ]]; then
    from=${REAL_IP_FROM:-172.16.0.0/12}
    [[ "$from" =~ ^[0-9a-fA-F.:/]+$ ]] || fail 'invalid REAL_IP_FROM'
    sed -i -e 's/#real_ip_header/real_ip_header/' -e "s|#set_real_ip_from 172.16.0.0/12;|set_real_ip_from $from;|" /etc/nginx/sites-available/default.conf
fi
# Supervisor used to load www.conf directly. Explicit include order preserves pool tuning.
printf 'include=/usr/local/etc/php-fpm.d/www.conf\ninclude=/opt/entry-security/entry-security.conf\n' > /usr/local/etc/php-fpm-entry.conf
nginx -t
php-fpm -t --fpm-config /usr/local/etc/php-fpm-entry.conf
exec /usr/bin/supervisord -n -c /etc/supervisord.conf
