"""Isolated runtime regressions. Usage: python3 tests/runtime-settings.py REBUILT_IMAGE.

No published ports, host mounts, or container network access; no real git push.
"""
import contextlib
import json
import subprocess
import sys
import time
import uuid

image = sys.argv[1]


def docker(*args, check=True):
    return subprocess.run(['docker', *args], capture_output=True, text=True,
                          timeout=60, check=check)


@contextlib.contextmanager
def container(setup='', env=(), startup=True):
    name = 'runtime-settings-' + uuid.uuid4().hex[:12]
    args = ['run', '-d', '--name', name, '--network', 'none', '--entrypoint', 'bash',
            '-e', 'TZ=UTC', '-e', 'SKIP_COMPOSER=1', '-e', 'SKIP_CHOWN=1']
    for value in env:
        args += ['-e', value]
    command = (setup + '\n' if setup else '') + ('exec /start.sh' if startup else 'exec sleep 300')
    try:
        docker(*args, image, '-ec', command)
        yield name
    finally:
        docker('rm', '-f', name, check=False)


def execute(name, script, check=True):
    return docker('exec', name, 'bash', '-ec', script, check=check)


def response(name):
    for _ in range(60):
        result = docker('exec', name, 'curl', '-fsS', '--max-time', '2',
                        '-D', '/tmp/runtime-headers', 'http://127.0.0.1/index.php', check=False)
        if result.returncode == 0:
            return result.stdout
        if docker('inspect', '-f', '{{.State.Running}}', name).stdout.strip() != 'true':
            break
        time.sleep(0.5)
    raise AssertionError(docker('logs', name).stdout + docker('logs', name).stderr)


# A disabled pull must fail without touching the source tree, even from another cwd.
with container(startup=False) as name:
    result = execute(name, '''
find /var/www/html -exec stat -c '%n %u:%g %a %s %Y' {} \\; | sort > /tmp/before
cd /tmp
set +e
/usr/bin/pull > /tmp/pull-output 2>&1
status=$?
set -e
[ "$status" = 1 ]
grep -q 'disabled.*immutable image' /tmp/pull-output
find /var/www/html -exec stat -c '%n %u:%g %a %s %Y' {} \\; | sort > /tmp/after
cmp /tmp/before /tmp/after
# Missing push identities must fail; no git command may execute.
mkdir /tmp/bin
cat > /tmp/bin/git <<'SH'
#!/bin/sh
[ "$PWD" = /var/www/html ] || exit 98
printf '%s\\n' "$1" >> /tmp/git-calls
case "$1" in
  add) exit "${FAKE_ADD:-0}" ;;
  diff) exit "${FAKE_DIFF:-0}" ;;
  commit) exit "${FAKE_COMMIT:-0}" ;;
esac
SH
chmod +x /tmp/bin/git
for missing in GIT_EMAIL GIT_NAME; do
  set +e
  env PATH="/tmp/bin:$PATH" GIT_EMAIL=fixture@example.invalid GIT_NAME=Fixture env -u "$missing" /usr/bin/push > /tmp/push-output 2>&1
  status=$?
  set -e
  [ "$status" = 1 ]
done
[ ! -e /tmp/git-calls ]
export PATH="/tmp/bin:$PATH" GIT_EMAIL=fixture@example.invalid GIT_NAME=Fixture WEBROOT=/tmp
for diff in 0 1; do
  : > /tmp/git-calls
  FAKE_DIFF=$diff /usr/bin/push
  grep -qx push /tmp/git-calls
  if [ "$diff" = 1 ]; then grep -qx commit /tmp/git-calls; else ! grep -qx commit /tmp/git-calls; fi
done
for failure in FAKE_ADD=23 FAKE_COMMIT=23; do
  : > /tmp/git-calls
  if env FAKE_DIFF=1 "$failure" /usr/bin/push; then exit 1; fi
  ! grep -qx push /tmp/git-calls
done
''')
print('PASS: pull makes no source/ownership changes; push identity/root/failures/clean tree (stub git)')

# The shipped default response must not be phpinfo() or reveal process environment.
with container(env=('RUNTIME_SECRET=runtime-secret-must-not-leak',)) as name:
    body = response(name)
    assert body.strip(), 'empty default response'
    assert all(marker not in body.lower() for marker in
               ('runtime-secret-must-not-leak', 'phpinfo()', 'php version', 'php variables', '<?php')), body
    assert 'x-powered-by:' not in execute(name, 'cat /tmp/runtime-headers').stdout.lower()
    execute(name, "php -r 'exit(extension_loaded(\"xdebug\") ? 1 : 0);'")
print('PASS: default response has no phpinfo/secrets/PHP header; Xdebug defaults off')

probe = '''cat > /var/www/html/index.php <<'PHP'
<?php
header('Content-Type: application/json');
echo json_encode(['sapi' => PHP_SAPI, 'memory' => ini_get('memory_limit'),
 'xdebug' => extension_loaded('xdebug'), 'expose_php' => ini_get('expose_php')]);
PHP
mkdir -p /root/.ssh
printf 'fixture-known-host\\n' > /root/.ssh/known_hosts
# Ensure memory replacement does not depend on the image's original default.
sed -i 's/^memory_limit.*/memory_limit = 384M/' /usr/local/etc/php/conf.d/docker-vars.ini
'''
for enabled in ('0', '1'):
    with container(probe, (f'ENABLE_XDEBUG={enabled}', 'PHP_MEM_LIMIT=192',
                           'XDEBUG_CONFIG=client_host=127.0.0.1')) as name:
        for restart in range(2):
            if restart:
                docker('restart', name)
            actual = json.loads(response(name))
            assert actual == {'sapi': 'fpm-fcgi', 'memory': '192M',
                              'xdebug': enabled == '1', 'expose_php': ''}, actual
            execute(name, '''
[ "$(cat /root/.ssh/known_hosts)" = fixture-known-host ]
ssh -G example.invalid 2>/dev/null | grep -Eq '^stricthostkeychecking (yes|true)$'
[ "$(grep -c '^memory_limit' /usr/local/etc/php/conf.d/docker-vars.ini)" = 1 ]
[ ! -e /usr/local/etc/php/conf.d/docker-php-ext-xdebug.ini ]
''')
            assert 'already loaded' not in docker('logs', name).stderr.lower()
        # Exercise changing the setting in the SAME writable container, not just fresh starts.
        execute(name, "sed -i '/^# Use the same single loader path/i ENABLE_XDEBUG="
                + ('0' if enabled == '1' else '1') + "' /start-base.sh")
        docker('restart', name)
        assert json.loads(response(name))['xdebug'] == (enabled != '1')
print('PASS: real FPM memory/expose_php, Xdebug enable/disable/restart, strict SSH and known_hosts')

for setting in ('PHP_MEM_LIMIT', 'PHP_POST_MAX_SIZE', 'PHP_UPLOAD_MAX_FILESIZE'):
    for value in ('0', '-1', '192M', '1;expose_php=On'):
        with container(env=(f'{setting}={value}',)) as name:
            assert docker('wait', name).stdout.strip() == '1'
            logs = docker('logs', name)
            assert f'{setting} must be a positive integer in MiB' in logs.stderr
            assert 'supervisord started' not in logs.stdout + logs.stderr
print('PASS: invalid numeric size settings fail before services start')

fixture = '''
cat > /var/www/html/composer.json <<'JSON'
{"name":"fixture/runtime","require":{},"repositories":{"packagist.org":false}}
JSON
composer update --no-install --no-plugins --no-scripts --no-interaction --working-dir=/var/www/html
[ -f /var/www/html/composer.lock ]
'''
for mode in ('development', 'production'):
    with container(fixture, ('SKIP_COMPOSER=', 'COMPOSER_DISABLE_NETWORK=1',
                             'COMPOSER_ALLOW_SUPERUSER=1', f'APPLICATION_ENV={mode}')) as name:
        response(name)
        execute(name, 'test -f /var/www/html/vendor/autoload.php')
        logs = docker('logs', name)
        assert 'prestissimo' not in logs.stdout + logs.stderr
print('PASS: composer.lock startup in development/production, empty dependencies, no network')
