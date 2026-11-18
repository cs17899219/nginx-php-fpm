#!/bin/sh
set -eu
apk add --no-cache su-exec
# Discard the inherited privileged startup; it is never a runtime dependency.
rm -f /start-base.sh /opt/entry-security/entry-security.sh /opt/entry-security/config.sha256 /run/nginx/nginx.pid
cp /opt/entry-security/start.sh /start.sh
chmod 755 /start.sh
mkdir -p /opt/app-init /etc/supervisor/conf.d
rm -f /etc/nginx/sites-available/default-ssl.conf
# Keep reviewed originals separate from generated settings.
sed -i '/^user /d; /^#user /d' /etc/nginx/nginx.conf
sed -i '1i pid /run/nginx/nginx.pid;\nerror_log /dev/stderr warn;' /etc/nginx/nginx.conf
sed -i '/http {/a\    client_body_temp_path /run/nginx/client;\n    proxy_temp_path /run/nginx/proxy;\n    fastcgi_temp_path /run/nginx/fastcgi;\n    uwsgi_temp_path /run/nginx/uwsgi;\n    scgi_temp_path /run/nginx/scgi;' /etc/nginx/nginx.conf
cp /etc/nginx/nginx.conf /opt/entry-security/nginx.conf.template
sed -i 's|command=/usr/sbin/nginx |command=/usr/sbin/nginx -e /dev/stderr |; s|user=root.*|user=nginx|; s|/dev/shm/supervisor.sock|/run/nginx/supervisor.sock|g; s|/tmp/supervisord|/run/nginx/supervisord|g; s|--fpm-config /usr/local/etc/php-fpm.d/www.conf|--fpm-config /usr/local/etc/php-fpm-entry.conf|' /etc/supervisord.conf
printf 'include=/usr/local/etc/php-fpm.d/www.conf\ninclude=/opt/entry-security/entry-security.conf\n' > /usr/local/etc/php-fpm-entry.conf
sed 's|@WEBROOT@|/var/www/html|g' /opt/entry-security/site.conf.template > /etc/nginx/sites-available/default.conf
printf '; generated at root init\n' > /usr/local/etc/php/conf.d/zz-runtime.ini
printf '; disabled\n' > /usr/local/etc/php/conf.d/xdebug.ini
# Old images may contain these inherited startup outputs.
rm -f /usr/local/etc/php/conf.d/timezone.ini /usr/local/etc/php/conf.d/zz-expose-php.ini
mkdir -p /run/nginx /var/lib/nginx /var/log/nginx
chown nginx:nginx /run/nginx /var/lib/nginx /var/log/nginx
chmod 750 /run/nginx /var/lib/nginx /var/log/nginx
chown -R root:root /opt/entry-security /opt/app-init /etc/nginx /usr/local/etc /etc/supervisor
chmod -R go-w /opt/entry-security /opt/app-init /etc/nginx /usr/local/etc /etc/supervisor
printf '' > /opt/entry-security/bootstrap.sha256
printf '{}\n' > /opt/entry-security/manifest.json
printf '{}\n' > /opt/app-init/manifest.json
/usr/bin/python3 -I -S /opt/entry-security/security.py --seal
