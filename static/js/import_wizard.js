/**
 * ─── StatementHub Import Wizard — Shared JavaScript ───
 *
 * Unified logic for all import mapping wizards (TB import, journal upload,
 * cloud import). Each wizard template initialises this module with its own
 * configuration via ImportWizard.init(config).
 *
 * Required config keys:
 *   standardAccounts  — Array of {id, standard_code, line_item_label, statement_section}
 *   entityAccounts    — Array of {code, name, section, maps_to_id}
 *   entityPk          — String UUID of the entity
 *   fyPk              — String UUID of the financial year
 *   suggestCodeUrl    — URL for the entity_coa_suggest_code AJAX endpoint
 *   quickAddUrl       — URL for the quick_add_entity_account AJAX endpoint
 *   csrfToken         — CSRF token string
 *   balanceRequired   — Boolean, whether balance check should block submit (default true)
 */
var ImportWizard = (function() {
    'use strict';

    var config = {};
    var activeSearchIdx = null;

    // ================================================================
    // INITIALISATION
    // ================================================================
    function init(cfg) {
        config = cfg;
        config.balanceRequired = cfg.balanceRequired !== false; // default true

        populateStatementLineDropdowns();
        populateQuickAddMapsTo();
        checkBalance();
        bindEntitySearch();
        bindQuickAddModal();
        bindFilters();
        bindApproveAllLearned();
        bindAutoMatchAll();
        bindCountUpdates();
        bindFormSubmit();
    }

    // ================================================================
    // POPULATE STATEMENT LINE DROPDOWNS
    // ================================================================
    function populateStatementLineDropdowns() {
        var selects = document.querySelectorAll('.mapping-select');
        selects.forEach(function(select) {
            var groups = {};
            config.standardAccounts.forEach(function(acct) {
                var section = acct.statement_section || 'Other';
                if (!groups[section]) groups[section] = [];
                groups[section].push(acct);
            });
            Object.keys(groups).sort().forEach(function(section) {
                var optgroup = document.createElement('optgroup');
                optgroup.label = section;
                groups[section].forEach(function(acct) {
                    var option = document.createElement('option');
                    option.value = acct.id;
                    option.textContent = acct.standard_code + ' - ' + acct.line_item_label;
                    optgroup.appendChild(option);
                });
                select.appendChild(optgroup);
            });
            var learnedValue = select.dataset.learnedValue;
            if (learnedValue) select.value = learnedValue;
        });
    }

    function populateQuickAddMapsTo() {
        var qaMapsTo = document.getElementById('qaMapsTo');
        if (!qaMapsTo) return;
        config.standardAccounts.forEach(function(acct) {
            var opt = document.createElement('option');
            opt.value = acct.id;
            opt.textContent = acct.standard_code + ' - ' + acct.line_item_label;
            qaMapsTo.appendChild(opt);
        });
    }

    // ================================================================
    // BALANCE CHECK
    // ================================================================
    function checkBalance() {
        var totalDr = 0, totalCr = 0;
        document.querySelectorAll('.mapping-row').forEach(function(row) {
            var dr = parseFloat(row.querySelector('.debit-cell').textContent.replace(/,/g, '')) || 0;
            var cr = parseFloat(row.querySelector('.credit-cell').textContent.replace(/,/g, '')) || 0;
            totalDr += dr;
            totalCr += cr;
        });

        var totalDebitEl = document.getElementById('totalDebit');
        var totalCreditEl = document.getElementById('totalCredit');
        if (totalDebitEl) totalDebitEl.textContent = totalDr.toLocaleString('en-AU', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        if (totalCreditEl) totalCreditEl.textContent = totalCr.toLocaleString('en-AU', {minimumFractionDigits: 2, maximumFractionDigits: 2});

        var diff = Math.abs(totalDr - totalCr);
        var balanced = diff < 0.02; // Allow 1 cent rounding

        var balanceCard = document.getElementById('balanceCard');
        var balanceStatus = document.getElementById('balanceStatus');
        var balanceBar = document.getElementById('balanceBarInner');
        var balanceMsg = document.getElementById('balanceMessage');
        var balanceDetail = document.getElementById('balanceDetail');
        var commitBtn = document.getElementById('commitBtn');

        if (balanced) {
            if (balanceCard) balanceCard.className = 'card text-center border-success';
            if (balanceStatus) balanceStatus.innerHTML = '<i class="bi bi-check-circle text-success"></i> Balanced';
            if (balanceBar) balanceBar.className = 'card-body py-2 d-flex justify-content-between align-items-center balance-ok';
            if (balanceMsg) balanceMsg.innerHTML = '<i class="bi bi-check-circle-fill text-success"></i> Trial balance is in balance';
            if (balanceDetail) balanceDetail.textContent = 'Dr $' + totalDr.toLocaleString('en-AU', {minimumFractionDigits: 2}) + ' = Cr $' + totalCr.toLocaleString('en-AU', {minimumFractionDigits: 2});
            if (commitBtn) {
                commitBtn.disabled = false;
                commitBtn.classList.remove('btn-secondary');
                commitBtn.classList.add('btn-success');
            }
        } else {
            if (balanceCard) balanceCard.className = 'card text-center border-danger';
            if (balanceStatus) balanceStatus.innerHTML = '<i class="bi bi-exclamation-triangle text-danger"></i> Out';
            if (balanceBar) balanceBar.className = 'card-body py-2 d-flex justify-content-between align-items-center balance-error';
            if (balanceMsg) balanceMsg.innerHTML = '<i class="bi bi-exclamation-triangle-fill text-danger"></i> Trial balance is OUT OF BALANCE';
            if (balanceDetail) balanceDetail.textContent = 'Dr $' + totalDr.toLocaleString('en-AU', {minimumFractionDigits: 2}) + ' vs Cr $' + totalCr.toLocaleString('en-AU', {minimumFractionDigits: 2}) + ' — Difference: $' + diff.toLocaleString('en-AU', {minimumFractionDigits: 2});
            if (commitBtn && config.balanceRequired) {
                commitBtn.disabled = true;
                commitBtn.classList.remove('btn-success');
                commitBtn.classList.add('btn-secondary');
            }
        }
    }

    // ================================================================
    // ENTITY ACCOUNT SEARCH DROPDOWN
    // ================================================================
    function bindEntitySearch() {
        var dropdown = document.getElementById('entitySearchDropdown');
        var searchInput = document.getElementById('entitySearchInput');
        var searchResults = document.getElementById('entitySearchResults');
        var createNewBtn = document.getElementById('entityCreateNew');
        if (!dropdown || !searchInput) return;

        window.openEntitySearch = function(displayEl) {
            var idx = displayEl.dataset.idx;
            activeSearchIdx = idx;

            var rect = displayEl.getBoundingClientRect();
            dropdown.style.top = (rect.bottom + window.scrollY + 2) + 'px';
            dropdown.style.left = (rect.left + window.scrollX) + 'px';
            dropdown.classList.add('show');

            searchInput.value = '';
            searchInput.focus();
            renderEntityResults('');
        };

        document.addEventListener('click', function(e) {
            var inModal = e.target.closest('#quickAddModal');
            if (!dropdown.contains(e.target) && !e.target.classList.contains('entity-acct-display') && !e.target.closest('.entity-acct-display')) {
                dropdown.classList.remove('show');
                if (!inModal) {
                    activeSearchIdx = null;
                }
            }
        });

        searchInput.addEventListener('input', function() {
            renderEntityResults(this.value.trim().toLowerCase());
        });

        if (createNewBtn) {
            createNewBtn.addEventListener('click', function() {
                dropdown.classList.remove('show');
                openQuickAdd();
            });
        }
    }

    function renderEntityResults(query) {
        var searchResults = document.getElementById('entitySearchResults');
        if (!searchResults) return;

        var filtered = config.entityAccounts;
        if (query) {
            filtered = config.entityAccounts.filter(function(a) {
                return a.code.toLowerCase().includes(query) || a.name.toLowerCase().includes(query);
            });
        }

        if (filtered.length === 0) {
            searchResults.innerHTML = '<div class="text-center text-muted py-3"><small>No matching accounts. Click below to create one.</small></div>';
            return;
        }

        var html = '';
        filtered.slice(0, 50).forEach(function(a) {
            html += '<div class="result-item" data-code="' + a.code + '" data-name="' + a.name.replace(/"/g, '&quot;') + '" data-maps-to="' + (a.maps_to_id || '') + '">';
            html += '<span class="code">' + a.code + '</span>';
            html += '<span>' + a.name + '</span>';
            if (a.section) html += ' <small class="text-muted">(' + a.section + ')</small>';
            html += '</div>';
        });
        searchResults.innerHTML = html;

        searchResults.querySelectorAll('.result-item').forEach(function(item) {
            item.addEventListener('click', function() {
                assignEntityAccount(activeSearchIdx, this.dataset.code, this.dataset.name, this.dataset.mapsTo);
                document.getElementById('entitySearchDropdown').classList.remove('show');
            });
        });
    }

    function assignEntityAccount(idx, code, name, mapsToId) {
        var row = document.querySelector('.mapping-row[data-idx="' + idx + '"]');
        if (!row) return;

        row.querySelector('.entity-acct-input').value = code;

        var display = row.querySelector('.entity-acct-display');
        display.className = 'entity-acct-display assigned';
        display.innerHTML = '<span class="badge bg-primary">' + code + '</span><span class="small">' + name + '</span>';

        if (mapsToId) {
            var select = row.querySelector('.mapping-select');
            if (select) select.value = mapsToId;
        }

        updateCounts();
    }

    // ================================================================
    // QUICK-ADD MODAL
    // ================================================================
    var qaModal = null;
    var suggestTimer = null;

    function bindQuickAddModal() {
        var modalEl = document.getElementById('quickAddModal');
        if (!modalEl) return;
        qaModal = new bootstrap.Modal(modalEl);

        document.getElementById('qaSection').addEventListener('change', function() {
            suggestCode(document.getElementById('qaName').value);
        });
        document.getElementById('qaName').addEventListener('input', function() {
            suggestCode(this.value);
        });

        document.getElementById('qaSubmit').addEventListener('click', function() {
            submitQuickAdd(this);
        });
    }

    function openQuickAdd() {
        if (activeSearchIdx === null) return;
        var row = document.querySelector('.mapping-row[data-idx="' + activeSearchIdx + '"]');
        if (!row) return;

        var sourceCode = row.dataset.sourceCode;
        var sourceName = row.dataset.sourceName;

        document.getElementById('qaName').value = sourceName;
        document.getElementById('qaTaxCode').value = '';
        document.getElementById('qaClassification').value = '';
        document.getElementById('qaMapsTo').value = '';

        // Clear any previous duplicate warning
        var warnEl = document.getElementById('qaDuplicateWarning');
        if (warnEl) { warnEl.className = 'd-none'; warnEl.innerHTML = ''; }

        var guessed = guessSection(sourceCode);
        selectSectionOption(guessed.sectionKey, guessed.rangeMin);
        suggestCode(sourceName);

        qaModal.show();
    }

    function guessSection(code) {
        var num = parseInt(code);
        if (isNaN(num)) return {sectionKey: '', rangeMin: 0, rangeMax: 0};
        if (num < 1000) return {sectionKey: 'revenue', rangeMin: 0, rangeMax: 999};
        if (num < 2000) return {sectionKey: 'expenses', rangeMin: 1000, rangeMax: 1999};
        if (num < 2500) return {sectionKey: 'assets', rangeMin: 2000, rangeMax: 2499};
        if (num < 3000) return {sectionKey: 'assets', rangeMin: 2500, rangeMax: 2999};
        if (num < 3500) return {sectionKey: 'liabilities', rangeMin: 3000, rangeMax: 3499};
        if (num < 4000) return {sectionKey: 'liabilities', rangeMin: 3500, rangeMax: 3999};
        return {sectionKey: 'equity', rangeMin: 4000, rangeMax: 4999};
    }

    function selectSectionOption(sectionKey, rangeMin) {
        var sel = document.getElementById('qaSection');
        if (!sectionKey) { sel.selectedIndex = 0; return; }
        for (var i = 0; i < sel.options.length; i++) {
            var opt = sel.options[i];
            if (opt.value === sectionKey && parseInt(opt.dataset.rangeMin) === rangeMin) {
                sel.selectedIndex = i;
                return;
            }
        }
        sel.value = sectionKey;
    }

    function suggestCode(accountName) {
        var sel = document.getElementById('qaSection');
        var opt = sel.options[sel.selectedIndex];
        var codeInput = document.getElementById('qaCode');
        var hint = document.getElementById('qaCodeHint');
        var warnEl = document.getElementById('qaDuplicateWarning');

        if (!opt || !opt.value || !accountName || !accountName.trim()) {
            codeInput.value = '';
            hint.textContent = '';
            if (warnEl) { warnEl.className = 'd-none'; warnEl.innerHTML = ''; }
            return;
        }

        if (suggestTimer) clearTimeout(suggestTimer);
        hint.textContent = 'Calculating...';

        suggestTimer = setTimeout(function() {
            fetch(config.suggestCodeUrl + '?section=' +
                  encodeURIComponent(opt.value) + '&account_name=' +
                  encodeURIComponent(accountName.trim()))
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.suggested_code) {
                    codeInput.value = data.suggested_code;
                    hint.textContent = data.position_info || '';
                } else {
                    hint.textContent = data.position_info || 'No available codes in this range.';
                }

                // Handle duplicate / similar match warnings
                if (warnEl) {
                    if (data.existing_match) {
                        warnEl.className = 'iw-existing-match mb-3';
                        warnEl.innerHTML = '<i class="bi bi-check-circle-fill text-success"></i> <strong>Existing account found:</strong> ' +
                            data.existing_code + ' — ' + data.existing_name +
                            '<br><small>This account already exists. You can assign it directly instead of creating a duplicate.</small>' +
                            '<br><button type="button" class="btn btn-sm btn-success mt-1" id="qaUseExisting">' +
                            '<i class="bi bi-arrow-right-circle"></i> Use Existing Account</button>';
                        // Bind the "Use Existing" button
                        setTimeout(function() {
                            var useBtn = document.getElementById('qaUseExisting');
                            if (useBtn) {
                                useBtn.addEventListener('click', function() {
                                    assignEntityAccount(activeSearchIdx, data.existing_code, data.existing_name, '');
                                    qaModal.hide();
                                });
                            }
                        }, 50);
                    } else if (data.similar_match && data.similar_ratio >= 80) {
                        warnEl.className = 'iw-similar-warning mb-3';
                        warnEl.innerHTML = '<i class="bi bi-exclamation-triangle-fill text-warning"></i> <strong>Similar account found (' + data.similar_ratio + '% match):</strong> ' +
                            data.similar_code + ' — ' + data.similar_name +
                            '<br><small>Check if this is the same account to avoid duplicates.</small>' +
                            '<br><button type="button" class="btn btn-sm btn-outline-warning mt-1" id="qaUseSimilar">' +
                            '<i class="bi bi-arrow-right-circle"></i> Use This Instead</button>';
                        setTimeout(function() {
                            var simBtn = document.getElementById('qaUseSimilar');
                            if (simBtn) {
                                simBtn.addEventListener('click', function() {
                                    assignEntityAccount(activeSearchIdx, data.similar_code, data.similar_name, '');
                                    qaModal.hide();
                                });
                            }
                        }, 50);
                    } else {
                        warnEl.className = 'd-none';
                        warnEl.innerHTML = '';
                    }
                }
            })
            .catch(function() {
                hint.textContent = 'Could not suggest code.';
            });
        }, 300);
    }

    function submitQuickAdd(btn) {
        var targetIdx = activeSearchIdx;
        var section = document.getElementById('qaSection').value;
        var code = document.getElementById('qaCode').value.trim();
        var name = document.getElementById('qaName').value.trim();
        var taxCode = document.getElementById('qaTaxCode').value;
        var classification = document.getElementById('qaClassification').value.trim();
        var mapsTo = document.getElementById('qaMapsTo').value;

        if (!section || !code || !name) {
            alert('Section, Code, and Name are required.');
            return;
        }

        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Creating...';

        fetch(config.quickAddUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': config.csrfToken,
            },
            body: JSON.stringify({
                entity_pk: config.entityPk,
                section: section,
                account_code: code,
                account_name: name,
                tax_code: taxCode,
                classification: classification,
                maps_to_id: mapsTo,
            }),
        })
        .then(function(resp) { return resp.json(); })
        .then(function(data) {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-plus-circle"></i> Create & Assign';
            if (data.success) {
                config.entityAccounts.push({
                    code: data.account.code,
                    name: data.account.name,
                    section: data.account.section,
                    maps_to_id: data.account.maps_to_id || '',
                });
                assignEntityAccount(targetIdx, data.account.code, data.account.name, data.account.maps_to_id || '');
                qaModal.hide();
            } else {
                alert('Error: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(function(err) {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-plus-circle"></i> Create & Assign';
            alert('Network error: ' + err.message);
        });
    }

    // ================================================================
    // FILTERS
    // ================================================================
    function bindFilters() {
        var searchFilter = document.getElementById('searchFilter');
        if (searchFilter) {
            searchFilter.addEventListener('input', function() {
                var query = this.value.toLowerCase();
                document.querySelectorAll('.mapping-row').forEach(function(row) {
                    var text = row.textContent.toLowerCase();
                    row.style.display = text.includes(query) ? '' : 'none';
                });
            });
        }

        document.querySelectorAll('.filter-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                document.querySelectorAll('.filter-btn').forEach(function(b) { b.classList.remove('active'); });
                this.classList.add('active');
                var filter = this.dataset.filter;
                document.querySelectorAll('.mapping-row').forEach(function(row) {
                    if (filter === 'all') {
                        row.style.display = '';
                    } else {
                        row.style.display = row.dataset.confidence === filter ? '' : 'none';
                    }
                });
            });
        });
    }

    // ================================================================
    // APPROVE ALL LEARNED
    // ================================================================
    function bindApproveAllLearned() {
        var btn = document.getElementById('approveAllLearned');
        if (!btn) return;
        btn.addEventListener('click', function() {
            var count = 0;
            document.querySelectorAll('.mapping-select').forEach(function(select) {
                var learnedValue = select.dataset.learnedValue;
                if (learnedValue && select.value !== learnedValue) {
                    select.value = learnedValue;
                    count++;
                }
            });
            alert(count > 0 ? 'Restored ' + count + ' learned mapping(s).' : 'All learned mappings are already set.');
        });
    }

    // ================================================================
    // AUTO-MATCH BY CODE
    // ================================================================
    function bindAutoMatchAll() {
        var btn = document.getElementById('autoMatchAll');
        if (!btn) return;
        btn.addEventListener('click', function() {
            var matched = 0;
            var entityMap = {};
            config.entityAccounts.forEach(function(a) {
                entityMap[a.code.toLowerCase()] = a;
            });

            document.querySelectorAll('.mapping-row').forEach(function(row) {
                var code = row.dataset.sourceCode.toLowerCase();
                var input = row.querySelector('.entity-acct-input');
                if (input.value) return;

                if (entityMap[code]) {
                    var a = entityMap[code];
                    assignEntityAccount(row.dataset.idx, a.code, a.name, a.maps_to_id || '');
                    matched++;
                }
            });
            alert(matched > 0 ? 'Auto-matched ' + matched + ' account(s) by code.' : 'No additional matches found.');
        });
    }

    // ================================================================
    // COUNT UPDATES
    // ================================================================
    function updateCounts() {
        var mapped = 0, unmapped = 0;
        document.querySelectorAll('.mapping-row').forEach(function(row) {
            var entityVal = row.querySelector('.entity-acct-input').value;
            var mappingVal = row.querySelector('.mapping-select').value;
            if (entityVal || mappingVal) {
                mapped++;
            } else {
                unmapped++;
            }
        });
        var mappedEl = document.getElementById('mappedCount');
        var unmappedEl = document.getElementById('unmappedCount');
        if (mappedEl) mappedEl.textContent = mapped;
        if (unmappedEl) unmappedEl.textContent = unmapped;
    }

    function bindCountUpdates() {
        document.querySelectorAll('.mapping-select').forEach(function(select) {
            select.addEventListener('change', updateCounts);
        });
    }

    // ================================================================
    // FORM SUBMIT VALIDATION
    // ================================================================
    function bindFormSubmit() {
        var form = document.getElementById('importForm');
        if (!form) return;

        form.addEventListener('submit', function(e) {
            if (config.balanceRequired) {
                var totalDr = 0, totalCr = 0;
                document.querySelectorAll('.mapping-row').forEach(function(row) {
                    var dr = parseFloat(row.querySelector('.debit-cell').textContent.replace(/,/g, '')) || 0;
                    var cr = parseFloat(row.querySelector('.credit-cell').textContent.replace(/,/g, '')) || 0;
                    totalDr += dr;
                    totalCr += cr;
                });
                var diff = Math.abs(totalDr - totalCr);
                if (diff >= 0.02) {
                    e.preventDefault();
                    alert('Cannot import: Trial balance is out of balance by $' + diff.toFixed(2) + '. Debits must equal credits.');
                    return;
                }
            }

            var unmappedCount = 0;
            document.querySelectorAll('.mapping-row').forEach(function(row) {
                var entityVal = row.querySelector('.entity-acct-input').value;
                if (!entityVal) unmappedCount++;
            });
            if (unmappedCount > 0) {
                if (!confirm(unmappedCount + ' line(s) have no entity account assigned. They will be imported with the source code/name only. Continue?')) {
                    e.preventDefault();
                }
            }
        });
    }

    // ================================================================
    // PUBLIC API
    // ================================================================
    return {
        init: init,
        assignEntityAccount: assignEntityAccount,
        checkBalance: checkBalance,
        updateCounts: updateCounts
    };
})();
