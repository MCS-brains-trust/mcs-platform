# StatementHub Entra cutover

## Before the deploy

- Azure app registration `StatementHub` exists, single-tenant, redirect URI
  `https://statementhub.com.au/accounts/oidc/callback/`, ID tokens enabled.
- `/opt/statementhub/.env` carries `MS_TENANT_ID`, `SH_ENTRA_CLIENT_ID`,
  `SH_ENTRA_CLIENT_SECRET`. The deploy does not create them and gunicorn will
  boot with SSO misconfigured rather than refuse to start.

## Deploy

Merging to `main` fires `.github/workflows/deploy.yml`, which pulls, installs
requirements, runs `migrate`, collects static, and restarts celery, celerybeat,
gunicorn in that order. Nothing here is manual.

## Linking the 7

Both logins are live at this point. Each person signs in once via
"Sign in with Microsoft"; the callback links their existing row by email claim.
Nobody can be locked out during this window, because their password still works.

Check progress at any time:

    cd /opt/statementhub && venv/bin/python manage.py entra_link_status

## Removing password login

Only when `entra_link_status` prints `linked N of N` and exits 0. That removal is
Task 8 of the SSO plan and is a separate PR — reversible until it lands, not after.

## If SSO fails for one person

`/accounts/login/?sso=failed` is where a failed match lands. Causes, in order of
likelihood: their `accounts.User.email` does not match their Entra mail/UPN; their
row is `is_active=False`; their row is already linked to a different `oid`
(logged as `entra.sso.oid_conflict`). Fix the row, not the backend.
