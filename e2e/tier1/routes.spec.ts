import * as fs from 'fs';
import * as path from 'path';
import { test, expect, loadRouteManifest, pageForRole, type RouteRecord } from '../fixtures/roles';

/**
 * Tier 1: assert every GET route in the committed manifest behaves as it did before.
 *
 * The contract here is regression, not absolute correctness, and the distinction
 * matters. The manifest resolves path parameters by picking a row of the right model,
 * which cannot know a view's preconditions: an RDTI route handed a financial year with
 * no RDTI data returns 404, and the view is right to. Asserting "everything must
 * return 200" would report those as failures forever, and a suite with permanent
 * known failures trains everyone to ignore it.
 *
 * So each route's status is baselined in status.baseline.json and any *change* is a
 * failure. A route that returned 404 and still does is fine; one that returned 200
 * and now returns 404 is a regression, which is exactly the signal wanted.
 *
 * Three things fail regardless of the baseline, because they are never acceptable:
 * a 5xx, a response carrying a Django error page, and a rendered page that throws
 * uncaught JavaScript or fails to load its own assets.
 *
 * Status is checked with an API request rather than a navigation. Roughly a fifth of
 * these routes return a file (.docx, .pdf, .xlsx) or JSON; navigating to those either
 * triggers a download or renders text, so page.goto() cannot assert on them uniformly.
 * The browser is then used only for routes that actually return HTML, which is where
 * console and sub-resource checks mean anything.
 */

const manifest = loadRouteManifest();
const crawlable: RouteRecord[] = manifest.routes.filter((r) => r.status === 'crawlable');

const BASELINE_PATH = path.join(__dirname, 'status.baseline.json');
const OBSERVED_PATH = path.join(__dirname, '..', 'test-results', 'observed-status.json');

/** Whether to record observed statuses instead of asserting against the baseline. */
const UPDATE_BASELINE = process.env.E2E_UPDATE_BASELINE === '1';

const baselineFile = fs.existsSync(BASELINE_PATH)
  ? JSON.parse(fs.readFileSync(BASELINE_PATH, 'utf-8'))
  : {};
const baseline: Record<string, number> = baselineFile.statuses ?? {};

/**
 * Routes currently returning 5xx, listed so the completeness check knows they are
 * accounted for rather than merely unbaselined. They still fail the sweep — a server
 * error is never baselined — but they fail once, as themselves, instead of also
 * showing up as a missing-baseline failure.
 */
const knownFailures: string[] = baselineFile.known_failures ?? [];

/**
 * Stable key for a route.
 *
 * Name alone is not unique: `entity_detail` is both core's /entities/<pk>/ and the
 * token-authenticated /api/coworker/entity/<pk>/, which return 200 and 401. Keying on
 * the name alone made the second inherit the first's expectation. The URL is not
 * usable either, since it embeds primary keys that change with every refresh — so the
 * path *pattern* plus the name is what identifies a route across runs.
 */
function routeKey(route: RouteRecord): string {
  return `${route.name} ${route.path}`;
}

// Text that appears only on a Django error page. Checked even on a 200, because a view
// that catches its own exception and renders an error template would otherwise pass.
const ERROR_MARKERS = [
  'Traceback (most recent call last)',
  'TemplateDoesNotExist',
  'NoReverseMatch',
  'FieldError at',
  'ProgrammingError at',
  'OperationalError at',
  'DoesNotExist at',
  'Django tried these URL patterns',
  'Server Error (500)',
];

// Sub-resource failures that are not defects. Matched on URL, because Chromium's
// console text for a failed load carries no URL and cannot be filtered usefully.
const IGNORABLE_RESOURCES = [/\/favicon\.ico(\?|$)/i, /\/apple-touch-icon/i];

/** Console errors already covered by response tracking, which reports the URL. */
const REDUNDANT_CONSOLE = [/^Failed to load resource:/i];

const observed: Record<string, number> = {};

test.afterAll(async () => {
  // Written every run, so promoting a new baseline never requires a special mode:
  //   npm run bless   (see scripts/bless_baseline.sh)
  if (Object.keys(observed).length === 0) return;
  fs.mkdirSync(path.dirname(OBSERVED_PATH), { recursive: true });
  const existing = fs.existsSync(OBSERVED_PATH)
    ? JSON.parse(fs.readFileSync(OBSERVED_PATH, 'utf-8'))
    : {};
  fs.writeFileSync(OBSERVED_PATH, JSON.stringify({ ...existing, ...observed }, null, 2));
});

test.describe('Tier 1 — route regression sweep', () => {
  test('manifest is present and non-trivial', () => {
    // A crawl of 0 routes and a crawl of 213 both report zero failures. This makes the
    // difference visible, so an emptied or stale manifest cannot look like success.
    expect(
      crawlable.length,
      'route manifest has suspiciously few crawlable routes — regenerate it with ' +
        'manage.py e2e_dump_routes --settings=config.settings_e2e',
    ).toBeGreaterThan(150);
  });

  test('every crawlable route has a baseline', () => {
    if (UPDATE_BASELINE) test.skip(true, 'recording a new baseline');
    const missing = crawlable
      .filter((r) => baseline[routeKey(r)] === undefined && !knownFailures.includes(routeKey(r)))
      .map((r) => routeKey(r));
    // A new route with no baseline is not silently skipped — an unbaselined route is
    // an uncovered route, and this is what makes that visible in CI.
    expect(
      missing,
      `${missing.length} route(s) have no baseline status. Review them, then run ` +
        `npm run bless to record:\n  ${missing.join('\n  ')}`,
    ).toEqual([]);
  });

  for (const route of crawlable) {
    test(`${route.name} [${route.path}]`, async ({ browser, baseURL }, testInfo) => {
      const page = await pageForRole(browser, 'admin', baseURL);
      const context = page.context();

      try {
        // --- status and body, via API request -----------------------------------
        // Uses the authenticated context's cookies, and does not care whether the
        // response is HTML, JSON or a file download.
        const response = await context.request.get(route.url!, { maxRedirects: 5 });
        const status = response.status();
        observed[routeKey(route)] = status;

        await testInfo.attach('request', {
          body: `${route.url}\n→ ${status} ${response.headers()['content-type'] ?? ''}`,
          contentType: 'text/plain',
        });

        // Never acceptable, baseline or not.
        expect(
          status,
          `${route.name} (${route.url}) returned ${status} — a server error is never an ` +
            `acceptable baseline. See /opt/statementhub/.e2e/logs/django.log`,
        ).toBeLessThan(500);

        const contentType = response.headers()['content-type'] ?? '';
        const isHtml = contentType.includes('text/html');

        if (isHtml) {
          const body = await response.text();
          for (const marker of ERROR_MARKERS) {
            expect(
              body,
              `${route.name} returned ${status} but the body contains "${marker}", so the ` +
                `view failed and rendered an error page.`,
            ).not.toContain(marker);
          }
        }

        // --- regression check ---------------------------------------------------
        if (!UPDATE_BASELINE) {
          const expected = baseline[routeKey(route)];
          if (expected !== undefined) {
            expect(
              status,
              `${route.name} changed status: was ${expected}, now ${status}. ` +
                `If this change is intended, re-bless the baseline.`,
            ).toBe(expected);
          }
        }

        // --- rendered-page checks ----------------------------------------------
        // Only meaningful for HTML that actually renders, so downloads, JSON and
        // error statuses are not navigated at all.
        if (!isHtml || status >= 400) return;

        const consoleErrors: string[] = [];
        const pageErrors: string[] = [];
        const failedResources: string[] = [];

        page.on('console', (msg) => {
          if (msg.type() !== 'error') return;
          const text = msg.text();
          if (REDUNDANT_CONSOLE.some((re) => re.test(text))) return;
          consoleErrors.push(text);
        });
        page.on('pageerror', (err) => pageErrors.push(err.message));
        page.on('response', (r) => {
          const url = r.url();
          if (r.status() < 400) return;
          if (IGNORABLE_RESOURCES.some((re) => re.test(url))) return;
          failedResources.push(`${r.status()} ${url}`);
        });

        await page.goto(route.url!, { waitUntil: 'domcontentloaded' });

        expect(
          pageErrors,
          `${route.name} raised uncaught JavaScript:\n  ${pageErrors.join('\n  ')}`,
        ).toEqual([]);

        expect(
          failedResources,
          `${route.name} failed to load its own assets:\n  ${failedResources.join('\n  ')}`,
        ).toEqual([]);

        expect(
          consoleErrors,
          `${route.name} logged console errors:\n  ${consoleErrors.join('\n  ')}`,
        ).toEqual([]);
      } finally {
        await context.close();
      }
    });
  }
});
