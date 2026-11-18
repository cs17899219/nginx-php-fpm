#!/usr/bin/env python3
"""Isolated Docker contract tests; no published ports or application network."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

IMAGE = sys.argv[1] if len(sys.argv) > 1 else 'cs17899219/nginx-php-fpm:root-init-test'
CAPS = ['--network=none', '--cap-drop=ALL', '--cap-add=CHOWN', '--cap-add=FOWNER',
        '--cap-add=SETUID', '--cap-add=SETGID', '--security-opt=no-new-privileges:true']


def docker(*args, check=True, timeout=120):
    return subprocess.run(['docker', *args], text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=check, timeout=timeout)


def reject(label, command='exec /start.sh', env=(), image=IMAGE, expected=None):
    args = [item for value in env for item in ('-e', value)]
    result = docker('run', '--rm', *CAPS, *args, '--entrypoint=sh', image,
                    '-c', command, check=False)
    assert result.returncode != 0, (label, result.stdout)
    assert 'ROOT_HOOK_EXECUTED' not in result.stdout, (label, result.stdout)
    assert 'supervisord started' not in result.stdout, (label, result.stdout)
    if expected:
        assert expected in result.stdout, (label, result.stdout)
    print('PASS reject:', label, flush=True)


def running(image, env=()):
    args = [item for value in env for item in ('-e', value)]
    cid = docker('run', '-d', *CAPS, *args, image).stdout.strip()
    try:
        for _ in range(90):
            state = json.loads(docker('inspect', cid).stdout)[0]['State']
            assert state['Running'], docker('logs', cid).stdout
            result = docker('exec', '-u', '100:101', cid, 'curl', '-fsS',
                            'http://127.0.0.1:8080/', check=False)
            if result.returncode == 0:
                return cid, result.stdout
            time.sleep(1)
        raise AssertionError(docker('logs', cid).stdout)
    except BaseException:
        docker('rm', '-f', cid)
        raise


def runtime_checks(cid):
    check = r'''
set -eu
[ "$(stat -c %a /run/nginx/php-fpm.sock)" = 660 ]
[ "$(stat -c %u /etc/nginx/nginx.conf)" = 0 ]
[ ! -w /etc/nginx/nginx.conf ]
[ ! -w /usr/local/etc/php/conf.d/zz-runtime.ini ]
[ ! -w /var/www/html/index.php ]
for status in /proc/[0-9]*/status; do
  case "$(grep '^Name:' "$status")" in
    *nginx*|*php-fpm*|*supervisord*)
      grep -Eq '^Uid:[[:space:]]+100[[:space:]]+100[[:space:]]+100[[:space:]]+100$' "$status"
      grep -Eq '^CapEff:[[:space:]]+0+$' "$status"
      grep -Eq '^CapPrm:[[:space:]]+0+$' "$status"
      grep -Eq '^CapInh:[[:space:]]+0+$' "$status"
      grep -Eq '^NoNewPrivs:[[:space:]]+1$' "$status";;
  esac
done
'''
    docker('exec', '-u', '100:101', cid, 'sh', '-ec', check)


cid, _ = running(IMAGE)
try:
    runtime_checks(cid)
    defaults = json.loads(docker('exec', '-u', '100:101', cid, 'php', '-r',
        'echo json_encode([PHP_VERSION, ini_get("opcache.enable"), extension_loaded("xdebug"), '
        'ini_get("memory_limit"), date_default_timezone_get(), get_loaded_extensions()]);').stdout)
    assert defaults[:5] == ['8.2.35', '1', False, '256M', 'UTC'], defaults
    assert {'pdo_mysql', 'mysqli', 'pdo_pgsql', 'redis', 'mongodb', 'gd', 'intl', 'zip'} <= set(defaults[5]), defaults
    docker('restart', cid)
    # Wait for restart, independently of the first successful startup.
    for _ in range(90):
        result = docker('exec', '-u', '100:101', cid, 'curl', '-fsS', 'http://127.0.0.1:8080/', check=False)
        if result.returncode == 0:
            break
        time.sleep(1)
    else:
        raise AssertionError(docker('logs', cid).stdout)
    runtime_checks(cid)
    print('PASS default/no-hook, HTTP8080, socket660, configs/source protected, uid/caps/nnp, restart', flush=True)
finally:
    docker('rm', '-f', cid)

for value in ['0', '-1', '128M', '1;auto_prepend_file=/tmp/evil', '1048577']:
    reject('invalid memory ' + value, env=['PHP_MEM_LIMIT=' + value])
for key in ['PHPRC', 'PHP_INI_SCAN_DIR', 'PHP_VALUE', 'PHP_ADMIN_VALUE', 'PHP_INI_DIR']:
    reject(key + ' injection', env=[key + '=/var/www/html'])
for setting in ['RUN_SCRIPTS=1', 'SCRIPTS_DIR=/tmp', 'GIT_REPO=x', 'PUID=100',
                'PGID=101', 'SKIP_COMPOSER=0', 'WEBROOT=/var/www/html/../evil',
                'TZ=UTC\nfoo=bar', 'REAL_IP_FROM=127.0.0.1;include /tmp/x', 'ERRORS=yes',
                'ENV=/var/www/html/evil.sh', 'ENV=production', 'ENV=']:
    reject(setting, env=[setting])
reject('root runtime entry', 'exec /start.sh --runtime', expected='runtime requires uid 100')

with tempfile.TemporaryDirectory(prefix='base-root-init-') as temp:
    path = Path(temp)
    (path / 'root.sh').write_text('echo ROOT_HOOK_EXECUTED\nid -u > /run/root-uid\nprintf "%s" "${ENV:-unset}" > /run/root-env\necho root >> /run/root-count\n/usr/local/bin/php -n -r \'if (extension_loaded("redis") || extension_loaded("xdebug") || ini_get("auto_prepend_file") !== "") exit(1);\'\n')
    (path / 'runtime.sh').write_text('test "$(id -u)" = 100\nexport APP_MARK=runtime-export\nif [ "${TEST_FAIL:-0}" = 1 ]; then return 37; fi\n')
    (path / 'index.php').write_text('<?php if (isset($_GET["log"])) error_log("ROOT_INIT_STDERR_PROBE"); header("Content-Type: application/json"); echo json_encode(["export"=>getenv("APP_MARK"),"env"=>getenv("ENV"),"mem"=>ini_get("memory_limit"),"post"=>ini_get("post_max_size"),"upload"=>ini_get("upload_max_filesize"),"tz"=>date_default_timezone_get(),"errors"=>ini_get("display_errors"),"opcache"=>ini_get("opcache.enable"),"expose"=>ini_get("expose_php"),"xdebug"=>extension_loaded("xdebug")]);')
    (path / 'app.conf').write_text('[program:fixture]\ncommand=/bin/sh -c "echo SUPERVISOR_APP_%(ENV_ENV)s_%(ENV_APP_MARK)s; exec sleep 3600"\nstdout_logfile=/dev/stdout\nstdout_logfile_maxbytes=0\nstderr_logfile=/dev/stderr\nstderr_logfile_maxbytes=0\n')
    (path / 'Dockerfile').write_text(f'''FROM {IMAGE}
COPY root.sh runtime.sh /opt/app-init/
COPY app.conf /etc/supervisor/conf.d/app.conf
COPY index.php /var/www/html/index.php
RUN chmod 644 /opt/app-init/*.sh && /usr/bin/python3 -I -S /opt/entry-security/security.py --seal
''')
    fixture = 'root-init-hook-fixture:test'
    docker('build', '--pull=false', '-t', fixture, temp)
    for command in [
        'echo tamper >> /opt/app-init/root.sh',
        'echo tamper >> /opt/app-init/runtime.sh',
        'chmod 666 /opt/app-init/root.sh',
        'chmod 777 /opt/app-init',
        'chmod 777 /opt',
        'chown 100:101 /opt/app-init/runtime.sh',
        'echo tamper >> /usr/local/etc/php/php.ini',
        'echo tamper >> /usr/local/etc/php-fpm.d/www.conf',
        'echo tamper >> /usr/lib/python3.14/os.py',
        'echo tamper >> /opt/entry-security/security.py',
        'echo tamper >> /etc/nginx/fastcgi_params',
        'ln -sf /var/www/html/index.php /opt/app-init/root.sh',
        'ln -sf /tmp /etc/nginx/sites-available',
        'echo extra > /opt/app-init/extra.sh',
        'echo extra > /usr/local/etc/php/conf.d/extra.ini',
        'mkdir -p /var/www/html/conf/nginx; echo x > /var/www/html/conf/nginx/nginx.conf',
    ]:
        reject(command, command + '; exec /start.sh', image=fixture)
    cid, body = running(fixture, ['ENV=Production', 'PHP_MEM_LIMIT=128', 'PHP_POST_MAX_SIZE=32',
                                 'PHP_UPLOAD_MAX_FILESIZE=16', 'TZ=Asia/Tokyo', 'ERRORS=1',
                                 'OPcache=1', 'HIDE_NGINX_HEADERS=0', 'REAL_IP_HEADER=1',
                                 'REAL_IP_FROM=10.0.0.0/8', 'ENABLE_XDEBUG=1',
                                 'PHP_ERRORS_STDERR=1', 'PHP_CATCHALL=1'])
    try:
        values = json.loads(body)
        assert values == {'export': 'runtime-export', 'env': 'Production', 'mem': '128M', 'post': '32M',
                          'upload': '16M', 'tz': 'Asia/Tokyo', 'errors': '1',
                          'opcache': '0', 'expose': '', 'xdebug': True}, values
        runtime_checks(cid)
        docker('exec', '-u', '100:101', cid, 'curl', '-fsS', 'http://127.0.0.1:8080/?log=1')
        logs = docker('logs', cid).stdout
        assert 'ROOT_INIT_STDERR_PROBE' in logs
        assert 'SUPERVISOR_APP_Production_runtime-export' in logs, logs
        assert docker('exec', cid, 'cat', '/run/root-uid').stdout.strip() == '0'
        assert docker('exec', cid, 'cat', '/run/root-env').stdout == 'Production'
        before = docker('exec', cid, 'cat', '/run/root-count').stdout
        result = docker('exec', '-u', '100:101', '-e', 'TEST_FAIL=1', cid, '/start.sh', '--runtime', check=False)
        assert result.returncode == 37, result.stdout
        assert docker('exec', cid, 'cat', '/run/root-count').stdout == before
        headers = docker('exec', '-u', '100:101', cid, 'curl', '-sI', 'http://127.0.0.1:8080/').stdout
        assert 'nginx/' in headers and 'X-Powered-By' not in headers, headers
        assert 'set_real_ip_from 10.0.0.0/8;' in docker('exec', cid, 'cat', '/etc/nginx/sites-available/default.conf').stdout
        print('PASS sealed root hook uid0, isolated PHP -n, runtime exports/failure/no-root-reentry, settings', flush=True)
    finally:
        docker('rm', '-f', cid)
    for app_env in ['Test', 'Development']:
        cid, body = running(fixture, ['ENV=' + app_env])
        try:
            assert json.loads(body)['env'] == app_env, body
            assert docker('exec', cid, 'cat', '/run/root-env').stdout == app_env
            print('PASS ENV propagated to root hook, runtime and FPM:', app_env, flush=True)
        finally:
            docker('rm', '-f', cid)
    # Failed root hook aborts, before runtime/services.
    (path / 'root.sh').write_text('exit 29\n')
    docker('build', '--pull=false', '-t', fixture, temp)
    reject('failed root hook', image=fixture)
    # Supervisor configs are sealed; root hooks must not generate new ones.
    (path / 'root.sh').write_text('echo "[program:unsafe]" > /etc/supervisor/conf.d/generated.conf\n')
    docker('build', '--pull=false', '-t', fixture, temp)
    reject('runtime-generated Supervisor config', image=fixture, expected='integrity mismatch')
    docker('image', 'rm', fixture)
print('All root-init tests passed.', flush=True)
