#!/bin/sh
set -eu
# Non-interactive ash does not source BASH_ENV/ENV. Never enter a root bash.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
unset BASH_ENV CDPATH
# ENV is application data, not a shell startup path. Preserve only the agreed
# downstream values; non-interactive ash never sources this variable.
if [ "${ENV+x}" = x ]; then
  case "$ENV" in
    Production|Test|Development) ;;
    *) echo 'start: ENV must be Production, Test or Development' >&2; exit 1 ;;
  esac
fi
cd /
# BusyBox and its loader are the trusted image bootstrap. Verify interpreter
# dependencies before importing Python or executing any application hook.
for parent in / /opt /etc; do
  [ "$(/bin/busybox stat -c '%u:%a' "$parent")" = '0:755' ] || {
    echo "start: unsafe bootstrap parent $parent" >&2; exit 1;
  }
done
bad=$(/bin/busybox find -L /bin /sbin /lib /usr /opt/entry-security /opt/app-init /etc/nginx /etc/supervisor \
  ! -type l \( ! -user root -o -perm -020 -o -perm -002 \) -print -quit)
[ -z "$bad" ] || { echo "start: writable or unowned dependency: $bad" >&2; exit 1; }
/bin/busybox sha256sum -c /opt/entry-security/bootstrap.sha256 >/dev/null
case "${1:-}" in
  '')
    [ "$(id -u)" = 0 ] || { echo 'start: root initialization requires uid 0' >&2; exit 1; }
    /usr/bin/python3 -I -S /opt/entry-security/security.py --init
    exec /sbin/su-exec nginx:nginx /start.sh --runtime
    ;;
  --runtime)
    [ "$#" = 1 ] && [ "$(id -u)" = 100 ] || { echo 'start: runtime requires uid 100' >&2; exit 1; }
    /usr/bin/python3 -I -S /opt/entry-security/security.py --verify
    if [ -f /opt/app-init/runtime.sh ]; then
      . /opt/app-init/runtime.sh
    fi
    cd /
    /usr/sbin/nginx -e /dev/stderr -t
    /usr/local/sbin/php-fpm -t --fpm-config /usr/local/etc/php-fpm-entry.conf
    exec /usr/bin/supervisord -n -c /etc/supervisord.conf
    ;;
  *) echo 'start: unsupported arguments' >&2; exit 1 ;;
esac
