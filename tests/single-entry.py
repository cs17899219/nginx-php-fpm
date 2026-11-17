"""Isolated HTTP/FPM tests against the EXACT supplied hardened image (no tag substitution)."""
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

image = sys.argv[1]

def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()

with tempfile.TemporaryDirectory() as temp:
    root = pathlib.Path(temp)
    root.chmod(0o755)  # Native Linux workers must traverse the bind-mounted fixture.
    web = root / 'frontend/web'
    web.mkdir(parents=True)
    (web / 'index.php').write_text('''<?php
require __DIR__ . '/internal.php';
header('Content-Type: application/json');
echo json_encode(['uri'=>$_SERVER['REQUEST_URI'], 'method'=>$_SERVER['REQUEST_METHOD'],
 'body'=>file_get_contents('php://input'), 'query'=>$_GET, 'post'=>$_POST,
 'auth'=>$_SERVER['HTTP_AUTHORIZATION'] ?? '', 'cookie'=>$_COOKIE,
 'script'=>$_SERVER['SCRIPT_FILENAME'], 'included'=>INCLUDED]);
''')
    (web / 'internal.php').write_text('<?php const INCLUDED = true;')
    probes = ['probe.php', 'uploads/probe.php', 'uploads/index.php', 'probe.PHP',
              'probe.php5', 'probe.phtml', 'probe.phar', '.well-known/probe.php',
              '.well-known/probe.txt', '.env', '.user.ini', '.git/config', '.hidden/a.css']
    for name in probes:
        p = web / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('<?php file_put_contents("/tmp/probe-executed", "yes"); echo "PROBE_MARKER";')
    (web / '.user.ini').write_text('; PROBE_MARKER\n')
    (web / 'style.css').write_text('body { color: black; }')
    name = docker('run', '-d', '-p', '127.0.0.1::80', '-e', 'WEBROOT=/var/www/html/frontend/web',
                  '-e', 'SKIP_COMPOSER=1', '-e', 'TZ=UTC', '-v', f'{root}:/var/www/html:ro', image)
    try:
        port = docker('port', name, '80/tcp').rsplit(':', 1)[1]
        base = f'http://127.0.0.1:{port}'

        def request(path, body=None, headers=None):
            try:
                with urllib.request.urlopen(urllib.request.Request(base + path, data=body, headers=headers or {}), timeout=3) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as error:
                return error.code, error.read()

        def ready():
            for _ in range(60):
                try:
                    if request('/index.php')[0] == 200:
                        return
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(0.5)
            raise AssertionError(docker('logs', name))

        for restart in range(2):
            if restart:
                docker('restart', name)
                port = docker('port', name, '80/tcp').rsplit(':', 1)[1]
                base = f'http://127.0.0.1:{port}'
            ready()
            for path in ['/controller/action?source=app', '/csapi/login/login?source=app', '/index.php?r=health']:
                payload = b'{"hello":"world"}'
                status, data = request(path, payload, {'Content-Type': 'application/json', 'Authorization': 'Bearer fixture', 'Cookie': 'session=fixture'})
                result = json.loads(data)
                assert status == 200 and result['uri'] == path and result['method'] == 'POST', result
                assert result['body'] == payload.decode() and result['auth'] == 'Bearer fixture', result
                assert result['cookie']['session'] == 'fixture' and result['included'], result
                assert result['script'] == '/var/www/html/frontend/web/index.php', result
            status, data = request('/form', b'a=hello%20world', {'Content-Type': 'application/x-www-form-urlencoded'})
            assert status == 200 and json.loads(data)['post'] == {'a': 'hello world'}
            for path in probes + ['probe.php/a', 'index.php/a', 'probe%2ephp', 'uploads//probe.php', 'probe.php/a.css']:
                status, data = request('/' + path)
                assert status in (403, 404) and b'PROBE_MARKER' not in data and b'<?php' not in data, (path, status, data)
            assert request('/style.css') == (200, b'body { color: black; }')
            docker('exec', name, 'sh', '-c', 'test ! -e /tmp/probe-executed && test "$(stat -c %a /var/run/php-fpm.sock)" = 660')
            docker('exec', '-u', 'nobody', name, 'sh', '-c', 'test ! -w /var/run/php-fpm.sock')
            docker('exec', '-u', 'nginx', name, 'sh', '-c', 'if ( : >> /var/www/html/frontend/web/index.php ) 2>/dev/null; then exit 1; fi')
            config = docker('exec', name, 'nginx', '-T')
            assert config.count('fastcgi_pass ') == 1
            assert config.count('fastcgi_param SCRIPT_FILENAME $document_root/index.php;') == 1
            fpm = subprocess.run(['docker', 'exec', name, 'php-fpm', '-tt', '--fpm-config', '/usr/local/etc/php-fpm-entry.conf'], capture_output=True, text=True, check=True)
            assert 'security.limit_extensions = .php' in fpm.stderr
        print('PASS: routing, body/auth/cookie, includes, source denial, socket, restart:', image)
    finally:
        docker('rm', '-f', name)
