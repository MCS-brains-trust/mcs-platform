/**
 * Journal entry grid: keep the row being worked on in the top half of the
 * screen, instead of letting it slide under the bottom edge as rows are added.
 *
 * Loaded by core/adjustment_form.html and core/journal_edit.html, which each
 * carry their own copy of the line grid and its account picker.
 *
 * The arithmetic is kept pure and exported for e2e/fixtures/journal_grid.unit.spec.ts;
 * the DOM half below is a thin wrapper that measures elements and calls
 * window.scrollBy with the answer.
 *
 * Note that this only works because the form carries .journal-scroll-room
 * (45vh of padding underneath). Without space below the last row the browser
 * has nothing left to scroll into, and the page silently keeps the old
 * behaviour on exactly the rows worth fixing.
 */
(function (root) {
    'use strict';

    // The comfort band runs from just under the sticky topbar to 45% of the
    // viewport. Anything inside it is already readable and must not be moved --
    // a page that re-scrolls on every focus is worse than one that never does.
    var BAND_FRACTION = 0.45;
    // Where a row that misses the band gets parked.
    var PARK_FRACTION = 0.33;
    // Breathing room under an open account dropdown.
    var DROPDOWN_MARGIN = 16;

    function computeScrollDelta(m) {
        var bandBottom = m.viewportHeight * BAND_FRACTION;
        if (m.elemTop >= m.topbarHeight && m.elemTop <= bandBottom) {
            return 0;
        }
        return Math.round(m.elemTop - m.viewportHeight * PARK_FRACTION);
    }

    function computeDropdownDelta(m) {
        var overflow = m.inputBottom + m.dropdownHeight + m.margin - m.viewportHeight;
        if (overflow <= 0) {
            return 0;
        }
        // Never scroll so far that the field being typed into leaves the
        // screen. When that cap is itself negative the viewport is too short
        // to help at all, and moving the page would only make things worse.
        var cap = Math.max(0, m.inputBottom - m.topbarHeight);
        return Math.round(Math.min(overflow, cap));
    }

    // ---------------------------------------------------------------- DOM ---

    function topbarHeight() {
        var raw = getComputedStyle(document.documentElement)
            .getPropertyValue('--topbar-height');
        return parseInt(raw, 10) || 0;
    }

    function scrollBehavior() {
        return window.matchMedia &&
            window.matchMedia('(prefers-reduced-motion: reduce)').matches
            ? 'auto' : 'smooth';
    }

    function scrollByDelta(delta) {
        if (!delta) return;
        // scrollTo with an absolute target, not scrollBy. Adding a row focuses
        // its picker, which opens the dropdown, so keepDropdownInView and
        // keepRowInView both fire within the same tick. A second smooth
        // scrollBy lands relative to the FIRST one's destination rather than
        // where the page actually is, so the two deltas compound and the row
        // sails past the band. Both measure honest rects against the live
        // scroll position, so resolving to an absolute target makes the later
        // call supersede the earlier one instead of stacking on it.
        window.scrollTo({
            top: Math.max(0, window.scrollY + delta),
            behavior: scrollBehavior(),
        });
    }

    /** Bring a journal row into the comfort band, if it is not already there. */
    function keepRowInView(el) {
        if (!el) return;
        scrollByDelta(computeScrollDelta({
            elemTop: el.getBoundingClientRect().top,
            viewportHeight: window.innerHeight,
            topbarHeight: topbarHeight(),
        }));
    }

    /**
     * Make room for an open account dropdown. Measures what is actually
     * rendered rather than the 250px max-height, so filtering down to two
     * accounts does not scroll the page for a list that already fit.
     */
    function keepDropdownInView(input, dropdown) {
        if (!input || !dropdown) return;
        scrollByDelta(computeDropdownDelta({
            inputBottom: input.getBoundingClientRect().bottom,
            dropdownHeight: dropdown.getBoundingClientRect().height,
            margin: DROPDOWN_MARGIN,
            viewportHeight: window.innerHeight,
            topbarHeight: topbarHeight(),
        }));
    }

    // Under the Playwright unit runner this file is loaded as a module with no
    // window, purely to reach the two pure functions below.
    if (root) root.journalGrid = {
        keepRowInView: keepRowInView,
        keepDropdownInView: keepDropdownInView,
        computeScrollDelta: computeScrollDelta,
        computeDropdownDelta: computeDropdownDelta,
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { computeScrollDelta, computeDropdownDelta };
    }
})(typeof window !== 'undefined' ? window : null);
