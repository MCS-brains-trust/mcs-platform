import { spawn, type ChildProcess } from 'child_process';
import { execFile } from 'child_process';
import { promisify } from 'util';

const execFileAsync = promisify(execFile);

/**
 * A Django instance on its own port, serving its own database branch.
 *
 * Tier 2 flows finalise years and roll them forward — one-way transitions that no
 * cleanup can undo — so a file that ran against a shared branch would poison every
 * file after it. Branching is CREATE DATABASE ... TEMPLATE, roughly 4s for this
 * 500MB database, which is what makes one branch per file affordable.
 *
 * Ports are passed in and fixed per spec file rather than derived from workerIndex,
 * because two files can run concurrently on different workers and a derived port
 * would collide.
 */

import { REPO_DIR, E2E_STATE_DIR } from './paths';

const START_SERVER = `${REPO_DIR}/e2e/scripts/start_server.sh`;
const BOOT_TIMEOUT_MS = 180_000;
// Bound each readiness probe so a connection that is accepted but never answered
// cannot block a poll iteration far past the intended 500ms cadence.
const PROBE_TIMEOUT_MS = 5_000;
// After SIGTERM, how long to wait for the process group to actually exit before
// escalating to SIGKILL. Short, because this only covers a clean Django shutdown;
// a process still alive after this is presumed wedged.
const KILL_POLL_MS = 150;
const KILL_TIMEOUT_MS = 5_000;

export interface Instance {
  baseURL: string;
  dbName: string;
  port: number;
  stop(): Promise<void>;
}

async function waitForServer(baseURL: string, proc: ChildProcess): Promise<void> {
  const deadline = Date.now() + BOOT_TIMEOUT_MS;
  let lastError = '';

  while (Date.now() < deadline) {
    if (proc.exitCode !== null) {
      throw new Error(
        `start_server.sh exited with code ${proc.exitCode} before the server came up. ` +
          `Last error: ${lastError || '(none)'}`,
      );
    }
    try {
      const controller = new AbortController();
      const abortTimer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
      try {
        const response = await fetch(`${baseURL}/accounts/login/`, { signal: controller.signal });
        if (response.status === 200) return;
        lastError = `status ${response.status}`;
      } finally {
        clearTimeout(abortTimer);
      }
    } catch (err) {
      lastError = String(err);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  throw new Error(
    `server at ${baseURL} did not answer within ${BOOT_TIMEOUT_MS}ms. Last error: ${lastError}`,
  );
}

/**
 * SIGTERM the process group and confirm it actually exited before returning.
 * A DROP DATABASE succeeds regardless of whether the OS process is gone, because
 * Postgres disconnects the backend itself -- so without this check, a wedged
 * runserver can outlive the branch it was serving and leave the port bound for
 * whatever tries to reuse it next.
 */
async function killProcessGroup(proc: ChildProcess, slug: string): Promise<void> {
  if (!proc.pid || proc.exitCode !== null) return;

  try {
    process.kill(-proc.pid, 'SIGTERM');
  } catch {
    // Already gone.
    return;
  }

  const deadline = Date.now() + KILL_TIMEOUT_MS;
  while (proc.exitCode === null && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, KILL_POLL_MS));
  }

  if (proc.exitCode === null) {
    console.warn(`[${slug}] process group did not exit after SIGTERM, sending SIGKILL`);
    try {
      process.kill(-proc.pid, 'SIGKILL');
    } catch {
      // Already gone.
    }
  }
}

/**
 * Drop the database branch. start_server.sh also drops WITH (FORCE) on the way
 * in, so a branch left behind here cannot wedge the next run -- this is tidiness,
 * not correctness, which is why a failure here is logged rather than thrown.
 */
async function dropDatabase(dbName: string, slug: string): Promise<void> {
  await execFileAsync('bash', [
    '-c',
    `set -a; source ${E2E_STATE_DIR}/db.env; set +a; ` +
      `PGPASSWORD="$E2E_DB_PASSWORD" psql -h "$E2E_DB_HOST" -p "$E2E_DB_PORT" ` +
      `-U "$E2E_DB_USER" -d postgres -qc 'DROP DATABASE IF EXISTS ${dbName} WITH (FORCE);'`,
  ]).catch((err) => {
    console.warn(`[${slug}] could not drop ${dbName}: ${err.message}`);
  });
}

export async function startInstance(slug: string, port: number): Promise<Instance> {
  const dbName = `sh_e2e_tier2_${slug}`;
  const baseURL = `http://127.0.0.1:${port}`;

  const proc = spawn('bash', [START_SERVER, dbName, String(port)], {
    cwd: REPO_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
    // Own process group, so stop() can kill the whole tree. runserver --noreload
    // does not fork, but the bash wrapper is still a parent to signal.
    detached: true,
  });

  proc.stdout?.on('data', (chunk) => process.stdout.write(`[${slug}] ${chunk}`));
  proc.stderr?.on('data', (chunk) => process.stderr.write(`[${slug}] ${chunk}`));
  // Without this, a spawn-level failure (bash missing, EMFILE, etc.) fires an
  // unhandled 'error' event and crashes the whole test process with a raw stack
  // instead of surfacing through this file's own diagnostics.
  proc.on('error', (err) => {
    console.error(`[${slug}] failed to spawn start_server.sh: ${err.message}`);
  });

  try {
    await waitForServer(baseURL, proc);
  } catch (err) {
    // The server never came up, so no Instance is returned and no caller will
    // ever call stop(). Without cleaning up on this path, the process stays
    // bound to `port` and the branch stays allocated, blocking every future
    // attempt to use either.
    await killProcessGroup(proc, slug);
    await dropDatabase(dbName, slug);
    throw err;
  }

  return {
    baseURL,
    dbName,
    port,
    async stop() {
      await killProcessGroup(proc, slug);
      await dropDatabase(dbName, slug);
    },
  };
}
