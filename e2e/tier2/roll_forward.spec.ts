/**
 * Roll-forward for a company.
 *
 * The flow itself lives in roll_forward_flow.ts, shared with the trust, partnership
 * and sole trader specs beside this one. This file supplies only what is
 * company-specific: its port, its manifest, and the accounts its chart uses.
 *
 * This fixture is the odd one out in the suite, deliberately. Its chart uses
 * MYOB-style hyphenated codes ("1-1000", "3-1000 Retained Earnings"), and not one of
 * the ~5,000 chart-of-accounts rows in the production copy is hyphenated -- every real
 * entity uses HandiLedger numerics. That mismatch is what exposed the retained-profits
 * defect: 4199 does not exist on a hyphenated chart, so the year's result had nowhere
 * to go. Keeping it hyphenated keeps that class of defect covered, and the sibling
 * specs cover the shapes real clients actually have.
 */
import { describeRollForward } from './roll_forward_flow';

describeRollForward({
  profile: 'company',
  port: 8202,
  manifest: 'fixture_entity.json',
  instanceSlug: 'rollfwd',
  retainedProfitsCode: '3-1000',
  balanceSheetCodes: ['1-1000', '1-2000', '1-2100', '2-1000', '3-1000'],
  plCodes: ['4-1000', '6-1000'],
  expectedAccountsToAdd: 7,
  // Seven rows out of seven prior accounts, one for one: the five balance-sheet
  // accounts carry an opening balance, Sales and Administration carry a comparative
  // only, and the $20,000 net profit between them is absorbed into 3-1000 Retained
  // Earnings, taking it from -60,000 to -80,000.
  expectedRolledRows: 7,
  expectedRetainedOpening: '-80000.00',
  // Empty: this profile's checkpoints were blessed before the flow was shared, and
  // renaming them would invalidate the golden baseline.
  checkpointPrefix: '',
  // Verbatim from the pre-extraction spec: an unrecorded $1,000 Administration
  // invoice, so Administration 10,000 -> 11,000 and Trade Creditors 6,000 -> 7,000.
  // Both sides total 101,000.00.
  amendedPriorTb: [
    ['1-1000', 'Cash at Bank', 70000.0, 0.0],
    ['1-2000', 'Plant and Equipment', 20000.0, 0.0],
    ['1-2100', 'Accumulated Depreciation', 0.0, 4000.0],
    ['2-1000', 'Trade Creditors', 0.0, 7000.0],
    ['3-1000', 'Retained Earnings', 0.0, 60000.0],
    ['4-1000', 'Sales', 0.0, 30000.0],
    ['6-1000', 'Administration', 11000.0, 0.0],
  ],
  amendedTbFileName: 'tb_prior_amended.xlsx',
  // 2-1000 Trade Creditors is the direct half; 3-1000 Retained Earnings moves
  // -80,000 -> -79,000 because the expense reduces the year's profit. 6-1000
  // correctly never appears -- a P&L account resets each year.
  expectedChangedCodes: ['2-1000', '3-1000'],
});
