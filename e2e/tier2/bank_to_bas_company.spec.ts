/**
 * Bank statement to BAS, for a company.
 *
 * One entity type for now. entity_type reaches the GST path only to select a
 * chart of accounts (core/bas_utils.py:341-345); the arithmetic is driven by tax
 * codes on transactions, so a second type would mostly re-prove the same sums.
 * Adding one is more than a new profile plus a file like this one: bank_to_bas_flow.ts's
 * ALLOCATIONS table is keyed to this entity type's own chart of accounts (its codes may
 * not exist for another type at all, and whatever replaces them must independently carry
 * no mapped tax_code -- see the ALLOCATIONS comment in bank_to_bas_flow.ts), and the bank
 * account name and the "2000" TB row it asserts are hard-coded too. See e2e/README.md.
 */
import { describeBankToBas } from './bank_to_bas_flow';

describeBankToBas({
  profile: 'bank_bas',
  port: 8206,
  manifest: 'fixture_entity_bank_bas.json',
  instanceSlug: 'bank_bas_company',
});
