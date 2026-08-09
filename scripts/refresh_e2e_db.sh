#!/usr/bin/env bash
#
# Refresh the E2E template database from a production dump.
#
#   scripts/refresh_e2e_db.sh
#
# Dumps production (read-only), restores into the local PostgreSQL 18 cluster as
# sh_e2e_template, then hardens the copy by severing every inherited outbound
# credential. Individual test files branch from the template with
# CREATE DATABASE ... TEMPLATE, which is a local file copy taking seconds, so this
# script only needs re-running when you want fresher data.
#
# Production is only ever read. The single guard that matters is REFUSE_NON_LOCAL
# below: the restore target must resolve to loopback, so a mistyped or inherited
# DATABASE_URL cannot turn this into a script that rewrites production.

set -euo pipefail

REPO_DIR="${STATEMENTHUB_RUNTIME_ROOT:-/opt/statementhub}"
E2E_DIR="${REPO_DIR}/.e2e"
DUMP_DIR="${E2E_DIR}/dumps"
HARDEN_SQL="${REPO_DIR}/scripts/e2e_harden.sql"
PG_BIN="/usr/lib/postgresql/18/bin"
KEEP_DUMPS=3

log() { printf '\n[refresh_e2e_db] %s\n' "$*"; }
fail() { printf '\n[refresh_e2e_db] ERROR: %s\n' "$*" >&2; exit 1; }

# --- Load configuration -----------------------------------------------------

[[ -f "${REPO_DIR}/.env" ]] || fail "missing ${REPO_DIR}/.env (need production DATABASE_URL to dump from)"
[[ -f "${E2E_DIR}/db.env" ]] || fail "missing ${E2E_DIR}/db.env (run the provisioning step first)"
[[ -f "${HARDEN_SQL}" ]] || fail "missing ${HARDEN_SQL}"

SOURCE_URL="$(grep '^DATABASE_URL=' "${REPO_DIR}/.env" | cut -d= -f2-)"
[[ -n "${SOURCE_URL}" ]] || fail "DATABASE_URL not found in .env"

# shellcheck disable=SC1091
set -a; source "${E2E_DIR}/db.env"; set +a

: "${E2E_DB_HOST:?}" "${E2E_DB_PORT:?}" "${E2E_DB_USER:?}" "${E2E_DB_PASSWORD:?}" "${E2E_TEMPLATE_DB:?}"

# --- REFUSE_NON_LOCAL -------------------------------------------------------
# Everything below this point writes. It must only ever write to loopback.

case "${E2E_DB_HOST}" in
    127.0.0.1|::1|localhost) ;;
    *) fail "restore target '${E2E_DB_HOST}' is not loopback — refusing to write to a remote host" ;;
esac

if [[ "${SOURCE_URL}" == *"${E2E_TEMPLATE_DB}"* ]]; then
    fail "source and target appear to be the same database — refusing"
fi

export PGPASSWORD="${E2E_DB_PASSWORD}"
psql_e2e() { psql -h "${E2E_DB_HOST}" -p "${E2E_DB_PORT}" -U "${E2E_DB_USER}" -v ON_ERROR_STOP=1 "$@"; }

# --- Dump -------------------------------------------------------------------
# pg_dump must be at least the server version; production is PG 18, so the 18
# client is used explicitly rather than whatever /usr/bin/pg_dump resolves to
# (the host also has a 16 client on PATH, which aborts against an 18 server).

mkdir -p "${DUMP_DIR}"
# The dump is a byte-for-byte copy of the production database — every client's
# financial data, sitting at rest on the droplet. Under the default umask pg_dump
# would leave it 0644 inside a 0755 directory, readable by any account on the box.
# The directory is locked down before pg_dump writes into it, and the file itself
# immediately after, so the window where it exists world-readable is closed.
chmod 0700 "${DUMP_DIR}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP_FILE="${DUMP_DIR}/prod-${STAMP}.dump"

log "dumping production → ${DUMP_FILE}"
"${PG_BIN}/pg_dump" \
    --format=custom \
    --no-owner \
    --no-privileges \
    --file="${DUMP_FILE}" \
    "${SOURCE_URL}"
chmod 0600 "${DUMP_FILE}"

log "dump complete ($(du -h "${DUMP_FILE}" | cut -f1))"

# --- Recreate template ------------------------------------------------------
# datallowconn is flipped off and connections terminated first: PostgreSQL
# refuses both DROP DATABASE and CREATE DATABASE ... TEMPLATE while any other
# session holds the database open, and a stale test run is exactly the kind of
# thing that would otherwise hold one.

log "recreating ${E2E_TEMPLATE_DB}"
psql_e2e -d postgres -q <<SQL
UPDATE pg_database SET datistemplate = false, datallowconn = false
 WHERE datname = '${E2E_TEMPLATE_DB}';
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname = '${E2E_TEMPLATE_DB}' AND pid <> pg_backend_pid();
SQL
psql_e2e -d postgres -qc "DROP DATABASE IF EXISTS ${E2E_TEMPLATE_DB};"
psql_e2e -d postgres -qc "CREATE DATABASE ${E2E_TEMPLATE_DB};"
psql_e2e -d "${E2E_TEMPLATE_DB}" -qc "CREATE EXTENSION IF NOT EXISTS vector;"

# --- Restore ----------------------------------------------------------------
# --no-acl/--no-owner because production roles (doadmin et al) do not exist
# locally. Exit status is inspected rather than trusted: pg_restore reports
# non-fatal warnings through the same channel as real failures, so the row-count
# verification below is what actually confirms success.

log "restoring into ${E2E_TEMPLATE_DB}"
set +e
"${PG_BIN}/pg_restore" \
    --dbname="postgresql://${E2E_DB_USER}:${E2E_DB_PASSWORD}@${E2E_DB_HOST}:${E2E_DB_PORT}/${E2E_TEMPLATE_DB}" \
    --no-owner \
    --no-acl \
    --jobs=4 \
    "${DUMP_FILE}" 2> "${DUMP_DIR}/restore-${STAMP}.log"
RESTORE_RC=$?
set -e

if [[ ${RESTORE_RC} -ne 0 ]]; then
    log "pg_restore exited ${RESTORE_RC}; last lines of restore log:"
    tail -20 "${DUMP_DIR}/restore-${STAMP}.log" >&2
fi

# --- Verify -----------------------------------------------------------------
# A restore that produced an empty or half-populated database is worse than one
# that failed loudly, because the suite would run green against nothing.

ENTITY_COUNT="$(psql_e2e -d "${E2E_TEMPLATE_DB}" -tAc "SELECT count(*) FROM core_entity;")"
if [[ "${ENTITY_COUNT}" -lt 1 ]]; then
    fail "restore produced ${ENTITY_COUNT} entities — aborting before hardening"
fi
log "restored ${ENTITY_COUNT} entities"

# --- Harden -----------------------------------------------------------------

log "hardening restored copy"
psql_e2e -d "${E2E_TEMPLATE_DB}" -q -f "${HARDEN_SQL}"

LIVE_TOKENS="$(psql_e2e -d "${E2E_TEMPLATE_DB}" -tAc \
    "SELECT count(*) FROM integrations_accountingconnection
      WHERE coalesce(access_token, '') <> '' OR coalesce(refresh_token, '') <> '';")"
if [[ "${LIVE_TOKENS}" -ne 0 ]]; then
    fail "${LIVE_TOKENS} connection rows still hold credentials after hardening"
fi

MARKED="$(psql_e2e -d "${E2E_TEMPLATE_DB}" -tAc "SELECT count(*) FROM e2e_marker WHERE is_e2e;")"
[[ "${MARKED}" -ge 1 ]] || fail "e2e_marker missing after hardening"

# --- Mirror media -----------------------------------------------------------
# The database references uploaded files by path, so restoring rows without the
# files leaves every page that renders one — the firm logo on the dashboard, a
# governing document, a workpaper template — serving a broken asset. Tier 1 treats a
# 404 sub-resource as a failure, correctly, so the files have to come across too.
#
# A copy, not a symlink: settings_e2e points MEDIA_ROOT here, and the suite
# generates statements and packages, which under a symlink would be written into
# production's media tree.
#
# Direction is production → E2E and must stay that way. --delete makes the E2E tree
# a faithful mirror, which also clears whatever the previous run generated.

log "mirroring media (${REPO_DIR}/media → ${E2E_DIR}/media)"
mkdir -p "${E2E_DIR}/media"
rsync -a --delete "${REPO_DIR}/media/" "${E2E_DIR}/media/"
log "media mirrored ($(du -sh "${E2E_DIR}/media" | cut -f1))"

# --- Seal as template -------------------------------------------------------
# datistemplate lets test files branch from it; datallowconn stays on so the
# bootstrap commands can still write to it between refreshes.

psql_e2e -d postgres -qc "UPDATE pg_database SET datistemplate = true WHERE datname = '${E2E_TEMPLATE_DB}';"

# --- Prune old dumps --------------------------------------------------------

ls -1t "${DUMP_DIR}"/prod-*.dump 2>/dev/null | tail -n +$((KEEP_DUMPS + 1)) | while read -r old; do
    log "pruning ${old}"
    rm -f "${old}"
done

log "done — ${E2E_TEMPLATE_DB} ready (${ENTITY_COUNT} entities, credentials severed)"
