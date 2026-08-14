/**
 * Bank statement to BAS, for a company.
 *
 * One entity type for now. entity_type reaches the GST path only to select a
 * chart of accounts (core/bas_utils.py:341-345); the arithmetic is driven by tax
 * codes on transactions, so a second type would mostly re-prove the same sums.
 * Adding one is a new profile plus a file like this one.
 */
import { describeBankToBas } from './bank_to_bas_flow';

describeBankToBas({
  profile: 'bank_bas',
  port: 8206,
  manifest: 'fixture_entity_bank_bas.json',
  instanceSlug: 'bank_bas_company',
  checkpointPrefix: 'bank_bas:',
});
