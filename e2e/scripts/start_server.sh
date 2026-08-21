#!/usr/bin/env bash
#
# Provision a database branch and start a Django instance against it.
#
#   start_server.sh <db_name> <port>
#
# Called by Playwright's webServer for Tier 1, and by the per-file instance helper
# for Tier 2. Playwright starts webServer before globalSetup, so the branch and its
# fixture users have to be created here rather than in setup — by the time the
# server is listening, the database it serves must already be usable.
#
# Branching is CREATE DATABASE ... TEMPLATE, a local file copy measured at ~4s for
# this 500MB database, which is what makes a fresh database per test file practical.

set -euo pipefail

DB_NAME="${1:?usage: start_server.sh <db_name> <port>}"
PORT="${2:?usage: start_server.sh <db_name> <port>}"

# Two roots, deliberately separate -- see e2e/fixtures/paths.ts for why.
# REPO_DIR is the checkout under test; RUNTIME_DIR owns the venv and .e2e state.
REPO_DIR="${STATEMENTHUB_ROOT:-/opt/statementhub}"
RUNTIME_DIR="${STATEMENTHUB_RUNTIME_ROOT:-/opt/statementhub}"
E2E_DIR="${RUNTIME_DIR}/.e2e"

# shellcheck disable=SC1091
set -a; source "${E2E_DIR}/db.env"; set +a

export PGPASSWORD="${E2E_DB_PASSWORD}"
PSQL="psql -h ${E2E_DB_HOST} -p ${E2E_DB_PORT} -U ${E2E_DB_USER} -v ON_ERROR_STOP=1"

# Every branch inherits the template's schema, so a template one migration
# behind breaks every test that touches the changed table -- and it silently
# has, twice: four migrations behind on 2026-08-20, and review.0014 on
# 2026-08-21, which 500'd the whole financial-year page and looked like a
# product bug. Nothing kept it current, so it is checked and repaired here,
# where every instance boots, rather than left to be rediscovered.
#
# Under flock because Tier 2 boots an instance per test file: concurrent
# migrations of one database would race, and CREATE DATABASE ... TEMPLATE
# below needs the template free of other connections anyway.
#
# Note --check is run UNPIPED and its status read directly. Piping it into
# anything reports the pipe's exit status instead, which is how the stale
# template went unnoticed the second time.
(
  flock 9
  cd "${REPO_DIR}"
  if env DJANGO_SETTINGS_MODULE=config.settings_e2e \
         E2E_DB_NAME="${E2E_TEMPLATE_DB}" \
         "${RUNTIME_DIR}/venv/bin/python" manage.py migrate --check >/dev/null 2>&1
  then
    :
  else
    echo "[start_server] ${E2E_TEMPLATE_DB} is behind — migrating the template"
    env DJANGO_SETTINGS_MODULE=config.settings_e2e \
        E2E_DB_NAME="${E2E_TEMPLATE_DB}" \
        "${RUNTIME_DIR}/venv/bin/python" manage.py migrate --noinput
  fi
) 9>"${E2E_DIR}/template-migrate.lock"

echo "[start_server] branching ${DB_NAME} from ${E2E_TEMPLATE_DB}"

# A previous run killed mid-test can leave both the branch and open connections to
# it behind; drop it with force so startup is never blocked by the last failure.
${PSQL} -d postgres -qc "DROP DATABASE IF EXISTS ${DB_NAME} WITH (FORCE);"
${PSQL} -d postgres -qc "CREATE DATABASE ${DB_NAME} TEMPLATE ${E2E_TEMPLATE_DB};"

cd "${REPO_DIR}"
# The interpreter belongs to the machine, not to the checkout under test: a CI
# workspace has no venv of its own.
# shellcheck disable=SC1091
source "${RUNTIME_DIR}/venv/bin/activate"

export DJANGO_SETTINGS_MODULE=config.settings_e2e
export E2E_DB_NAME="${DB_NAME}"
export E2E_PORT="${PORT}"
# Give this instance its own cache keyspace. Instances share redis DB 4 and
# sessions are cached_db, so without this a session from another instance's
# database can be served from cache here, where its user row may not exist.
export E2E_CACHE_PREFIX="${DB_NAME}"

echo "[start_server] seeding role fixtures"
python3 manage.py e2e_bootstrap_users >/dev/null

echo "[start_server] seeding tier 2 fixture entity"
python3 manage.py e2e_seed_fixture_entity >/dev/null

echo "[start_server] serving ${DB_NAME} on 127.0.0.1:${PORT}"
# --noreload because the autoreloader forks, which detaches the child from the
# process group Playwright signals on teardown and leaves an orphan holding the port.
exec python3 manage.py runserver "127.0.0.1:${PORT}" --noreload
