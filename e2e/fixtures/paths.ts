/**
 * Where the rig looks for code, and where it looks for runtime state.
 *
 * These are two different roots, and conflating them is what has kept this suite off
 * a self-hosted runner (see .github/workflows/e2e.yml's header):
 *
 *   REPO_DIR     the checkout under test -- specs, baselines, scripts, manage.py.
 *                In CI this is $GITHUB_WORKSPACE, the ref actions/checkout just
 *                placed there. Pointing a runner's work directory at the live
 *                deployment to make these agree would be worse than the mismatch:
 *                actions/checkout hard-resets its target every run, which would blow
 *                away the tree gunicorn and celery serve from.
 *
 *   RUNTIME_DIR  the machine's own state -- the virtualenv, and .e2e (database
 *                credentials, the production dump, role manifests, tb workbooks,
 *                logs). None of it belongs to a checkout and none of it should move
 *                with one, so it stays at the deployment root even in CI.
 *
 * Both default to /opt/statementhub, which is what every local invocation wants and
 * what this rig has always assumed, so nothing changes for a developer running the
 * suite by hand.
 */
export const REPO_DIR = process.env.STATEMENTHUB_ROOT || '/opt/statementhub';
export const RUNTIME_DIR =
  process.env.STATEMENTHUB_RUNTIME_ROOT || '/opt/statementhub';

/** Shared rig state: db.env, users.json, fixture_entity.json, tb/, dumps/, logs/. */
export const E2E_STATE_DIR = `${RUNTIME_DIR}/.e2e`;

/** The interpreter the Django side runs under. Belongs to the machine, not the ref. */
export const VENV_PYTHON = `${RUNTIME_DIR}/venv/bin/python`;
