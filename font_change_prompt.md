# Font Change: Times New Roman → Arial

## PHASE 1 — AUDIT (read files, do not write any code yet)

Confirm the exact constant definitions in each file that need changing:

1. `generate_fs_templates.py` line 27-28 — confirm FONT_BODY and FONT_NAME both equal "Times New Roman"
2. `fs_template_service.py` — confirm the constant name and line where _NOTES_FONT and FONT are defined as "Times New Roman"
3. `docgen.py` line 44 — confirm FONT_NAME = "Times New Roman"
4. `taxplan_docgen.py` line 38 — confirm FONT_NAME = "Times New Roman"
5. `table_helpers.py` line 20 — confirm FONT_NAME = "Times New Roman"
6. `template_renderer.py` line 47 — confirm styles["font_name"] default = "Times New Roman"

Also confirm the hard-coded "Times New Roman" strings (not via constant) at:
- `fs_template_service.py` lines 2309 and 3058
- `generate_fs_templates.py` line 726
- `views.py` lines 11194, 11202, 11210, 11249, 11260, 11287, 11310, 11331

Do not proceed until all Phase 1 findings are confirmed.

---

## PHASE 2 — FIX

Change every "Times New Roman" reference to "Arial" across all document generation files. Apply as follows:

1. **generate_fs_templates.py**: Change `FONT_BODY = "Times New Roman"` → `"Arial"` and `FONT_NAME = FONT_BODY` (no change needed, inherits). Also fix hard-coded string at line 726.

2. **fs_template_service.py**: Change the constant definition of `_NOTES_FONT` and `FONT` from `"Times New Roman"` → `"Arial"`. Also fix hard-coded strings at lines 2309 and 3058.

3. **docgen.py**: Change `FONT_NAME = "Times New Roman"` → `"Arial"` at line 44.

4. **taxplan_docgen.py**: Change `FONT_NAME = "Times New Roman"` → `"Arial"` at line 38.

5. **table_helpers.py**: Change `FONT_NAME = "Times New Roman"` (or default parameter) → `"Arial"` at line 20.

6. **template_renderer.py**: Change `styles["font_name"]` default `= "Times New Roman"` → `"Arial"` at line 47.

7. **views.py**: Change all 8 hard-coded `"Times New Roman"` strings at lines 11194, 11202, 11210, 11249, 11260, 11287, 11310, 11331 → `"Arial"`.

**Do NOT change:**
- Arial references (already correct)
- Palatino Linotype references
- Calibri references in views_trust.py and views_upgrades.py

---

## PHASE 3 — VERIFY

Run on the server (Claude Code cannot run this locally — no SECRET_KEY on Windows dev):

```
source /opt/statementhub/venv/bin/activate && cd /opt/statementhub && python3 manage.py generate_fs_templates --force
```

Then confirm zero remaining Times New Roman references:

```
grep -rn "Times New Roman" /opt/statementhub/core/ --include="*.py"
```

Report the full grep output — should return zero lines.

---

## PHASE 4 — COMMIT

```
git add -A
git commit -m "feat: change all document fonts from Times New Roman to Arial across all generation files"
git push origin master
```

Report the commit hash.
