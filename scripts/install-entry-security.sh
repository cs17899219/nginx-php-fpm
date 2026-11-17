#!/bin/sh
set -eu
# Fail on a changed base startup contract rather than installing a hook that never runs.
test "$(grep -c '^exec /usr/bin/supervisord -n -c /etc/supervisord.conf$' /start.sh)" = 1
# The base ships an inactive SSL example; it must not become a second PHP entry.
test ! -e /etc/nginx/sites-enabled/default-ssl.conf
rm -f /etc/nginx/sites-available/default-ssl.conf
mv /start.sh /start-base.sh
sed -i '2a set -e' /start-base.sh
# The inherited ERRORS setting appends a pool directive on every restart.
sed -i '/^# Display PHP error/ i sed -i "/^php_flag.*display_errors.* = /d" /usr/local/etc/php-fpm.d/www.conf' /start-base.sh
sed -i 's|^exec /usr/bin/supervisord -n -c /etc/supervisord.conf$|exec /start.sh --final|' /start-base.sh
cp /opt/entry-security/entry-security.sh /start.sh
chmod 755 /start.sh /start-base.sh
sed -i 's|--fpm-config /usr/local/etc/php-fpm.d/www.conf|--fpm-config /usr/local/etc/php-fpm-entry.conf|' /etc/supervisord.conf
grep -q -- '--fpm-config /usr/local/etc/php-fpm-entry.conf' /etc/supervisord.conf
# The base leaves the Nginx worker user implicit; 0660 requires an explicit identity.
sed -i '1i user nginx nginx;' /etc/nginx/nginx.conf
sha256sum /etc/nginx/nginx.conf /etc/nginx/fastcgi_params /etc/supervisord.conf > /opt/entry-security/config.sha256
sed 's|@WEBROOT@|/var/www/html|g' /opt/entry-security/site.conf.template > /etc/nginx/sites-available/default.conf
printf 'include=/usr/local/etc/php-fpm.d/www.conf\ninclude=/opt/entry-security/entry-security.conf\n' > /usr/local/etc/php-fpm-entry.conf
