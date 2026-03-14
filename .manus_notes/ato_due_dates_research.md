# ATO due dates research

Authoritative source:
- https://www.ato.gov.au/tax-and-super-professionals/for-tax-professionals/prepare-and-lodge/registered-agent-lodgment-program-2025-26/due-dates-by-month

Confirmed page pattern:
- Month detail page path: /due-dates-by-month/{month-year-lowercase-hyphenated}
- Example: /due-dates-by-month/march-2026

Confirmed March 2026 entries from official ATO page:
- 21 March 2026 — Lodge and pay February 2026 monthly business activity statement.
- 31 March 2026 — Lodge tax return for companies and super funds with total income of more than $2 million in the latest year lodged (excluding large and medium taxpayers), unless the return was due earlier.
- 31 March 2026 — Payment for companies and super funds in this category is also due by this date.
- 31 March 2026 — Lodge tax return for the head company of a consolidated group (excluding large and medium), with a member who had a total income in excess of $2 million in their latest year lodged, unless the return was due earlier.
- 31 March 2026 — Payment for companies in this category is also due by this date.
- 31 March 2026 — Lodge tax return for individuals and trusts whose latest return resulted in a tax liability of $20,000 or more, excluding large and medium trusts.
- 31 March 2026 — Payment for individuals and trusts in this category is due as advised on their notice of assessment.

Implementation direction:
- Build a lightweight service/helper that requests the current ATO lodgment program month page based on today in Australia/Melbourne.
- Parse all month pages from current month forward until at least 3 future due dates are found.
- Normalize each due date into: due_date, label/description, source_url.
- Filter to entries where due_date >= today in Australia/Melbourne.
- Sort ascending and keep next 3 unique upcoming due-date items.
- Cache results for a short period to avoid hitting the ATO on every dashboard request.
