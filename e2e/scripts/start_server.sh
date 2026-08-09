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

REPO_DIR="/opt/statementhub"
E2E_DIR="${REPO_DIR}/.e2e"

# shellcheck disable=SC1091
set -a; source "${E2E_DIR}/db.env"; set +a

export PGPASSWORD="${E2E_DB_PASSWORD}"
PSQL="psql -h ${E2E_DB_HOST} -p ${E2E_DB_PORT} -U ${E2E_DB_USER} -v ON_ERROR_STOP=1"

echo "[start_server] branching ${DB_NAME} from ${E2E_TEMPLATE_DB}"

# A previous run killed mid-test can leave both the branch and open connections to
# it behind; drop it with force so startup is never blocked by the last failure.
${PSQL} -d postgres -qc "DROP DATABASE IF EXISTS ${DB_NAME} WITH (FORCE);"
${PSQL} -d postgres -qc "CREATE DATABASE ${DB_NAME} TEMPLATE ${E2E_TEMPLATE_DB};"

cd "${REPO_DIR}"
# shellcheck disable=SC1091
source venv/bin/activate

export DJANGO_SETTINGS_MODULE=config.settings_e2e
export E2E_DB_NAME="${DB_NAME}"
export E2E_PORT="${PORT}"
# Give this instance its own cache keyspace. Instances share redis DB 4 and
# sessions are cached_db, so without this a session from another instance's
# database can be served from cache here, where its user row may not exist.
export E2E_CACHE_PREFIX="${DB_NAME}"

echo "[start_server] seeding role fixtures"
python3 manage.py e2e_bootstrap_users >/dev/null

echo "[start_server] serving ${DB_NAME} on 127.0.0.1:${PORT}"
# --noreload because the autoreloader forks, which detaches the child from the
# process group Playwright signals on teardown and leaves an orphan holding the port.
exec python3 manage.py runserver "127.0.0.1:${PORT}" --noreload
