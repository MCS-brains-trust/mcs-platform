/**
 * Roll-forward for a partnership.
 *
 * Modelled on D.P Vaughan & D Vriend. Partner capital accounts are sub-coded
 * (.01/.02) as the trust's beneficiary accounts are, but 4199 Unappropriated profits
 * sits in capital_accounts rather than pl_appropriation -- so this file isolates the
 * sub-account question from the section question the trust raises.
 *
 * The share-of-profit (4003.x) and drawings (4054.x) accounts are on the chart but
 * carry no prior balance, so they never appear in the rolled trial balance. They are
 * present because a partnership chart without them is not a partnership chart, and
 * because the next defect this fixture finds is likely to involve them.
 */
import { describeRollForward } from './roll_forward_flow';

describeRollForward({
  profile: 'partnership',
  port: 8204,
  manifest: 'fixture_entity_partnership.json',
  instanceSlug: 'rollfwd_partnership',
  retainedProfitsCode: '4199',
  balanceSheetCodes: ['2000', '2860', '2869', '3048', '4000.01', '4000.02', '4199'],
  plCodes: ['0105', '1510'],
  expectedAccountsToAdd: 8,
  // Eight prior rows carry forward, plus a new 4199 line for the year's result.
  expectedRolledRows: 9,
  expectedRetainedOpening: '-20000.00',
  checkpointPrefix: 'partnership:',
  amendedPriorTb: [
    ['2000', 'Cash at bank', 70000.0, 0.0],
    ['2860', 'Plant & equipment (cost)', 20000.0, 0.0],
    ['2869', 'Less: Accumulated depreciation', 0.0, 4000.0],
    ['3048', 'Trade creditors', 0.0, 7000.0],
    ['4000.01', 'Opening balance - Partner — Partner One', 0.0, 30000.0],
    ['4000.02', 'Opening balance - Partner — Partner Two', 0.0, 30000.0],
    ['0105', 'Sales', 0.0, 30000.0],
    ['1510', 'Accountancy', 11000.0, 0.0],
  ],
  amendedTbFileName: 'tb_prior_amended_partnership.xlsx',
  expectedChangedCodes: ['3048', '4199'],
});
