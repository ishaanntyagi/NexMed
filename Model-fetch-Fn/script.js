const fileInput   = document.getElementById('fileInput');
const dropZone    = document.getElementById('dropZone');
const browseBtn   = document.getElementById('browseBtn');
const extractBtn  = document.getElementById('extractBtn');
const reasonBtn   = document.getElementById('reasonBtn');
const featuresOutput = document.getElementById('featuresOutput');
const reportOutput   = document.getElementById('reportOutput');
const extractTag  = document.getElementById('extractTag');
const reportTag   = document.getElementById('reportTag');

let currentFile = null;
let currentFeaturesRaw = null;

// ─── DRAG & DROP ──────────────────────────────────────────
dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));

dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
});

browseBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    fileInput.click();
});

dropZone.addEventListener('click', () => fileInput.click());

fileInput.addEventListener('change', (e) => {
    if (e.target.files[0]) handleFile(e.target.files[0]);
});

function handleFile(file) {
    currentFile = file;
    currentFeaturesRaw = null;
    const reader = new FileReader();
    reader.onload = (event) => {
        document.getElementById('imagePreview').src = event.target.result;
        document.getElementById('imagePreviewContainer').classList.remove('hidden');
        document.getElementById('previewLabel').textContent = file.name;
    };
    reader.readAsDataURL(file);
    extractBtn.disabled = false;
    reasonBtn.disabled  = true;

    resetTag(extractTag);
    resetTag(reportTag);
    featuresOutput.innerHTML = `<span class="placeholder-text">Run Feature Extraction to populate findings.</span>`;
    reportOutput.innerHTML   = `<span class="placeholder-text">Run Clinical Reasoning to generate report.</span>`;
    extractBtn.innerHTML = `
        <span class="btn-num">01</span>
        <span class="btn-label">
            <i data-lucide="scan-eye"></i>
            Feature Extraction
        </span>
        <i data-lucide="arrow-right" class="btn-arrow"></i>`;
    reasonBtn.innerHTML = `
        <span class="btn-num">02</span>
        <span class="btn-label">
            <i data-lucide="brain-circuit"></i>
            Clinical Reasoning
        </span>
        <i data-lucide="arrow-right" class="btn-arrow"></i>`;
    lucide.createIcons();
}

function resetTag(tag) {
    tag.textContent = 'PENDING';
    tag.style.color = '';
    tag.style.borderColor = '';
    tag.style.background  = '';
}

function markComplete(tag) {
    tag.textContent = 'COMPLETE';
    tag.style.color = 'var(--accent-green)';
    tag.style.borderColor = 'rgba(48,209,88,0.3)';
    tag.style.background  = 'var(--green-dim)';
}

function markError(tag) {
    tag.textContent = 'ERROR';
    tag.style.color = '#FF453A';
    tag.style.borderColor = 'rgba(255,69,58,0.3)';
    tag.style.background  = 'rgba(255,69,58,0.12)';
}

// small HTML-escape so bullet text can't inject tags
function esc(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// ─── PHASE 1: FEATURE EXTRACTION ─────────────────────────
extractBtn.addEventListener('click', async () => {
    if (!currentFile) return;

    featuresOutput.innerHTML = `<span class="placeholder-text">Analyzing image...</span>`;
    extractTag.textContent   = 'PROCESSING';
    extractTag.style.color   = 'var(--accent)';
    extractTag.style.borderColor = 'rgba(41,151,255,0.3)';
    extractTag.style.background  = 'var(--accent-dim)';
    extractBtn.innerHTML = `
        <span class="btn-num">01</span>
        <span class="btn-label"><span class="loader"></span> Processing...</span>`;
    extractBtn.disabled = true;

    const formData = new FormData();
    formData.append('image', currentFile);
    formData.append('vision_engine', document.getElementById('modelChoice').value);

    try {
        const res  = await fetch('/analyze', { method: 'POST', body: formData });
        const data = await res.json();

        if (!res.ok || data.error) throw new Error(data.error || 'Request failed');

        currentFeaturesRaw = data.features_raw;
        renderFeatures(data.features);

        markComplete(extractTag);
        extractBtn.innerHTML = `
            <span class="btn-num">01</span>
            <span class="btn-label">
                <i data-lucide="check-circle-2"></i> Extracted
            </span>`;
        lucide.createIcons();
        reasonBtn.disabled = false;

    } catch (err) {
        console.error(err);
        featuresOutput.innerHTML =
            `<span style="color:#FF453A;">⚠ ${esc(err.message || 'Backend connection failed.')}</span>`;
        markError(extractTag);
        extractBtn.innerHTML = `
            <span class="btn-num">01</span>
            <span class="btn-label">
                <i data-lucide="scan-eye"></i> Retry Extraction
            </span>
            <i data-lucide="arrow-right" class="btn-arrow"></i>`;
        lucide.createIcons();
        extractBtn.disabled = false;
    }
});

// ─── Feature row styling helpers ──────────────────────────

// Detect fracture presence from the value string.
// Returns 'yes' | 'no' | null (unknown / inconclusive / mixed).
function detectFracturePresence(val) {
    const s = String(val).toLowerCase().trim();
    if (/^\s*yes\b/.test(s) || /\bpresent\b/.test(s)) return 'yes';
    if (/^\s*no\b/.test(s)  || /\babsent\b/.test(s) || /no\s+fracture/.test(s)) return 'no';
    return null;
}

// Extract a 0-100 percentage from the confidence string.
// Returns { num: Number, rest: String } or null if we can't find one.
function parseConfidence(val) {
    const s = String(val);
    const m = s.match(/(\d{1,3})\s*%/);
    if (!m) return null;
    const num = Math.max(0, Math.min(100, parseInt(m[1], 10)));
    // Everything that isn't the "NN%" token, cleaned up
    const rest = s.replace(m[0], '').replace(/^[\s,.\-–—:]+/, '').trim();
    return { num, rest };
}

// Build the HTML for a single feature value based on key context.
function renderFeatureValue(key, val) {
    const k = String(key).toUpperCase();

    // FRACTURE row
    if (k.includes('FRACTURE')) {
        const state = detectFracturePresence(val);
        if (state === 'yes') {
            return `<div class="feature-val is-fracture-yes"><span class="val-pill">${esc(val)}</span></div>`;
        }
        if (state === 'no') {
            return `<div class="feature-val is-fracture-no"><span class="val-pill">${esc(val)}</span></div>`;
        }
        // Inconclusive or weird — render plainly
        return `<div class="feature-val">${esc(val)}</div>`;
    }

    // CONFIDENCE row
    if (k.includes('CONFIDENCE')) {
        const parsed = parseConfidence(val);
        if (parsed) {
            let tier = 'conf-mid';
            if (parsed.num >= 85) tier = 'conf-high';
            else if (parsed.num < 70) tier = 'conf-low';
            const rest = parsed.rest ? `<span class="conf-note">${esc(parsed.rest)}</span>` : '';
            return `
                <div class="feature-val is-confidence ${tier}">
                    <span class="conf-num">${parsed.num}%</span>
                    ${rest}
                </div>`;
        }
        return `<div class="feature-val">${esc(val)}</div>`;
    }

    // Default
    return `<div class="feature-val">${esc(val)}</div>`;
}

function renderFeatures(features) {
    const lines = Object.entries(features).map(([k, v]) => `
        <div class="feature-line">
            <span class="feature-key">${esc(k)}</span>
            ${renderFeatureValue(k, v)}
        </div>`).join('');
    featuresOutput.innerHTML = lines;
}

// ─── PHASE 2: CLINICAL REASONING ─────────────────────────
reasonBtn.addEventListener('click', async () => {
    if (!currentFeaturesRaw) return;

    reportOutput.innerHTML = `<span class="placeholder-text">Generating clinical report...</span>`;
    reportTag.textContent  = 'PROCESSING';
    reportTag.style.color  = 'var(--accent)';
    reportTag.style.borderColor = 'rgba(41,151,255,0.3)';
    reportTag.style.background  = 'var(--accent-dim)';
    reasonBtn.innerHTML = `
        <span class="btn-num">02</span>
        <span class="btn-label"><span class="loader"></span> Reasoning...</span>`;
    reasonBtn.disabled = true;

    try {
        const res = await fetch('/reason', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                features_raw:     currentFeaturesRaw,
                reasoning_engine: document.getElementById('modelChoice').value
            })
        });
        const data = await res.json();

        if (!res.ok || data.error) throw new Error(data.error || 'Request failed');

        renderReport(data.report);

        markComplete(reportTag);
        reasonBtn.innerHTML = `
            <span class="btn-num">02</span>
            <span class="btn-label">
                <i data-lucide="check-circle-2"></i> Report Ready
            </span>`;
        lucide.createIcons();

    } catch (err) {
        console.error(err);
        reportOutput.innerHTML =
            `<span style="color:#FF453A;">⚠ ${esc(err.message || 'Backend connection failed.')}</span>`;
        markError(reportTag);
        reasonBtn.innerHTML = `
            <span class="btn-num">02</span>
            <span class="btn-label">
                <i data-lucide="brain-circuit"></i> Retry Reasoning
            </span>
            <i data-lucide="arrow-right" class="btn-arrow"></i>`;
        lucide.createIcons();
        reasonBtn.disabled = false;
    }
});

// Convert a body string into an HTML block of bullets + paragraphs.
// Lines starting with "- " become bullets. Other lines become paragraphs.
function renderBody(body) {
    if (!body || body === '—') {
        return `<div class="report-paragraph" style="color:var(--text-3);">—</div>`;
    }

    const lines = body.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    const parts = [];

    for (const line of lines) {
        const bulletMatch = line.match(/^(?:[-•▸]|\d+[\.\)])\s+(.*)$/);
        if (bulletMatch) {
            parts.push(`<div class="report-bullet"><span>${esc(bulletMatch[1])}</span></div>`);
        } else {
            parts.push(`<div class="report-paragraph">${esc(line)}</div>`);
        }
    }

    return parts.join('');
}

function renderReport(sections) {
    const html = Object.entries(sections).map(([heading, body]) => `
        <div class="report-section">
            <div class="report-heading">${esc(heading)}</div>
            <div class="report-body">${renderBody(body)}</div>
        </div>`).join('');
    reportOutput.innerHTML = html;
}