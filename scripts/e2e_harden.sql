-- Post-restore hardening for the E2E template database.
--
-- Runs against the freshly restored copy of production, NEVER against production
-- itself (refresh_e2e_db.sh refuses any non-local target before this executes).
--
-- Purpose: sever every outbound credential inherited from the production dump so
-- the E2E instance cannot act on production's behalf. The critical case is OAuth
-- refresh tokens: providers rotate them single-use, so if the E2E instance ever
-- refreshed an inherited token, the copy still held in production would be
-- invalidated and the live connection would break. Blanking them here makes that
-- impossible rather than merely unlikely.
--
-- Tokens are EncryptedTextField. decrypt_value() short-circuits on falsy input
-- (config/encryption.py:67), so '' reads back as '' rather than raising.

BEGIN;

-- 1. Per-entity accounting connections (Xero / MYOB / QuickBooks).
UPDATE integrations_accountingconnection
   SET status = 'disconnected',
       access_token = '',
       refresh_token = '',
       token_expires_at = NULL;

-- 2. Firm-wide provider connections. Column sets differ slightly between these
--    tables, so each statement is guarded on the columns it actually touches.
DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'integrations_xeroglobalconnection',
        'integrations_qbglobalconnection',
        'integrations_myobglobalconnection',
        'integrations_xpmconnection'
    ]
    LOOP
        IF to_regclass('public.' || tbl) IS NULL THEN
            CONTINUE;
        END IF;

        IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = tbl AND column_name = 'access_token') THEN
            EXECUTE format('UPDATE %I SET access_token = %L, refresh_token = %L', tbl, '', '');
        END IF;

        IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = tbl AND column_name = 'token_expires_at') THEN
            EXECUTE format('UPDATE %I SET token_expires_at = NULL', tbl);
        END IF;

        IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = tbl AND column_name = 'status') THEN
            EXECUTE format('UPDATE %I SET status = %L', tbl, 'disconnected');
        END IF;
    END LOOP;
END $$;

-- 3. Any other table carrying OAuth tokens (tenant/company-file rows, and any
--    future table following the same naming convention). Caught generically so a
--    newly added provider table cannot silently retain live credentials.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.table_name, c.column_name
          FROM information_schema.columns c
          JOIN information_schema.tables t
            ON t.table_name = c.table_name AND t.table_schema = c.table_schema
         WHERE c.table_schema = 'public'
           AND t.table_type = 'BASE TABLE'
           AND c.column_name IN ('access_token', 'refresh_token')
           AND c.table_name NOT IN (
               'integrations_accountingconnection',
               'integrations_xeroglobalconnection',
               'integrations_qbglobalconnection',
               'integrations_myobglobalconnection',
               'integrations_xpmconnection'
           )
    LOOP
        EXECUTE format('UPDATE %I SET %I = %L', r.table_name, r.column_name, '');
        RAISE NOTICE 'e2e_harden: blanked %.%', r.table_name, r.column_name;
    END LOOP;
END $$;

-- 4. FuseSign envelope references. Signing is out of scope for the suite, but a
--    restored row pointing at a real envelope could let a status-poll or resend
--    path touch a genuine client signing request.
UPDATE core_legaldocument
   SET fusesign_envelope_id = '',
       fusesign_status = 'not_sent';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'core_financialyear'
                  AND column_name = 'package_fusesign_id') THEN
        UPDATE core_financialyear
           SET package_fusesign_id = '',
               package_sent_for_signing = false;
    END IF;
END $$;

-- 5. Marker table. config/settings_e2e.py refuses to start unless this exists
--    and is_e2e is true, so the E2E instance cannot be pointed at production
--    even if DATABASE_URL were misconfigured.
DROP TABLE IF EXISTS e2e_marker;
CREATE TABLE e2e_marker (
    is_e2e boolean NOT NULL DEFAULT true,
    restored_at timestamptz NOT NULL DEFAULT now(),
    source_note text NOT NULL DEFAULT 'restored from production dump'
);
INSERT INTO e2e_marker DEFAULT VALUES;

COMMIT;
