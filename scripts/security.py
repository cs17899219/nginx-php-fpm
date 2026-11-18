#!/usr/bin/python3
"""Build-time sealing and fail-closed root initialization. No application imports."""
import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

BASE = Path('/opt/entry-security')
APP = Path('/opt/app-init')
ROOTS = ['/bin', '/sbin', '/lib', '/usr', '/etc/nginx', '/etc/supervisord.conf',
         '/etc/supervisor', '/etc/passwd', '/etc/group', '/etc/ld-musl-x86_64.path',
         '/etc/ld-musl-aarch64.path', '/start.sh', str(BASE)]
# Only these outputs are rewritten, never consumed by a root interpreter.
GENERATED = {'/etc/nginx/nginx.conf', '/etc/nginx/sites-available/default.conf',
             '/usr/local/etc/php/conf.d/zz-runtime.ini',
             '/usr/local/etc/php/conf.d/xdebug.ini'}
MANIFESTS = {str(BASE / 'manifest.json'), str(APP / 'manifest.json'), str(BASE / 'bootstrap.sha256')}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def protected(path):
    """Check every parent and link target, not just the leaf's ownership."""
    path = Path(path)
    for item in [path, *path.parents]:
        s = item.lstat()
        require(s.st_uid == 0, f'not root-owned: {item}')
        if stat.S_ISLNK(s.st_mode):
            target = item.resolve()
            # Packaged tools may contain dangling optional links. Protect the
            # nearest existing parent; a later added target changes the seal.
            while not target.exists():
                target = target.parent
            protected(target)
        else:
            require(not s.st_mode & 0o022, f'writable protected path: {item}')


def snapshot(roots):
    result = {}
    seen = set()
    def visit(path):
        name = str(path)
        if name in seen:
            return
        seen.add(name)
        if name in MANIFESTS:
            protected(path)
            return
        protected(path)
        s = path.lstat()
        entry: list[int | str] = [stat.S_IMODE(s.st_mode)]
        if path.is_symlink():
            require(not (path == APP or APP in path.parents), f'application links are forbidden: {path}')
            entry += ['link', os.readlink(path)]
            target = path.resolve()
            if target.exists():
                visit(target)
            else:
                result[str(target)] = ['absent']
        elif path.is_dir():
            entry += ['dir']
            # Container runtimes may inject local CA data (e.g. OrbStack).
            # Certificates are not executable startup dependencies.
            if name not in {'/usr/local/share/ca-certificates', '/usr/local/var', '/lib/apk'}:
                for child in sorted(path.iterdir()):
                    visit(child)
        else:
            require(path.is_file(), f'not a regular file: {path}')
            entry += ['file', '' if name in GENERATED else hashlib.sha256(path.read_bytes()).hexdigest()]
        result[name] = entry
    for root in roots:
        if os.path.lexists(root):
            visit(Path(root))
    return result


def integrity(seal=False):
    for hook in [APP / 'root.sh', APP / 'runtime.sh']:
        if hook.exists():
            require(stat.S_IMODE(hook.stat().st_mode) in (0o644, 0o755), f'hooks require mode 0644 or 0755: {hook}')
    for roots, manifest in [(ROOTS, BASE / 'manifest.json'), ([str(APP)], APP / 'manifest.json')]:
        protected(manifest)
        actual = snapshot(roots)
        if seal:
            manifest.write_text(json.dumps(actual, sort_keys=True) + '\n')
        else:
            expected = json.loads(manifest.read_text())
            changed = sorted(k for k in actual.keys() | expected.keys() if actual.get(k) != expected.get(k))
            require(not changed, 'integrity mismatch: ' + ', '.join(changed[:8]))
    if seal:
        files = {}
        for roots in [ROOTS, [str(APP)]]:
            files.update(snapshot(roots))
        checksums = {name: entry[2] for name, entry in files.items()
                     if len(entry) == 3 and entry[1] == 'file' and name not in GENERATED}
        for manifest in [BASE / 'manifest.json', APP / 'manifest.json']:
            checksums[str(manifest)] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        (BASE / 'bootstrap.sha256').write_text(''.join(
            f'{digest}  {name}\n' for name, digest in sorted(checksums.items())))


def settings():
    env = os.environ
    if 'ENV' in env:
        require(env['ENV'] in {'Production', 'Test', 'Development'},
                'ENV must be Production, Test or Development')
    # PHP startup overrides can load application code even for php-fpm -t.
    for key in env:
        if key == 'PHP_INI_DIR':
            require(env[key] == '/usr/local/etc/php', 'PHP_INI_DIR must remain /usr/local/etc/php')
            continue
        require(not key.startswith(('PHP_INI', 'PHP_VALUE', 'PHP_ADMIN_VALUE', 'LD_', 'BASH_FUNC_'))
                and key not in {'PHPRC', 'BASH_ENV', 'PYTHONPATH', 'PYTHONHOME'},
                f'forbidden environment override: {key}')
    for key in ['RUN_SCRIPTS', 'SCRIPTS_DIR', 'PUID', 'PGID', 'SSH_KEY', 'DOMAIN',
                'REMOVE_FILES', 'GIT_REPO', 'GIT_BRANCH', 'GIT_TAG', 'GIT_COMMIT',
                'GIT_EMAIL', 'GIT_NAME', 'GIT_USERNAME', 'GIT_PERSONAL_TOKEN', 'GIT_USE_SSH']:
        require(not env.get(key), f'unsupported unsafe setting: {key}')
    require(env.get('SKIP_COMPOSER', '1') == '1', 'Composer startup is unsupported')
    for key in ['SKIP_CHOWN', 'SKIP_CHMOD']:
        require(env.get(key, '1') == '1', f'{key} must be 1; source is never modified')
    for key in ['ERRORS', 'HIDE_NGINX_HEADERS', 'REAL_IP_HEADER', 'ENABLE_XDEBUG',
                'OPcache', 'PHP_CATCHALL', 'PHP_ERRORS_STDERR']:
        require(env.get(key, '') in ('', '0', '1'), f'{key} must be 0 or 1')
    for key in ['PHP_MEM_LIMIT', 'PHP_POST_MAX_SIZE', 'PHP_UPLOAD_MAX_FILESIZE']:
        value = env.get(key, '')
        require(not value or (re.fullmatch(r'[0-9]{1,7}', value) and 0 < int(value) <= 1048576),
                f'{key} must be a positive integer in MiB (maximum 1048576)')
    webroot = env.get('WEBROOT') or '/var/www/html'
    require(re.fullmatch(r'/var/www/html(?:/[a-zA-Z0-9_-][a-zA-Z0-9_.-]*)*', webroot), 'unsafe WEBROOT')
    require(Path(webroot).is_dir() and (Path(webroot).resolve() == Path('/var/www/html') or
            Path('/var/www/html') in Path(webroot).resolve().parents), 'WEBROOT missing or escapes source')
    for file in ['nginx.conf', 'nginx-site.conf', 'nginx-site-ssl.conf']:
        require(not os.path.lexists('/var/www/html/conf/nginx/' + file), 'unreviewed source nginx config')
    tz = env.get('TZ') or 'UTC'
    require(re.fullmatch(r'[A-Za-z0-9_+/-]+', tz) and '..' not in tz and
            Path('/usr/share/zoneinfo', tz).is_file() and not tz.startswith('/'), 'invalid TZ')
    trusted = env.get('REAL_IP_FROM') or '172.16.0.0/12'
    ipaddress.ip_network(trusted, strict=False)
    return webroot, tz, trusted


def initialize():
    require(os.getuid() == 0, 'initialization requires root')
    webroot, tz, trusted = settings()
    integrity()
    env = os.environ
    site = (BASE / 'site.conf.template').read_text().replace('@WEBROOT@', webroot)
    if env.get('REAL_IP_HEADER') == '1':
        site = site.replace('#real_ip_header', 'real_ip_header').replace(
            '#set_real_ip_from 172.16.0.0/12;', f'set_real_ip_from {trusted};')
    Path('/etc/nginx/sites-available/default.conf').write_text(site)
    nginx = (BASE / 'nginx.conf.template').read_text()
    if env.get('HIDE_NGINX_HEADERS') == '0':
        nginx = nginx.replace('server_tokens off;', 'server_tokens on;')
    Path('/etc/nginx/nginx.conf').write_text(nginx)
    ini = ['expose_php = Off', f'date.timezone = {tz}',
           'display_errors = ' + ('On' if env.get('ERRORS') == '1' else 'Off'),
           'log_errors = On', 'error_log = /proc/self/fd/2',
           'opcache.enable = ' + ('0' if env.get('OPcache') == '1' else '1')]
    for key, directive in [('PHP_MEM_LIMIT', 'memory_limit'), ('PHP_POST_MAX_SIZE', 'post_max_size'),
                           ('PHP_UPLOAD_MAX_FILESIZE', 'upload_max_filesize')]:
        if env.get(key):
            ini.append(f'{directive} = {int(env[key])}M')
    Path('/usr/local/etc/php/conf.d/zz-runtime.ini').write_text('\n'.join(ini) + '\n')
    Path('/usr/local/etc/php/conf.d/xdebug.ini').write_text(
        'zend_extension=xdebug\nxdebug.start_with_request=yes\nxdebug.client_host=host.docker.internal\nxdebug.mode=debug\n'
        if env.get('ENABLE_XDEBUG') == '1' else '; disabled\n')
    # Only fixed runtime directories; never recurse into application source.
    for name in ['/run/nginx', '/var/lib/nginx', '/var/log/nginx']:
        path = Path(name)
        protected(path.parent)
        require(not path.is_symlink(), f'runtime directory is a link: {path}')
        path.mkdir(exist_ok=True)
        require(path.is_dir(), f'not a runtime directory: {path}')
        os.chown(path, 100, 101)
        os.chmod(path, 0o750)
    if (APP / 'root.sh').is_file():
        subprocess.run(['/bin/sh', '-eu', str(APP / 'root.sh')], check=True, cwd='/')
    # Docker log pipes were opened by root. Allow nginx to reopen only these
    # exact descriptors for /dev/stdout and /proc/self/fd/2 logging.
    for fd in (1, 2):
        os.fchown(fd, 100, 101)


try:
    action = sys.argv[1:]
    if action == ['--seal']:
        require(os.getuid() == 0, 'sealing requires root at image build time')
        integrity(seal=True)
    elif action == ['--verify']:
        settings()
        integrity()
    elif action == ['--init']:
        initialize()
    else:
        raise ValueError('expected --seal, --verify or --init')
except (ValueError, OSError, subprocess.CalledProcessError) as error:
    sys.exit(f'entry-security: {error}')
