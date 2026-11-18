# Root-init implementation report

## Downstream interface (v1, ready)

Full contract: [root-init.md](root-init.md).

* Keep `USER root` and inherited `ENTRYPOINT ["/start.sh"]` (empty CMD).
* Optional POSIX hooks: `/opt/app-init/root.sh` executed by `/bin/sh -eu` as
  root after preflight; `/opt/app-init/runtime.sh` sourced as nginx UID 100,
  GID 101 for exports. No hooks is supported. Runtime never reenters root init.
* `ENV`, when present, must be exactly `Production`, `Test` or `Development`.
  It is preserved through root hook, privilege drop, runtime hook and FPM.
  All other values, including paths/empty strings, are rejected. Noninteractive
  ash does not source ENV. No root bash is used.
* Hook tree: root-owned, no group/world writes, no symlinks. Directories 0755;
  hooks 0644 or 0755. Keep hook/helper code under `/opt/app-init` or sealed
  system paths. No executable dependencies under mounted application source.
* **Exact build-time sealing API**, after all installations/configuration:

  ```sh
  /usr/bin/python3 -I -S /opt/entry-security/security.py --seal
  ```

  This writes `/opt/app-init/manifest.json`,
  `/opt/entry-security/manifest.json`, and
  `/opt/entry-security/bootstrap.sha256`. No custom manifest format or additional
  arguments required. Never seal at startup.
* Generated-content exclusions are **only**:
  `/etc/nginx/nginx.conf`, `/etc/nginx/sites-available/default.conf`,
  `/usr/local/etc/php/conf.d/zz-runtime.ini`,
  `/usr/local/etc/php/conf.d/xdebug.ini`. Their path types, owner and permissions
  are still checked. Root-init overwrites these from sealed templates/settings.
* **Supervisor configurations are sealed, not generated exclusions.** Install
  `/etc/supervisor/conf.d/*.conf` at build time, then seal. Use Supervisor's
  `%(ENV_NAME)s` interpolation with runtime-hook exports. Do not add/change
  `/etc/supervisor/conf.d` files or other sealed configs from the root hook:
  runtime preflight correctly rejects that as a manifest mismatch. If reviewed
  build customizations are needed for nginx, change its sealed template at
  `/opt/entry-security/nginx.conf.template` before sealing, not just the output.
* Root `nginx -t` can leave root-owned `/run/nginx/nginx.pid`; remove that fixed
  file after build-time validation (the base Dockerfiles do so). Do not leave
  root-owned PID files for the nginx runtime. Runtime validation is unprivileged.
* Root hooks needing PHP must use `php -n` and only explicitly selected sealed
  extension modules, never normal application INI/autoloading. Base root init
  itself executes no PHP. Root-hook environment exports are not propagated;
  put service exports in the runtime hook.
* Under the four-capability target, root has no DAC_OVERRIDE: prepare root-owned
  data directories and explicitly hand ownership to nginx. Do not write inside
  nginx-owned 0750 directories as root or recursively chown source code.

## Final tested evidence

Built local **amd64** image `cs17899219/nginx-php-fpm:root-init-test`:

`sha256:9f6dd27a353cb52da43d1d3b1c907a98074d870a1621b63b99cf15ee9ff70579`

```sh
docker build --pull=false -f tests/Dockerfile.overlay \
  --build-arg BASE_IMAGE=cs17899219/nginx-php-fpm:3.1.6-up2 \
  -t cs17899219/nginx-php-fpm:root-init-test .
python3 tests/root-init.py
python3 tests/single-entry.py cs17899219/nginx-php-fpm:root-init-test
```

Both suites passed on the rebuilt ENV-compatible tag. Every test container used
`--network=none --cap-drop=ALL --cap-add=CHOWN --cap-add=FOWNER
--cap-add=SETUID --cap-add=SETGID --security-opt=no-new-privileges:true`.
No test HTTP ports were published; requests ran inside the isolated containers.

Verified:

* Default no-hook HTTP8080 startup/restart; UID100 nginx, FPM and Supervisor;
  effective/permitted/inheritable capabilities zero; NoNewPrivs=1.
* FPM socket `/run/nginx/php-fpm.sock`, mode0660; nginx cannot write protected
  configs or source. Root-owned, non-group/world-writable configuration remains
  in place after initialization. FPM error messages appear in container stderr.
* Root hook UID0, PHP `-n` isolation, runtime hook exports, failed root/runtime
  hook aborts, direct root `--runtime` rejection, runtime never reruns root hook.
* All three ENV values survive root hook, runtime and FPM; arbitrary/empty ENV
  values fail. PHP override injection, invalid sizes, paths, timezone, network,
  booleans, and legacy unsafe settings fail before services.
* Sealed static app Supervisor config with `%(ENV_ENV)s` and runtime-hook export
  interpolation works. Root-generated Supervisor config under the sealed include
  directory fails with `integrity mismatch` (intentional, documented boundary).
* Hook/config/interpreter/dependency tampering, writable hooks/parents,
  non-root owners, extra files, app-hook symlinks, unreviewed source configs all
  fail before executing a root hook or services.
* PHP8.2.35/modules and safe defaults; validated memory/post/upload sizes, TZ,
  ERRORS, OPcache disable, Xdebug opt-in, nginx headers and real-IP settings.
* Single-index routing, HTTP body/auth/cookie/query forwarding, PHP includes,
  static content, PHP-source/hiding denial, read-only source mount, restart.
* Python ty/ruff LSP diagnostics clean; `git diff --check` clean. A focused
  independent code review found no remaining bug within the documented boundary.

## Limits / remaining risk

This is a **local overlay-layer build/test**, not a full PHP-from-source release
build, multi-architecture qualification, deployment, or business-app acceptance.
The overlay inherits EXPOSE80/9000 metadata; only8080 listens for HTTP and FPM
uses its Unix socket. The main Dockerfile exposes8080 (PHP parent9000 retained).
Legacy startup/smoke tests with old root/port80 contracts are not claimed as run.

Docker launch environment, BusyBox and its loader remain trusted; in particular
LD_PRELOAD can execute before any shell check, so loader-injection environment
must be prohibited by the launcher. No script can defend against a hostile root
operator replacing both manifests and code. Writable container rootfs is needed.
Known non-code state/CA exclusions are documented in `root-init.md`.

No commits, pushes, deployment, business networks or business tasks performed.
All test containers were removed. Downstream should rebuild its extension from
the final local tag above and run the exact sealing API after static config/hook
installation; do not generate additional sealed configs during root startup.
