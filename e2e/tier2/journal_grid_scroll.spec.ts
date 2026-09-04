import { test, expect, type Page, type Browser } from '@playwright/test';
import { startInstance, type Instance } from '../fixtures/instance';
import { loadUsers, loginAs } from '../fixtures/login';
import * as fs from 'fs';
import { E2E_STATE_DIR } from '../fixtures/paths';

/**
 * The browser half of the journal grid's scroll-follow behaviour.
 *
 * e2e/fixtures/journal_grid.unit.spec.ts pins the arithmetic. What it cannot see
 * is whether the page can act on it: the last row only reaches the top half if
 * something sits below it to scroll into, which is what .journal-scroll-room's
 * 45vh of padding provides. Delete that padding and every unit test stays green
 * while the feature quietly stops working on exactly the rows it was written
 * for. That is the regression this file exists to catch, so it asserts on real
 * scroll positions in a real viewport.
 *
 * Assertions retry rather than sleep. The scroll is smooth by design and a
 * browser may not begin the animation in the same frame it was asked to, so
 * "has the page stopped moving" is not a signal you can poll for -- an animation
 * that has not started yet looks exactly like one that has finished. Retrying
 * the assertion itself sidesteps that question entirely.
 */

const PORT = 8213;
const VIEWPORT = { width: 1280, height: 800 };
const SETTLE_TIMEOUT = 10_000;
let instance: Instance;

test.beforeAll(async () => {
  test.setTimeout(240_000);
  instance = await startInstance('journalgrid', PORT);
});

test.afterAll(async () => {
  await instance?.stop();
});

async function openJournalForm(browser: Browser): Promise<Page> {
  const context = await browser.newContext({
    baseURL: instance.baseURL,
    viewport: VIEWPORT,
  });
  const page = await context.newPage();
  const users = loadUsers();
  await loginAs(page, users.roles.admin, users.password);

  const { current_fy } = JSON.parse(
    fs.readFileSync(`${E2E_STATE_DIR}/fixture_entity.json`, 'utf-8'),
  );
  const response = await page.goto(`/years/${current_fy}/adjustments/create/`);
  expect(response?.status()).toBe(200);
  return page;
}

/** Click "Add Line" until the grid is taller than the viewport it sits in. */
async function addRowsUntilOverflowing(page: Page, max = 30): Promise<void> {
  for (let i = 0; i < max; i++) {
    await page.locator('#add-line-btn').click();
    const overflows = await page.evaluate(
      () =>
        document.querySelector('#journal-lines')!.getBoundingClientRect()
          .height > window.innerHeight,
    );
    if (overflows) return;
  }
  throw new Error(`grid never overflowed a ${VIEWPORT.height}px viewport`);
}

test('the row being entered stays in the top half as rows are added', async ({
  browser,
}) => {
  test.setTimeout(120_000);
  const page = await openJournalForm(browser);
  await addRowsUntilOverflowing(page);

  // The row holding focus is the one just added, and it must be readable:
  // on screen, and above the middle of the viewport.
  await expect
    .poll(
      () =>
        page.evaluate(() => {
          const row = (document.activeElement as HTMLElement).closest('tr')!;
          return Math.round(row.getBoundingClientRect().top);
        }),
      { timeout: SETTLE_TIMEOUT },
    )
    .toBeLessThan(VIEWPORT.height / 2);

  const rowTop = await page.evaluate(() => {
    const row = (document.activeElement as HTMLElement).closest('tr')!;
    return row.getBoundingClientRect().top;
  });
  expect(rowTop).toBeGreaterThan(0);

  await page.context().close();
});

test('the account list opens fully on screen on the bottom row', async ({
  browser,
}) => {
  test.setTimeout(120_000);
  const page = await openJournalForm(browser);
  await addRowsUntilOverflowing(page);

  // Close the picker the last Add Line left open and go back to the top, so
  // the row we are about to click is genuinely below the fold. The picker's
  // blur handler closes its list on a 150ms timer, so give it that long --
  // a list still marked .show would be picked up by the assertion below.
  await page.evaluate(() => {
    (document.activeElement as HTMLElement).blur();
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(300);

  // Clicking scrolls the input just barely into view, at the bottom edge --
  // which is exactly the position that used to push its 250px list off the
  // screen. This is the case keepDropdownInView exists for: focus arriving by
  // click, where the add-row path's keepRowInView never runs.
  await page.locator('.account-picker-input').last().click();

  await expect
    .poll(
      () =>
        page.evaluate(() => {
          const lists = document.querySelectorAll('.account-dropdown.show');
          if (lists.length === 0) return Number.POSITIVE_INFINITY;
          const list = lists[lists.length - 1] as HTMLElement;
          return Math.round(list.getBoundingClientRect().bottom);
        }),
      { timeout: SETTLE_TIMEOUT },
    )
    .toBeLessThanOrEqual(VIEWPORT.height);

  const { dropdownTop, inputTop } = await page.evaluate(() => {
    const lists = document.querySelectorAll('.account-dropdown.show');
    const list = lists[lists.length - 1] as HTMLElement;
    return {
      dropdownTop: list.getBoundingClientRect().top,
      inputTop: (document.activeElement as HTMLElement).getBoundingClientRect().top,
    };
  });
  // The whole list is visible, and the field being typed into was not pushed
  // off the top of the screen to achieve it.
  expect(dropdownTop).toBeGreaterThanOrEqual(0);
  expect(inputTop).toBeGreaterThan(0);

  await page.context().close();
});
