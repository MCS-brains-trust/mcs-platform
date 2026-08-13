/**
 * Roll-forward for a sole trader.
 *
 * Modelled on Daniel Habteslassie. No sub-coded capital accounts -- the real chart has
 * none -- so this is the cleanest test of "does the year's result reach the
 * type-correct retained-profits account", with the sub-account variable removed. It is
 * also the only profile whose plant pairing is 2850/2859 rather than 2860/2869, which
 * keeps the accumulated-depreciation carry-forward honest against a second code shape.
 */
import { describeRollForward } from './roll_forward_flow';

describeRollForward({
  profile: 'sole_trader',
  port: 8205,
  manifest: 'fixture_entity_sole_trader.json',
  instanceSlug: 'rollfwd_sole_trader',
  retainedProfitsCode: '4199',
  balanceSheetCodes: ['2000', '2850', '2859', '3048', '4010', '4199'],
  plCodes: ['0105', '1510'],
  expectedAccountsToAdd: 7,
  // Seven prior rows carry forward, plus a new 4199 line for the year's result.
  expectedRolledRows: 8,
  expectedRetainedOpening: '-20000.00',
  checkpointPrefix: 'sole_trader:',
  amendedPriorTb: [
    ['2000', 'Cash at bank', 70000.0, 0.0],
    ['2850', 'Plant & equipment - At cost', 20000.0, 0.0],
    ['2859', 'Less: Accumulated depreciation', 0.0, 4000.0],
    ['3048', 'Trade creditors', 0.0, 7000.0],
    ['4010', 'Capital contribution', 0.0, 60000.0],
    ['0105', 'Sales', 0.0, 30000.0],
    ['1510', 'Accountancy', 11000.0, 0.0],
  ],
  amendedTbFileName: 'tb_prior_amended_sole_trader.xlsx',
  expectedChangedCodes: ['3048', '4199'],
});
