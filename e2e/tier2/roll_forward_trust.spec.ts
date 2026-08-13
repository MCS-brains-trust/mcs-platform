/**
 * Roll-forward for a trust.
 *
 * Modelled on E & J Chiaravalle Family Trust. Two things differ from the company, and
 * both are the point of this file:
 *
 *   * The beneficiary capital accounts are sub-coded -- 4000.01 and 4000.02, one per
 *     beneficiary. Nothing in the suite exercised a sub-coded account before.
 *   * 4199 Undistributed income sits in the pl_appropriation section, where the
 *     partnership's and sole trader's equivalents sit in capital_accounts. The year's
 *     result can only reach it if pl_appropriation is treated as a balance-sheet
 *     section.
 *
 * Note this fixture's seeded chart is much larger than the eleven accounts
 * core/e2e_fixture_data.py lists: core/signals.py's handle_trust_entity_created seeds
 * the 466-row master trust template on creation, so the entity carries 469 accounts.
 * That is left alone deliberately -- a trust created through the real UI gets them too
 * -- and the profile's own chart is applied afterwards, so 4199 keeps the name and
 * section above. The prior-year trial balance the flow actually reads is still the
 * eight deterministic lines the profile defines.
 */
import { describeRollForward } from './roll_forward_flow';

describeRollForward({
  profile: 'trust',
  port: 8203,
  manifest: 'fixture_entity_trust.json',
  instanceSlug: 'rollfwd_trust',
  retainedProfitsCode: '4199',
  balanceSheetCodes: ['2000', '2860', '2869', '3048', '4000.01', '4000.02', '4199'],
  plCodes: ['0105', '1510'],
  expectedAccountsToAdd: 8,
  // Eight prior rows carry forward, plus a new 4199 line to hold the year's result --
  // the prior year has no 4199 of its own.
  expectedRolledRows: 9,
  // No prior 4199 balance, so the opening is the year's whole result: Sales 30,000
  // less Accountancy 10,000 = 20,000 profit, carried as a credit.
  expectedRetainedOpening: '-20000.00',
  checkpointPrefix: 'trust:',
  // The same double entry the company fixture uses: an unrecorded $1,000 Accountancy
  // invoice. Accountancy 10,000 -> 11,000, Trade creditors 6,000 -> 7,000; both sides
  // total 101,000.00.
  amendedPriorTb: [
    ['2000', 'Cash at bank', 70000.0, 0.0],
    ['2860', 'Plant & equipment (cost)', 20000.0, 0.0],
    ['2869', 'Less: Accumulated depreciation', 0.0, 4000.0],
    ['3048', 'Trade creditors', 0.0, 7000.0],
    ['4000.01', 'Opening balance - Beneficiary — Beneficiary One', 0.0, 30000.0],
    ['4000.02', 'Opening balance - Beneficiary — Beneficiary Two', 0.0, 30000.0],
    ['0105', 'Sales', 0.0, 30000.0],
    ['1510', 'Accountancy', 11000.0, 0.0],
  ],
  amendedTbFileName: 'tb_prior_amended_trust.xlsx',
  // 3048 Trade creditors is the direct half; 4199 moves -20,000 -> -19,000 because the
  // expense reduces the year's profit. 1510 correctly never appears -- a P&L account
  // resets each year.
  expectedChangedCodes: ['3048', '4199'],
});
