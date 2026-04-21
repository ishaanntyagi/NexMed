/* ==========================================================
   NexMed AI — RAG Consultation Module
   scriptrag.js  ·  single JS for indexrag.html + chatrag.html
   Dispatches by data-page attribute on the <script> tag.
   ========================================================== */

(() => {
    // -------- Page dispatch --------
    const page = document.currentScript?.dataset.page
              || document.querySelector('script[data-page]')?.dataset.page
              || 'intake';

    // -------- Storage keys --------
    const STORAGE = {
        ACTIVE:  'nexmed_rag_active',
        HISTORY: 'nexmed_rag_history',
    };

    // -------- Utilities --------
    const $  = (sel, root = document) => root.querySelector(sel);
    const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

    const esc = (s) => String(s ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

    const uid = () => `sess_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

    const nowIso = () => new Date().toISOString();

    const fmtDate = (iso) => {
        try {
            const d = new Date(iso);
            return d.toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        } catch { return iso; }
    };

    const fmtTime = (iso) => {
        try {
            return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch { return ''; }
    };

    const fmtFileSize = (bytes) => {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
    };

    const kbLabel = (kb) => ({
        general:   'General Orthopedic',
        pediatric: 'Pediatric Fractures',
        trauma:    'Trauma Protocols',
    }[kb] || 'General Orthopedic');

    // -------- Session I/O --------
    const loadActive = () => {
        try { return JSON.parse(localStorage.getItem(STORAGE.ACTIVE) || 'null'); }
        catch { return null; }
    };

    const saveActive = (session) => {
        localStorage.setItem(STORAGE.ACTIVE, JSON.stringify(session));
    };

    const clearActive = () => localStorage.removeItem(STORAGE.ACTIVE);

    const loadHistory = () => {
        try { return JSON.parse(localStorage.getItem(STORAGE.HISTORY) || '[]'); }
        catch { return []; }
    };

    const saveHistory = (list) => {
        localStorage.setItem(STORAGE.HISTORY, JSON.stringify(list));
    };

    const archiveSession = (session) => {
        if (!session) return;
        const list = loadHistory();
        const i = list.findIndex(x => x.id === session.id);
        const firstUser = session.messages.find(m => m.role === 'user');
        const entry = {
            id: session.id,
            createdAt: session.createdAt,
            updatedAt: nowIso(),
            patientName: session.patient?.name || '—',
            patientAge:  session.patient?.age  || '—',
            site:        session.patient?.site || '—',
            messageCount: session.messages.filter(m => m.role !== 'system').length,
            preview: firstUser?.content?.slice(0, 140) || 'No questions asked yet.',
            snapshot: session,
        };
        if (i >= 0) list[i] = entry;
        else list.unshift(entry);
        saveHistory(list);
    };

    // -------- Toast --------
    const toast = (msg, kind = 'success') => {
        const el = $('#toast'); const lbl = $('#toastMsg');
        if (!el || !lbl) return;
        lbl.textContent = msg;
        el.classList.remove('success', 'error');
        el.classList.add(kind, 'show');
        clearTimeout(toast._t);
        toast._t = setTimeout(() => el.classList.remove('show'), 2200);
    };

    // -------- History drawer (shared) --------
    const wireHistoryDrawer = () => {
        const drawer  = $('#historyDrawer');
        const backdrop = $('#drawerBackdrop');
        const close   = $('#drawerClose');
        const openBtn = $('#historyBtn');

        if (!drawer || !backdrop) return;

        const open = () => {
            renderHistory();
            drawer.classList.add('open');
            backdrop.classList.add('open');
        };
        const shut = () => {
            drawer.classList.remove('open');
            backdrop.classList.remove('open');
        };

        openBtn?.addEventListener('click', open);
        close?.addEventListener('click', shut);
        backdrop.addEventListener('click', shut);
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape') shut(); });
    };

    const renderHistory = () => {
        const body = $('#historyBody');
        if (!body) return;
        const list = loadHistory();

        if (list.length === 0) {
            body.innerHTML = `
                <div class="history-empty">
                    <i data-lucide="inbox"></i>
                    <p>No past consultations yet.</p>
                </div>`;
            lucide.createIcons();
            return;
        }

        body.innerHTML = list.map(item => `
            <div class="history-item" data-id="${esc(item.id)}">
                <div class="history-top">
                    <span class="history-name">${esc(item.patientName)}, ${esc(item.patientAge)}</span>
                    <span class="history-date">${esc(fmtDate(item.updatedAt || item.createdAt))}</span>
                </div>
                <div class="history-preview">${esc(item.preview)}</div>
                <div class="history-meta">
                    <span class="history-count">
                        ${esc(item.site)} · ${item.messageCount} msg${item.messageCount === 1 ? '' : 's'}
                    </span>
                    <button class="history-delete" data-id="${esc(item.id)}" title="Delete">
                        <i data-lucide="trash-2"></i>
                    </button>
                </div>
            </div>`).join('');
        lucide.createIcons();

        // Restore a past session (archive current first)
        $$('.history-item', body).forEach(el => {
            el.addEventListener('click', (e) => {
                if (e.target.closest('.history-delete')) return;
                const id = el.dataset.id;
                const found = loadHistory().find(x => x.id === id);
                if (!found) return;
                // Archive current active (if any) before switching
                const current = loadActive();
                if (current) archiveSession(current);
                saveActive(found.snapshot);
                window.location.href = 'chatrag.html';
            });
        });

        $$('.history-delete', body).forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const id = btn.dataset.id;
                const next = loadHistory().filter(x => x.id !== id);
                saveHistory(next);
                renderHistory();
                toast('Consultation deleted.');
            });
        });
    };

    // ===================================================================
    //  INTAKE PAGE
    // ===================================================================
    const initIntake = () => {

        const form = $('#intakeForm');
        if (!form) return;

        // --- Required field tracking ---
        const requiredIds = [
            'patientName', 'age', 'sex',
            'mechanism', 'timeSince', 'site', 'weightBearing',
            'complaint',
            'hospital', 'physician',
            'knowledgeBase',
        ];

        const updateSubmit = () => {
            const filled = requiredIds.filter(id => {
                const el = $('#' + id);
                if (!el) return false;
                return String(el.value || '').trim() !== '';
            }).length;
            const total = requiredIds.length;
            const meta = $('#formMeta');
            const btn  = $('#submitBtn');
            const ok   = filled === total;
            if (meta) {
                meta.innerHTML = `<span class="${ok ? 'count-ok' : 'count-miss'}">${filled}</span> of ${total} required fields completed`;
            }
            if (btn) btn.disabled = !ok;
        };

        requiredIds.forEach(id => {
            const el = $('#' + id);
            if (el) ['input', 'change', 'blur'].forEach(ev => el.addEventListener(ev, updateSubmit));
        });
        updateSubmit();

        // --- Pain slider display ---
        const pain = $('#painLevel');
        const painDisp = $('#painDisplay');
        pain?.addEventListener('input', () => {
            painDisp.textContent = pain.value;
            // Color tint by severity
            if (pain.value <= 3) painDisp.style.color = 'var(--accent-green)';
            else if (pain.value <= 6) painDisp.style.color = 'var(--accent)';
            else painDisp.style.color = 'var(--accent-red)';
        });

        // --- Conditional fields (neuro + prior fracture) ---
        $$('input[data-toggle]').forEach(cb => {
            cb.addEventListener('change', () => {
                const target = $('#' + cb.dataset.toggle);
                if (target) target.classList.toggle('show', cb.checked);
            });
        });

        // --- Image upload ---
        const dz        = $('#imageDropzone');
        const imgInput  = $('#imageInput');
        const previewEl = $('#imagePreview');
        const previewRow = $('#imagePreviewRow');
        const nameEl    = $('#imageName');
        const sizeEl    = $('#imageSize');
        const removeBtn = $('#imageRemove');

        let pendingImage = null;

        const handleImageFile = (file) => {
            if (!file || !file.type.startsWith('image/')) {
                toast('Please select an image file.', 'error');
                return;
            }
            if (file.size > 8 * 1024 * 1024) {
                toast('Image must be under 8 MB for localStorage.', 'error');
                return;
            }
            const reader = new FileReader();
            reader.onload = (ev) => {
                pendingImage = { dataUrl: ev.target.result, filename: file.name, size: file.size };
                previewEl.src = pendingImage.dataUrl;
                nameEl.textContent = file.name;
                sizeEl.textContent = fmtFileSize(file.size);
                previewRow.classList.add('show');
                dz.style.display = 'none';
            };
            reader.readAsDataURL(file);
        };

        dz?.addEventListener('click', () => imgInput.click());
        dz?.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('dragging'); });
        dz?.addEventListener('dragleave', () => dz.classList.remove('dragging'));
        dz?.addEventListener('drop', (e) => {
            e.preventDefault();
            dz.classList.remove('dragging');
            if (e.dataTransfer.files[0]) handleImageFile(e.dataTransfer.files[0]);
        });
        imgInput?.addEventListener('change', (e) => {
            if (e.target.files[0]) handleImageFile(e.target.files[0]);
        });
        removeBtn?.addEventListener('click', () => {
            pendingImage = null;
            previewRow.classList.remove('show');
            dz.style.display = 'block';
            imgInput.value = '';
        });

        // --- New Patient button (clears any previous session) ---
        $('#newPatientBtn')?.addEventListener('click', () => {
            form.reset();
            if (pendingImage) {
                pendingImage = null;
                previewRow.classList.remove('show');
                dz.style.display = 'block';
            }
            painDisp.textContent = '5';
            $$('.conditional-field').forEach(el => el.classList.remove('show'));
            updateSubmit();
            toast('Form cleared.');
        });

        // --- Submit: build session and jump to chat ---
        form.addEventListener('submit', (e) => {
            e.preventDefault();

            // Archive any existing active session before starting a new one
            const existing = loadActive();
            if (existing) archiveSession(existing);

            const session = {
                id: uid(),
                createdAt: nowIso(),
                engine: $('#modelChoice')?.value || 'Groq',
                knowledgeBase: $('#knowledgeBase').value,
                patient: {
                    name:          $('#patientName').value.trim(),
                    age:           $('#age').value.trim(),
                    sex:           $('#sex').value,
                    handedness:    $('#handedness').value || 'Not specified',
                    mechanism:     $('#mechanism').value.trim(),
                    timeSince:     $('#timeSince').value,
                    site:          $('#site').value,
                    weightBearing: $('#weightBearing').value,
                    painLevel:     Number($('#painLevel').value),
                    complaint:     $('#complaint').value.trim(),
                    visibleDeformity:      $('#deformity').checked,
                    openWound:             $('#openWound').checked,
                    neurovascular:         $('#neurovascular').checked,
                    neurovascularDetail:   $('#neuroText').value.trim(),
                    priorFracture:         $('#priorFracture').checked,
                    priorFractureDetail:   $('#priorText').value.trim(),
                    hospital:   $('#hospital').value.trim(),
                    unit:       $('#unit').value.trim(),
                    physician:  $('#physician').value.trim(),
                },
                image: pendingImage,
                messages: [],
            };

            // Seed with a system message
            session.messages.push({
                role: 'system',
                content: `Intake reviewed for ${session.patient.name} (${session.patient.age}, ${session.patient.sex}). ` +
                         `Site: ${session.patient.site}. Mechanism: ${session.patient.mechanism}. ` +
                         `KB: ${kbLabel(session.knowledgeBase)}. Ready to consult.`,
                timestamp: nowIso(),
            });

            saveActive(session);
            window.location.href = 'chatrag.html';
        });

        wireHistoryDrawer();
    };

    // ===================================================================
    //  CHAT PAGE
    // ===================================================================
    const initChat = () => {

        let session = loadActive();
        if (!session) {
            // No active session — bounce back to intake
            window.location.href = 'indexrag.html';
            return;
        }

        const stream        = $('#chatStream');
        const input         = $('#composerInput');
        const sendBtn       = $('#sendBtn');
        const attachBtn     = $('#attachBtn');
        const attachInput   = $('#attachInput');
        const attachWrap    = $('#composerAttachment');
        const attachThumb   = $('#attachThumb');
        const attachName    = $('#attachName');
        const attachRemove  = $('#attachRemove');
        const engineSelect  = $('#modelChoice');

        // --- Hydrate UI from session ---
        const hydrateSidebar = () => {
            // Topbar
            $('#topbarPatient').textContent = `${session.patient.name}, ${session.patient.age} · ${session.patient.sex}`;
            $('#topbarComplaint').textContent = `${session.patient.site} · ${session.patient.mechanism}`;

            // Patient snapshot
            const snap = $('#patientSnapshot');
            snap.innerHTML = `
                <div class="snapshot-name">${esc(session.patient.name)}</div>
                <div class="snapshot-meta">${esc(session.patient.age)} yrs · ${esc(session.patient.sex)} · ${esc(session.patient.handedness)}</div>
                <div class="snapshot-row">
                    <span class="snapshot-key">Site</span>
                    <span class="snapshot-val">${esc(session.patient.site)}</span>
                </div>
                <div class="snapshot-row">
                    <span class="snapshot-key">Pain</span>
                    <span class="snapshot-val">${esc(session.patient.painLevel)} / 10</span>
                </div>
                <div class="snapshot-row">
                    <span class="snapshot-key">Bearing</span>
                    <span class="snapshot-val">${esc(session.patient.weightBearing)}</span>
                </div>
                <div class="snapshot-row">
                    <span class="snapshot-key">Since</span>
                    <span class="snapshot-val">${esc(session.patient.timeSince)}</span>
                </div>
                <a class="snapshot-link" id="showIntakeLink">View full intake</a>
            `;
            $('#showIntakeLink').addEventListener('click', (e) => { e.preventDefault(); openIntakeModal(); });

            // Context badges
            const fieldCount = Object.values(session.patient).filter(v => v !== '' && v !== false && v != null).length;
            $('#ctxFields').innerHTML = `<i data-lucide="list-checks"></i><span>${fieldCount} fields loaded</span>`;
            if (session.image) $('#ctxImage').style.display = 'inline-flex';
            $('#ctxKB').innerHTML = `<i data-lucide="book-open"></i><span>KB: ${esc(kbLabel(session.knowledgeBase))}</span>`;

            // Engine
            if (engineSelect) engineSelect.value = session.engine || 'Groq';
            engineSelect?.addEventListener('change', () => {
                session.engine = engineSelect.value;
                saveActive(session);
            });

            lucide.createIcons();
        };

        // --- Render chat stream ---
        const renderStream = () => {
            stream.innerHTML = session.messages.map(m => renderMessage(m)).join('');
            scrollToBottom();
            lucide.createIcons();
        };

        const renderMessage = (m) => {
            if (m.role === 'system') {
                return `
                    <div class="msg msg-system">
                        <span class="msg-label">System</span>
                        <div class="msg-body">${esc(m.content)}</div>
                        <span class="msg-time">${esc(fmtTime(m.timestamp))}</span>
                    </div>`;
            }
            const label = m.role === 'user' ? 'User' : 'AI';
            const cls   = m.role === 'user' ? 'msg msg-user' : 'msg msg-ai';
            const imgHtml = m.image?.dataUrl
                ? `<img src="${esc(m.image.dataUrl)}" class="msg-image" alt="attached">`
                : '';

            // Citation chips (AI only, if present)
            let citeHtml = '';
            if (m.role === 'ai' && Array.isArray(m.citations) && m.citations.length) {
                const chips = m.citations.map(c => {
                    const preview = esc(c.chunk_preview || '');
                    const file    = esc(c.file || 'source');
                    return `<button class="cite-chip" data-preview="${preview}" title="${preview}">
                                <i data-lucide="file-text"></i>
                                <span>${file}</span>
                            </button>`;
                }).join('');
                citeHtml = `<div class="cite-row">${chips}</div>`;
            }

            return `
                <div class="${cls}">
                    <span class="msg-label">${label}</span>
                    <div class="msg-body">${esc(m.content)}${imgHtml}</div>
                    ${citeHtml}
                    <span class="msg-time">${esc(fmtTime(m.timestamp))}</span>
                </div>`;
        };

        const scrollToBottom = () => {
            requestAnimationFrame(() => { stream.scrollTop = stream.scrollHeight; });
        };

        // --- Composer behavior ---
        const autogrow = () => {
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 120) + 'px';
            sendBtn.disabled = input.value.trim().length === 0;
        };
        input.addEventListener('input', autogrow);
        input.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); send(); }
        });
        autogrow();

        // --- Attachment in composer ---
        let pendingAttach = null;
        attachBtn.addEventListener('click', () => attachInput.click());
        attachInput.addEventListener('change', (e) => {
            const f = e.target.files[0];
            if (!f) return;
            if (f.size > 8 * 1024 * 1024) { toast('Image under 8 MB only.', 'error'); return; }
            const reader = new FileReader();
            reader.onload = (ev) => {
                pendingAttach = { dataUrl: ev.target.result, filename: f.name };
                attachThumb.src = pendingAttach.dataUrl;
                attachName.textContent = f.name;
                attachWrap.classList.add('show');
            };
            reader.readAsDataURL(f);
        });
        attachRemove.addEventListener('click', () => {
            pendingAttach = null;
            attachWrap.classList.remove('show');
            attachInput.value = '';
        });

        // --- Suggested-question chips ---
        $$('.chip').forEach(c => c.addEventListener('click', () => {
            input.value = c.dataset.q || c.textContent;
            autogrow();
            input.focus();
        }));

        // --- Send a message ---
        const send = async () => {
            const text = input.value.trim();
            if (!text) return;

            // Push user message
            const userMsg = {
                role: 'user',
                content: text,
                timestamp: nowIso(),
                image: pendingAttach || undefined,
            };
            session.messages.push(userMsg);
            input.value = '';
            autogrow();

            const hadAttach = !!pendingAttach;
            pendingAttach = null;
            attachWrap.classList.remove('show');
            attachInput.value = '';

            saveActive(session);
            renderStream();

            // Typing indicator
            stream.insertAdjacentHTML('beforeend', `
                <div class="msg msg-ai" id="typingMsg">
                    <span class="msg-label">AI</span>
                    <div class="msg-body">
                        <div class="typing">
                            <div class="typing-dot"></div>
                            <div class="typing-dot"></div>
                            <div class="typing-dot"></div>
                        </div>
                    </div>
                </div>`);
            scrollToBottom();

            // Hit the backend
            let reply = '';
            let citations = [];
            try {
                const res = await fetch('/rag/ask', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({
                        question:       text,
                        patient:        session.patient,
                        knowledge_base: session.knowledgeBase,
                        engine:         session.engine || 'Groq',
                    }),
                });
                const data = await res.json();
                if (!res.ok || data.error) {
                    throw new Error(data.error || `Request failed (${res.status})`);
                }
                reply     = data.answer || '(empty response)';
                citations = Array.isArray(data.citations) ? data.citations : [];
            } catch (err) {
                console.error(err);
                reply = `⚠ Backend error: ${err.message || 'Could not reach /rag/ask.'}`;
            }

            const typingEl = $('#typingMsg');
            typingEl?.remove();

            const aiMsg = {
                role: 'ai',
                content: reply,
                timestamp: nowIso(),
                citations: citations,
            };
            session.messages.push(aiMsg);
            saveActive(session);
            archiveSession(session);
            renderStream();
        };

        // Delegated click: citation chips show their preview via toast
        stream.addEventListener('click', (e) => {
            const chip = e.target.closest('.cite-chip');
            if (!chip) return;
            const preview = chip.dataset.preview || 'No preview.';
            toast(preview);
        });

        sendBtn.addEventListener('click', send);

        // --- Export transcript ---
        $('#exportBtn').addEventListener('click', () => {
            const p = session.patient;
            const header = [
                'NexMed AI — Consultation Transcript',
                '=' .repeat(60),
                `Patient:    ${p.name}, ${p.age}, ${p.sex}`,
                `Site:       ${p.site}`,
                `Mechanism:  ${p.mechanism}`,
                `Pain:       ${p.painLevel}/10`,
                `Physician:  ${p.physician} · ${p.hospital}`,
                `KB:         ${kbLabel(session.knowledgeBase)}`,
                `Started:    ${fmtDate(session.createdAt)}`,
                '=' .repeat(60),
                '',
            ].join('\n');

            const body = session.messages.map(m => {
                const tag = m.role === 'user' ? 'USER' : m.role === 'ai' ? 'AI  ' : 'SYS ';
                return `[${fmtTime(m.timestamp)}] ${tag}  ${m.content}`;
            }).join('\n\n');

            const blob = new Blob([header + body + '\n'], { type: 'text/plain' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = `nexmed-${p.name.replace(/\s+/g, '_')}-${session.id}.txt`;
            a.click();
            URL.revokeObjectURL(a.href);
            toast('Transcript downloaded.');
        });

        // --- New patient (archive + back to intake) ---
        $('#newPatientBtn').addEventListener('click', () => {
            if (!confirm('Archive this consultation and start a new patient?')) return;
            archiveSession(session);
            clearActive();
            window.location.href = 'indexrag.html';
        });

        // --- Intake modal ---
        const modalBackdrop = $('#modalBackdrop');
        const modalBody     = $('#modalBody');

        const openIntakeModal = () => {
            const p = session.patient;
            const yn = (b) => b ? 'Yes' : 'No';
            const rows = (pairs) => pairs.map(([k, v]) =>
                `<div class="record-row"><span class="record-key">${esc(k)}</span><span class="record-val">${esc(v || '—')}</span></div>`
            ).join('');

            modalBody.innerHTML = `
                <div class="record-section">
                    <div class="record-section-head">Patient Demographics</div>
                    ${rows([
                        ['Name / ID', p.name],
                        ['Age', p.age],
                        ['Sex', p.sex],
                        ['Handedness', p.handedness],
                    ])}
                </div>
                <div class="record-section">
                    <div class="record-section-head">Injury Context</div>
                    ${rows([
                        ['Mechanism', p.mechanism],
                        ['Time Since', p.timeSince],
                        ['Fracture Site', p.site],
                        ['Weight-bearing', p.weightBearing],
                        ['Pain Level', `${p.painLevel} / 10`],
                    ])}
                </div>
                <div class="record-section">
                    <div class="record-section-head">Clinical Symptoms</div>
                    ${rows([
                        ['Primary Complaint', p.complaint],
                        ['Visible Deformity', yn(p.visibleDeformity)],
                        ['Open Wound', yn(p.openWound)],
                        ['Neurovascular Symptoms', p.neurovascular ? (p.neurovascularDetail || 'Yes') : 'No'],
                        ['Prior Fracture', p.priorFracture ? (p.priorFractureDetail || 'Yes') : 'No'],
                    ])}
                </div>
                <div class="record-section">
                    <div class="record-section-head">Facility</div>
                    ${rows([
                        ['Hospital', p.hospital],
                        ['Unit / Ward', p.unit],
                        ['Physician', p.physician],
                    ])}
                </div>
                <div class="record-section">
                    <div class="record-section-head">Session</div>
                    ${rows([
                        ['Knowledge Base', kbLabel(session.knowledgeBase)],
                        ['Engine', session.engine || 'Groq'],
                        ['Session ID', session.id],
                        ['Started', fmtDate(session.createdAt)],
                        ['Image', session.image?.filename || '—'],
                    ])}
                </div>
            `;
            modalBackdrop.classList.add('open');
        };

        $('#viewIntakeBtn').addEventListener('click', openIntakeModal);
        $('#modalClose').addEventListener('click', () => modalBackdrop.classList.remove('open'));
        modalBackdrop.addEventListener('click', (e) => {
            if (e.target === modalBackdrop) modalBackdrop.classList.remove('open');
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') modalBackdrop.classList.remove('open');
        });

        // --- Kick off ---
        hydrateSidebar();
        renderStream();
        input.focus();

        wireHistoryDrawer();
    };

    // ===================================================================
    //  BOOT
    // ===================================================================
    document.addEventListener('DOMContentLoaded', () => {
        if (page === 'intake') initIntake();
        else if (page === 'chat') initChat();
    });

})();