"""An in-flight HTTP request must survive a normal container shutdown."""
import concurrent.futures
import subprocess
import sys
import time
import urllib.error
import urllib.request

image = sys.argv[1]
script = '''printf '%s' '<?php
if (isset($_GET["slow"])) { file_put_contents("/tmp/started", "1"); sleep(15); }
echo "finished";
' > /var/www/html/index.php
exec /start.sh
'''
container = subprocess.check_output(
    ['docker', 'run', '-d', '-p', '127.0.0.1::80', '-e', 'TZ=UTC',
     '--entrypoint', 'sh', image, '-c', script], text=True,
).strip()
try:
    port = subprocess.check_output(
        ['docker', 'port', container, '80/tcp'], text=True,
    ).strip().rsplit(':', 1)[1]
    url = f'http://127.0.0.1:{port}/index.php'

    def request(suffix=''):
        with urllib.request.urlopen(url + suffix, timeout=30) as response:
            return response.read()

    for _ in range(30):
        try:
            if request() == b'finished':
                break
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    else:
        raise AssertionError('Container did not become ready')

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(request, '?slow=1')
        for _ in range(50):
            if subprocess.run(
                ['docker', 'exec', container, 'test', '-e', '/tmp/started'],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ).returncode == 0:
                break
            time.sleep(0.1)
        else:
            raise AssertionError('Slow request never started')
        subprocess.run(['docker', 'stop', '--time', '150', container],
                       check=True, stdout=subprocess.DEVNULL, timeout=160)
        assert pending.result() == b'finished', 'Response lost during shutdown'
    logs = subprocess.check_output(['docker', 'logs', container],
                                   stderr=subprocess.STDOUT, text=True)
    assert 'SIGKILL' not in logs, logs
    print(f'PASS: in-flight request drains without SIGKILL: {image}')
finally:
    subprocess.run(['docker', 'rm', '-f', container], check=False,
                   stdout=subprocess.DEVNULL)
