import { test, expect } from '@playwright/test';

/**
 * Pure-logic coverage for the journal grid's scroll-follow decisions.
 *
 * These two functions are the whole feature. Everything else in
 * static/js/journal_grid.js is a thin DOM wrapper that measures elements and
 * hands the numbers here, then calls window.scrollBy with the answer. Keeping
 * the arithmetic pure is what makes it testable at all: a browser-free
 * assertion on "how far should the page move" survives layout churn that would
 * break a screenshot or an offset assertion.
 *
 * The rule being encoded: the page moves only when it has something to fix.
 * A row already sitting in the comfort band -- below the sticky topbar, above
 * the middle of the viewport -- must produce a delta of exactly 0. A page that
 * re-scrolls on every focus is the annoyance we are replacing, not an
 * improvement on it.
 */
const {
  computeScrollDelta, computeDropdownDelta,
} = require('../../static/js/journal_grid.js');

// A 900px viewport under a 64px sticky topbar. The comfort band therefore runs
// from y=64 to y=405 (45%), and a row outside it is parked at y=297 (33%).
const VIEW = { viewportHeight: 900, topbarHeight: 64 };

test.describe('computeScrollDelta', () => {
  test('leaves a row alone when it already sits in the comfort band', () => {
    expect(computeScrollDelta({ ...VIEW, elemTop: 200 })).toBe(0);
  });

  test('pulls a row up when it sits below the middle of the viewport', () => {
    // 700 is past the 405 band edge, so the row is parked at 33%: 700-297.
    expect(computeScrollDelta({ ...VIEW, elemTop: 700 })).toBe(403);
  });

  test('scrolls back up when a row is hidden behind the sticky topbar', () => {
    // 20 is under the 64px topbar. A negative delta scrolls the page up.
    expect(computeScrollDelta({ ...VIEW, elemTop: 20 })).toBe(-277);
  });

  test('treats a row exactly on the band edge as already comfortable', () => {
    expect(computeScrollDelta({ ...VIEW, elemTop: 405 })).toBe(0);
  });
});

test.describe('computeDropdownDelta', () => {
  const MARGIN = 16;

  test('holds still when the whole list already fits below the input', () => {
    expect(computeDropdownDelta({
      ...VIEW, inputBottom: 400, dropdownHeight: 250, margin: MARGIN,
    })).toBe(0);
  });

  test('scrolls just far enough to bring the last item into view', () => {
    // 800 + 250 + 16 overflows a 900px viewport by 166.
    expect(computeDropdownDelta({
      ...VIEW, inputBottom: 800, dropdownHeight: 250, margin: MARGIN,
    })).toBe(166);
  });

  test('measures the list actually rendered, not its max-height', () => {
    // A two-account filter renders ~60px and fits, so the page must not move.
    // Scrolling as though the full 250px were showing would jump the page for
    // a list that was never off-screen.
    expect(computeDropdownDelta({
      ...VIEW, inputBottom: 800, dropdownHeight: 60, margin: MARGIN,
    })).toBe(0);
  });

  test('never scrolls the input being typed into off the top of the screen', () => {
    // A list taller than the room above and below it. The raw overflow is
    // 380+500+16-400 = 496, which would put the input at y=-116. The delta is
    // capped at 380-64 so the field stays just under the topbar.
    expect(computeDropdownDelta({
      viewportHeight: 400, topbarHeight: 64,
      inputBottom: 380, dropdownHeight: 500, margin: MARGIN,
    })).toBe(316);
  });

  test('does not scroll upward when there is room for the list nowhere', () => {
    // A viewport too short to hold the list on either side. The overflow says
    // scroll down 116, the cap says the input can only absorb -14 -- and the
    // smaller of those is a page that jumps UPWARD, away from the list it was
    // trying to reveal. Nothing sensible is available here, so do nothing.
    expect(computeDropdownDelta({
      viewportHeight: 200, topbarHeight: 64,
      inputBottom: 50, dropdownHeight: 250, margin: MARGIN,
    })).toBe(0);
  });
});
