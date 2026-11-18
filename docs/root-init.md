# Controlled root initialization (interface v1)

The image starts `/start.sh` as root, verifies sealed code/configuration and
permissions, writes only allowlisted settings, executes optional
`/opt/app-init/root.sh`, and **execs**
`su-exec nginx:nginx /start.sh --runtime`. Runtime requires UID 100 (nginx GID
101), verifies integrity again, **sources** optional `/opt/app-init/runtime.sh`
(for exports), validates nginx/PHP-FPM, then execs Supervisor as nginx. Runtime
never runs root initialization. Default: neither hook is required.

## Downstream build interface

```dockerfile
FROM cs17899219/nginx-php-fpm:root-init-test
USER root
COPY --chown=0:0 app-init/ /opt/app-init/
RUN chmod 755 /opt/app-init \
 && find /opt/app-init -type d -exec chmod 755 {} \; \
 && find /opt/app-init -type f -exec chmod 644 {} \; \
 && /usr/bin/python3 -I -S /opt/entry-security/security.py --seal
# Keep root startup; do not replace ENTRYPOINT or set USER nginx.
```

Seal **at build time**, after all hook/dependency/config installation. This
regenerates `/opt/app-init/manifest.json`, `/opt/entry-security/manifest.json`,
and `/opt/entry-security/bootstrap.sha256`. Never reseal at container startup.
The app manifest covers the entire app-init tree (except itself); base manifests
cover interpreters, modules, libraries, startup scripts, protected configurations,
symlink targets, metadata and parents. Missing/extra files, writable paths,
non-root owners, unexpected links, and hash changes fail closed. Hooks are
regular root-owned files, mode 0644 or 0755, under root-owned 0755 parents.
**No symlinks anywhere under app-init.** Do not put secrets in hook files: runtime
integrity verification requires them to be readable by nginx. Mount secrets at
separate paths, consumed by the reviewed hook as data only.

Both hooks use POSIX shell. Root hook runs as `/bin/sh -eu root.sh`, not sourced;
its environment changes do not propagate. Runtime hook is sourced with `set -eu`;
explicitly export variables needed by FPM (`clear_env=no`). Hook failure aborts.
Hooks are trusted, reviewed image code, not writable application scripts.
All root-executed helper code must be installed beneath `/opt/app-init` or the
sealed system paths **before sealing**. Never source/execute mounted application
code, Composer, Git, or application PHP configuration as root. If root init needs
PHP, use `/usr/local/bin/php -n` with only explicitly selected sealed modules,
e.g. `-d extension=/usr/local/lib/php/extensions/<ABI>/pdo_mysql.so`; do not load
application INI files or autoloaders. No root PHP is needed by the base image.

## Supported settings / deliberate incompatibilities

* `WEBROOT`: existing, resolved path strictly beneath `/var/www/html` (or the
  directory itself), safe path segments only; no source configuration imports.
* `TZ`: existing zoneinfo name, default UTC.
* `ENV`: absent or exactly `Production`, `Test`, `Development`; preserved for
  root/runtime hooks and FPM. Other values (including empty/path values) fail.
  Noninteractive ash does not source this variable.
* `PHP_MEM_LIMIT`, `PHP_POST_MAX_SIZE`, `PHP_UPLOAD_MAX_FILESIZE`: integer MiB,
  1–1048576, overriding defaults 256M/100M/100M. No INI fragments accepted.
* `ERRORS=1` enables display errors; default off. `HIDE_NGINX_HEADERS=0` enables
  nginx version headers; default hidden. PHP version exposure always disabled.
* `OPcache=1` disables OPcache (historical spelling/semantics); otherwise enabled.
  `ENABLE_XDEBUG=1` loads the installed Xdebug; otherwise disabled.
* `REAL_IP_HEADER=1`, `REAL_IP_FROM=<IPv4/IPv6 network>` (default 172.16.0.0/12).
* `PHP_ERRORS_STDERR` accepts 0/1; logging is **always** stderr.
  `PHP_CATCHALL` accepts 0/1; single-index routing is **always** enabled (as in
  the previous entry-security layer). Other PHP-looking paths remain denied.
* `SKIP_COMPOSER`, `SKIP_CHOWN`, `SKIP_CHMOD` are optional compatibility flags;
  only `1` is accepted. No startup Composer or recursive source chmod/chown.
* Nonempty Git/SSH provisioning settings, `RUN_SCRIPTS`, `SCRIPTS_DIR`, `PUID`,
  `PGID`, `DOMAIN`, `REMOVE_FILES` fail: provision code/identities/TLS at build
  time or with explicit reviewed hooks. `APPLICATION_ENV` remains application
  data; it no longer triggers Composer. Source Composer files are not executed.
* PHP configuration override environment (`PHPRC`, `PHP_INI*`, `PHP_VALUE*`,
  `PHP_ADMIN_VALUE*`) is forbidden even when empty. The inherited `PHP_INI_DIR`
  is allowed only at its fixed `/usr/local/etc/php` value. Interpreter injection
  variables are rejected. Arbitrary environment values are never interpolated
  into shell commands or INI/directive syntax.

## Deployment boundary

```sh
docker run --cap-drop=ALL --cap-add=CHOWN --cap-add=FOWNER \
  --cap-add=SETUID --cap-add=SETGID --security-opt=no-new-privileges:true \
  -p 8080:8080 cs17899219/nginx-php-fpm:root-init-test
```

HTTP is 8080, FPM socket `/run/nginx/php-fpm.sock` mode 0660. FPM/nginx/
Supervisor PID/temp/socket files live under `/run/nginx`. Configurations remain
root-owned and not writable by nginx. Inherited image EXPOSE 9000 metadata does
not mean TCP FPM is listening. No source ownership changes occur.

Image loader/BusyBox, the Docker launch environment, and image build authority
are trusted bootstrap boundaries. In particular **never pass LD_PRELOAD or
LD_LIBRARY_PATH**: a dynamic loader runs before any shell-level rejection.
Do not run privileged, mount over sealed paths, grant runtime Docker access,
or run another process capable of editing protected paths concurrently with
preflight. Integrity is not protection against a hostile Docker/root operator
who can replace both code and manifests. Root hooks must not change sealed files;
prepare only designated data/runtime paths, or subsequent verification fails.
Without DAC_OVERRIDE, root hooks cannot write nginx-owned 0750 directories:
prepare data in root-owned locations (e.g. a dedicated `/run/app-init`), then
explicitly hand ownership to nginx. Do not recursively chown application source.
Supervisor configs under `/etc/supervisor/conf.d` must be installed at build
time and sealed, not generated/changed in root hooks. Use `%(ENV_NAME)s`
interpolation with exports from the runtime hook. The only generated-content
exclusions are `/etc/nginx/nginx.conf`,
`/etc/nginx/sites-available/default.conf`,
`/usr/local/etc/php/conf.d/zz-runtime.ini`, and
`/usr/local/etc/php/conf.d/xdebug.ini`; their metadata remains protected.
If build validation runs nginx as root, remove `/run/nginx/nginx.pid` afterward
before handing the image to the nginx runtime (the base Dockerfiles do so).

Root-owned config writes require a writable container layer; read-only rootfs
and arbitrary UID remapping are not supported by this protocol. Local CA data
(`/usr/local/share/ca-certificates`), package-manager state (`/lib/apk`), and old
local log state (`/usr/local/var`) are not code and are excluded from content
sealing; these trees still must be root-owned and not group/world writable.
Never place root-executed helper code there. Root init hands only its existing
stdout/stderr descriptors to nginx so logging can reopen Docker's root-created
pipes after the UID drop; it does not chmod/chown arbitrary log paths.

## Local tests

`python3 tests/root-init.py [image]` builds isolated hook fixtures and runs
containers with network disabled and the capability/no-new-privileges target.
`python3 tests/single-entry.py <image>` additionally checks single-index routing,
body/auth/cookie forwarding, source denial and read-only source mounts, also with
network disabled. `tests/Dockerfile.overlay` tests the layer on a locally available
parent without rebuilding PHP; it is not a full release-build claim. Older
`startup.py`, `runtime-settings.py`, `smoke.sh` and `shutdown.py` retain historical
root-startup/port80 assumptions and are not the acceptance suite for this profile.
