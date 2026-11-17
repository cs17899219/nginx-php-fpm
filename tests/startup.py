"""Negative startup tests; each case must fail BEFORE starting Supervisor."""
import subprocess
import sys
import uuid

image = sys.argv[1]
cases = [
    ('WEBROOT injection', 'export WEBROOT="/var/www/html;bad"', 'WEBROOT must'),
    ('WEBROOT traversal', 'export WEBROOT=/var/www/html/../etc', 'WEBROOT must'),
    ('missing WEBROOT', 'export WEBROOT=/var/www/html/not-present', 'WEBROOT does not exist'),
    ('custom site', 'mkdir -p /var/www/html/conf/nginx; touch /var/www/html/conf/nginx/nginx-site.conf', 'custom nginx-site.conf'),
    ('extra site', 'touch /etc/nginx/sites-enabled/extra.conf', 'unreviewed site'),
    ('modified core', 'echo "# changed" >> /etc/nginx/nginx.conf', 'configuration changed'),
    ('invalid pool', 'echo "invalid_directive = yes" >> /usr/local/etc/php-fpm.d/www.conf', 'unknown entry'),
    ('failed init hook', 'mkdir -p /tmp/hooks; printf "#!/bin/sh\nexit 23\n" > /tmp/hooks/fail; export RUN_SCRIPTS=1 SCRIPTS_DIR=/tmp/hooks', ''),
]
for label, setup, expected in cases:
    name = 'entry-negative-' + uuid.uuid4().hex[:12]
    try:
        result = subprocess.run(['docker', 'run', '--name', name, '--entrypoint', 'bash',
                                 '-e', 'SKIP_COMPOSER=1', '-e', 'TZ=UTC', image,
                                 '-c', setup + '; exec /start.sh'], capture_output=True, text=True, timeout=30, check=False)
        output = result.stdout + result.stderr
        assert result.returncode not in (0, 125, 126, 127), (label, output)
        assert expected in output and 'supervisord started' not in output, (label, output)
        print('PASS:', label)
    finally:
        subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
