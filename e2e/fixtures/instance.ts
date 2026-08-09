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

const REPO_DIR = '/opt/statementhub';
const START_SERVER = `${REPO_DIR}/e2e/scripts/start_server.sh`;
const BOOT_TIMEOUT_MS = 180_000;

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
      const response = await fetch(`${baseURL}/accounts/login/`);
      if (response.status === 200) return;
      lastError = `status ${response.status}`;
    } catch (err) {
      lastError = String(err);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  throw new Error(
    `server at ${baseURL} did not answer within ${BOOT_TIMEOUT_MS}ms. Last error: ${lastError}`,
  );
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

  await waitForServer(baseURL, proc);

  return {
    baseURL,
    dbName,
    port,
    async stop() {
      if (proc.pid && proc.exitCode === null) {
        try {
          process.kill(-proc.pid, 'SIGTERM');
        } catch {
          // Already gone; dropping the branch below is what actually matters.
        }
      }
      // Wait briefly for connections to close, then drop the branch. start_server.sh
      // also drops WITH (FORCE) on the way in, so a leaked branch cannot wedge the
      // next run — this is tidiness, not correctness.
      await new Promise((resolve) => setTimeout(resolve, 1000));
      await execFileAsync('bash', [
        '-c',
        `set -a; source ${REPO_DIR}/.e2e/db.env; set +a; ` +
          `PGPASSWORD="$E2E_DB_PASSWORD" psql -h "$E2E_DB_HOST" -p "$E2E_DB_PORT" ` +
          `-U "$E2E_DB_USER" -d postgres -qc 'DROP DATABASE IF EXISTS ${dbName} WITH (FORCE);'`,
      ]).catch((err) => {
        console.warn(`[${slug}] could not drop ${dbName}: ${err.message}`);
      });
    },
  };
}
