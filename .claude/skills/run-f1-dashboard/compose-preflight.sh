#!/usr/bin/env bash
# Static checks on docker-compose.yml, for machines without Docker.
#
# Cannot replace actually running the stack — nothing here exercises nginx, the
# container network, or the images. It catches the failures that are visible
# without a daemon: missing build contexts, dangling service references, and
# env vars the compose file expects but .env does not supply.
set -uo pipefail
cd "$(dirname "$0")/../../.." || exit 1

PYBIN=.venv/bin/python
[ -x "$PYBIN" ] || PYBIN=python3
"$PYBIN" - <<'PY'
import os, re, sys
try:
    import yaml
except ImportError:
    sys.exit("pyyaml not installed; cannot parse compose")

compose = yaml.safe_load(open("docker-compose.yml"))
services = compose.get("services", {})
problems = []

for name, svc in services.items():
    build = svc.get("build")
    if isinstance(build, dict):
        context = build.get("context", ".")
        dockerfile = os.path.join(context, build.get("dockerfile", "Dockerfile"))
        if not os.path.isdir(context):
            problems.append("%s: build context %s does not exist" % (name, context))
        elif not os.path.isfile(dockerfile):
            problems.append("%s: %s does not exist" % (name, dockerfile))
    for dep in (svc.get("depends_on") or {}):
        if dep not in services:
            problems.append("%s depends on %s, which is not a service" % (name, dep))

# Variables compose interpolates, minus those with a ":-" default.
raw = open("docker-compose.yml").read()
required = {
    m.group(1) for m in re.finditer(r"\$\{([A-Z_][A-Z0-9_]*)\}", raw)
}
supplied = set()
if os.path.exists(".env"):
    for line in open(".env"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            supplied.add(line.split("=", 1)[0].strip())
missing = required - supplied
for name in sorted(missing):
    problems.append("%s is interpolated by compose but absent from .env" % name)

published = [
    (name, svc.get("ports"))
    for name, svc in services.items() if svc.get("ports")
]
internal = [n for n, svc in services.items() if not svc.get("ports")]

print("services            : %s" % ", ".join(services))
print("publishing host ports: %s" % ", ".join(n for n, _ in published))
print("internal only        : %s" % ", ".join(internal))
print()
# The security property worth asserting: the three internal services must not
# be reachable from the host, or core-api stops being the single auth boundary.
leaked = [n for n, _ in published
          if n in {"ingestion-service", "prediction-service", "scoring-service"}]
if leaked:
    problems.append("internal service(s) publish host ports: %s" % ", ".join(leaked))

if problems:
    print("PROBLEMS")
    for p in problems:
        print("  ✗ %s" % p)
    sys.exit(1)
print("✓ compose is internally consistent")
print("  (nginx, the container network and the images remain unverified —")
print("   only `docker compose up --build` can test those)")
PY

# ── Production compose ───────────────────────────────────────────────────────
# Static checks only; Docker is not installed here. Deliberately dependency-free
# (no PyYAML) so this runs on a bare host as well as a dev machine. These assert
# the two things that would be genuinely dangerous to get wrong in public.
if [ -f docker-compose.prod.yml ]; then
  echo ""
  echo "production compose:"
  prod_fail=0

  # Every service that publishes host ports, by name. Only caddy may.
  publishers=$(awk '
    /^  [a-z][a-z0-9_-]*:/ { svc = $1; sub(/:$/, "", svc) }
    /^    ports:/          { if (svc != "") print svc }
  ' docker-compose.prod.yml | sort -u)

  for svc in $publishers; do
    if [ "$svc" != "caddy" ]; then
      echo "  ✗ $svc publishes host ports; only caddy may"
      prod_fail=1
    fi
  done
  [ -n "$publishers" ] || { echo "  ✗ nothing publishes a port — caddy cannot serve"; prod_fail=1; }

  if ! grep -q "MONGO_INITDB_ROOT_USERNAME" docker-compose.prod.yml; then
    echo "  ✗ the database has no credentials"
    prod_fail=1
  fi
  if grep -qE '^\s+- "27017:27017"' docker-compose.prod.yml; then
    echo "  ✗ the database publishes 27017 to the host"
    prod_fail=1
  fi
  if ! grep -q 'JWT_SECRET' docker-compose.prod.yml; then
    echo "  ✗ core-api is missing JWT_SECRET"
    prod_fail=1
  fi

  if [ "$prod_fail" -eq 0 ]; then
    echo "  ✓ only caddy publishes host ports ($publishers)"
    echo "  ✓ the database is authenticated and not reachable from the host"
    echo "  ✓ core-api requires JWT_SECRET"
  else
    exit 1
  fi
fi
