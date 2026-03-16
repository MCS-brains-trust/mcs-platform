# StatementHub — Claude Code Master Reference
**Platform:** Cloud-based financial statement preparation and practice management for Australian accounting firms.
**Owner:** MC & S Pty Ltd (ABN 69 079 892 023) — Elio Scarton, Managing Director
**Live URL:** https://statementhub.com.au

---

## Stack
Django (Python) · PostgreSQL (managed DO) · Celery/Redis · Gunicorn · pgvector · python-docx/docxtpl · AWS Textract · Anthropic Claude API

## Paths
- **Server:** `/opt/statementhub` (Ubuntu, Digital Ocean Droplet)
- **Venv:** `/opt/statementhub/venv/bin/activate`
- **Local (Windows):** `C:\Users\Elio\mcs-platform` (Anaconda base env)
- **GitHub:** `github.com/faceless-truth/mcs-platform` (master branch)

## Deploy Sequence (always in this order)
```bash
source /opt/statementhub/venv/bin/activate
git pull origin master
python3 manage.py migrate
sudo systemctl restart gunicorn celery celerybeat
```

---

## Critical Rules — Never Break These
1. **Always use `python3`** (not `python`) on the server
2. **Every Claude Code session must end with git add -A → commit → push** — confirm commit hash before ending
3. **All journal entries must be double-sided** — single-sided postings are Priority 1 bugs
4. **Never write migration files manually** — always use `python3 manage.py makemigrations`
5. **Server-generated migrations** (merge migrations etc.) must be immediately pulled back to local and pushed to GitHub
6. **Financial year status flow is one-way:** draft → in_review → finalised → reopened. Never skip states.
7. **Watermarks (DRAFT/AUDIT RISK) must never appear** in finalised client package output
8. **Never run `manage.py shell` locally** — .env with SECRET_KEY is not present on Windows dev machine

## PowerShell Rules (Windows dev machine)
- Use `;` not `&&` to chain commands
- Run git commands one at a time on separate lines

---

## Staff
| Name | Role | Notes |
|------|------|-------|
| Elio Scarton | Managing Director | Power user, reviews all complex work |
| Harry Yeelock Gan | CPA Senior | SMSF specialist |
| Ross Mercuri | CA | Building into senior accountant |
| Brooke Austin | Accountant | Individual returns, progressing to business |
| Lyn Karman | Part-time | 25 years institutional knowledge |
| Eliza | Admin / AI Champion | Knowledge Brain administrator |

## Entity Mix
- 4,383 active entities total
- 538 companies · 351 trusts · 163 SMSFs · 108 partnerships · 3,191 individuals
- ~538 companies onboarded in StatementHub as of current state

---

## Section Documentation (read these for task-specific context)

| Task | Read This File |
|------|---------------|
| Models, database schema | `.claude/docs/MODELS.md` |
| Document generation (financial statements, docxtpl) | `.claude/docs/DOCGEN.md` |
| Eva AI engine, findings, Knowledge Brain | `.claude/docs/EVA.md` |
| Deploy, server ops, migrations | `.claude/docs/DEPLOYMENT.md` |
| Xero, FuseSign, AWS Textract integrations | `.claude/docs/INTEGRATIONS.md` |
| Financial year workflow, status machine | `.claude/docs/WORKFLOWS.md` |
| Workpapers, templates | `.claude/docs/WORKPAPERS.md` |
