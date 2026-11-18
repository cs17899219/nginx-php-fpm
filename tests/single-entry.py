"""Isolated HTTP/FPM tests against the EXACT supplied hardened image (no tag substitution)."""
import json
import pathlib
import subprocess
import sys
import tempfile
import time

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
    name = docker('run', '-d', '--network=none', '--cap-drop=ALL', '--cap-add=CHOWN',
                  '--cap-add=FOWNER', '--cap-add=SETUID', '--cap-add=SETGID',
                  '--security-opt=no-new-privileges:true', '-e', 'WEBROOT=/var/www/html/frontend/web',
                  '-e', 'SKIP_COMPOSER=1', '-e', 'TZ=UTC', '-v', f'{root}:/var/www/html:ro', image)
    try:
        def request(path, body=None, headers=None):
            args = ['docker', 'exec', '-u', '100:101', name, 'curl', '-sS', '--max-time', '3', '-w', '\\n%{http_code}']
            for key, value in (headers or {}).items():
                args.extend(['-H', f'{key}: {value}'])
            if body is not None:
                args.extend(['--data-binary', body.decode()])
            args.append('http://127.0.0.1:8080' + path)
            result = subprocess.run(args, capture_output=True, check=True)
            data, status = result.stdout.rsplit(b'\n', 1)
            return int(status), data

        def ready():
            for _ in range(60):
                try:
                    if request('/index.php')[0] == 200:
                        return
                except (OSError, subprocess.CalledProcessError):
                    pass
                time.sleep(0.5)
            raise AssertionError(docker('logs', name))

        for restart in range(2):
            if restart:
                docker('restart', name)
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
            docker('exec', '-u', '100:101', name, 'sh', '-c', 'test ! -e /tmp/probe-executed && test "$(stat -c %a /run/nginx/php-fpm.sock)" = 660')
            docker('exec', '-u', 'nobody', name, 'sh', '-c', 'test ! -w /run/nginx/php-fpm.sock')
            docker('exec', '-u', 'nginx', name, 'sh', '-c', 'if ( : >> /var/www/html/frontend/web/index.php ) 2>/dev/null; then exit 1; fi')
            config = docker('exec', '-u', '100:101', name, 'nginx', '-e', 'stderr', '-T')
            assert config.count('fastcgi_pass ') == 1
            assert config.count('fastcgi_param SCRIPT_FILENAME $document_root/index.php;') == 1
            fpm = subprocess.run(['docker', 'exec', '-u', '100:101', name, 'php-fpm', '-tt', '--fpm-config', '/usr/local/etc/php-fpm-entry.conf'], capture_output=True, text=True, check=True)
            assert 'security.limit_extensions = .php' in fpm.stderr
        print('PASS: routing, body/auth/cookie, includes, source denial, socket, restart:', image)
    finally:
        docker('rm', '-f', name)
