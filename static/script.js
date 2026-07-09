// Team Talk frontend — sends Chris's message once, renders every AI's
// response with its own color/name, and manages sessions, modes, and
// the AI roster.

const sessionSelect = document.getElementById('session-select');
const exportBtn = document.getElementById('export-btn');
const deleteBtn = document.getElementById('delete-btn');
const historyDiv = document.getElementById('history');
const chrisInput = document.getElementById('chris-input');
const sendBtn = document.getElementById('send-btn');
const legendDiv = document.getElementById('legend');
const modeSelect = document.getElementById('mode-select');
const turnSelect = document.getElementById('turn-select');

let currentSessionId = null;
let participantsCache = [];  // [{id, name, color, ...}] from /api/settings

// --- Session management -------------------------------------------------

async function refreshSessions() {
    const res = await fetch('/api/sessions');
    const data = await res.json();

    sessionSelect.innerHTML = '<option value="new">New Session</option>';
    for (const s of data.sessions) {
        const opt = document.createElement('option');
        opt.value = s.id;
        const preview = s.last_message ? ` — ${s.last_message.slice(0, 40)}` : '';
        opt.textContent = `${s.id} (${s.rounds} rounds)${preview}`;
        sessionSelect.appendChild(opt);
    }
    sessionSelect.value = currentSessionId || 'new';
}

async function loadSession(sessionId) {
    const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (!res.ok) {
        alert('Could not load session.');
        return;
    }
    const data = await res.json();
    currentSessionId = data.session_id;

    historyDiv.innerHTML = '';
    for (const round of data.rounds) {
        appendRoundToHistory(round);
    }
    historyDiv.scrollTop = historyDiv.scrollHeight;
}

function startNewSession() {
    currentSessionId = null;
    historyDiv.innerHTML = '<p class="empty-hint">Start a new conversation below — every AI answers.</p>';
}

sessionSelect.addEventListener('change', () => {
    if (sessionSelect.value === 'new') {
        startNewSession();
    } else {
        loadSession(sessionSelect.value);
    }
});

async function downloadExport(format, ext) {
    if (!currentSessionId) {
        alert('No active session to export yet.');
        return;
    }
    const res = await fetch(
        `/api/sessions/${encodeURIComponent(currentSessionId)}/export?format=${format}`,
        { method: 'POST' });
    if (!res.ok) {
        alert('Export failed.');
        return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${currentSessionId}.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
}

exportBtn.addEventListener('click', () => downloadExport('markdown', 'md'));
document.getElementById('share-btn').addEventListener('click', () => downloadExport('html', 'html'));

deleteBtn.addEventListener('click', async () => {
    if (!currentSessionId) {
        alert('No active session to delete.');
        return;
    }
    if (!confirm(`Delete session "${currentSessionId}"? This cannot be undone.`)) return;
    const res = await fetch(`/api/sessions/${encodeURIComponent(currentSessionId)}`, { method: 'DELETE' });
    if (!res.ok) {
        alert('Delete failed.');
        return;
    }
    startNewSession();
    await refreshSessions();
});

// --- Mode / turn selectors (remembered on this device) --------------------

const awardsToggle = document.getElementById('awards-toggle');
modeSelect.value = localStorage.getItem('teamtalk-mode') || 'collab';
turnSelect.value = localStorage.getItem('teamtalk-turns') || 'parallel';
awardsToggle.checked = localStorage.getItem('teamtalk-awards') !== 'off';
modeSelect.addEventListener('change', () => localStorage.setItem('teamtalk-mode', modeSelect.value));
turnSelect.addEventListener('change', () => localStorage.setItem('teamtalk-turns', turnSelect.value));
awardsToggle.addEventListener('change', () =>
    localStorage.setItem('teamtalk-awards', awardsToggle.checked ? 'on' : 'off'));

// --- Attachments -----------------------------------------------------------

const attachBtn = document.getElementById('attach-btn');
const attachInput = document.getElementById('attach-input');
const attachChips = document.getElementById('attach-chips');
let pendingAttachments = [];  // [{id, name, kind}]

attachBtn.addEventListener('click', () => attachInput.click());

attachInput.addEventListener('change', async () => {
    for (const file of attachInput.files) {
        const form = new FormData();
        form.append('file', file);
        try {
            const res = await fetch('/api/upload', { method: 'POST', body: form });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || `upload failed (${res.status})`);
            pendingAttachments.push(data);
        } catch (err) {
            alert(`Could not attach ${file.name}: ${err.message}`);
        }
    }
    attachInput.value = '';
    renderAttachChips();
});

function renderAttachChips() {
    attachChips.innerHTML = '';
    for (const att of pendingAttachments) {
        const chip = document.createElement('span');
        chip.className = 'chip';
        chip.textContent = `${att.kind === 'image' ? '🖼' : '📄'} ${att.name} `;
        const x = document.createElement('button');
        x.className = 'chip-x';
        x.textContent = '×';
        x.title = 'Remove attachment';
        x.addEventListener('click', () => {
            pendingAttachments = pendingAttachments.filter((a) => a.id !== att.id);
            renderAttachChips();
        });
        chip.appendChild(x);
        attachChips.appendChild(chip);
    }
    attachChips.style.display = pendingAttachments.length ? 'flex' : 'none';
}

// --- Chat ----------------------------------------------------------------

sendBtn.addEventListener('click', sendMessage);
chrisInput.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'Enter') sendMessage();
});

async function sendMessage() {
    const message = chrisInput.value.trim();
    if (!message) return;

    sendBtn.disabled = true;
    const sentAttachments = pendingAttachments;

    // Show the round immediately: your bubble + typing bubbles for every AI
    const pending = buildRound(
        { round: null, chris_message: message, attachments: sentAttachments }, true);
    removeEmptyHint();
    historyDiv.appendChild(pending);
    historyDiv.scrollTop = historyDiv.scrollHeight;

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                session_id: currentSessionId,
                mode: modeSelect.value,
                turn_style: turnSelect.value,
                awards: awardsToggle.checked,
                attachments: sentAttachments.map((a) => a.id),
            }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `Request failed (${res.status})`);
        }
        const data = await res.json();

        const isNewSession = !currentSessionId;
        currentSessionId = data.session_id;
        chrisInput.value = '';
        pendingAttachments = [];
        renderAttachChips();

        pending.replaceWith(buildRound(data));
        historyDiv.scrollTop = historyDiv.scrollHeight;

        if (isNewSession) await refreshSessions();
    } catch (err) {
        pending.replaceWith(buildRound({
            round: null,
            chris_message: message,
            responses: participantsCache.map((p) => ({
                id: p.id, name: p.name, color: p.color,
                text: `Error: ${err.message}`, tokens: 0,
            })),
        }));
    } finally {
        sendBtn.disabled = false;
        chrisInput.focus();
    }
}

// --- Rendering -----------------------------------------------------------

const MODE_LABELS = {
    debate: '⚔️ debate',
    ai_only: '🤖 AIs only',
    devils_advocate: "😈 devil's advocate",
    steelman: '🛡️ steelman',
    questions: '❓ questions',
    proof: '📋 proof',
    brainstorm: '💡 brainstorm',
    shoot_the_shit: '🍺 shooting the shit',
    consensus: '🤝 consensus',
    roast: '😂 roast',
    after_hours: '🍻 after hours',
    battle_royale: '🥊 battle royale',
    method_acting: '🎭 method acting',
    movie_cast: '🎬 movie cast',
    mystery: '🕵️ mystery',
    courtroom: '⚖️ courtroom',
    late_night: '🎙️ late night',
    concrete: '🔨 concrete',
};

function removeEmptyHint() {
    const hint = historyDiv.querySelector('.empty-hint');
    if (hint) hint.remove();
}

function appendRoundToHistory(round) {
    removeEmptyHint();
    historyDiv.appendChild(buildRound(round));
}

function normalizeRound(round) {
    // Resilience against an old backend: map the legacy two-AI shape
    if (!round.responses && round.claude_response !== undefined) {
        return {
            ...round,
            responses: [
                { id: 'claude', name: 'Claude', color: '#d97757', text: round.claude_response, tokens: round.claude_tokens },
                { id: 'chatgpt', name: 'ChatGPT', color: '#4bb388', text: round.chatgpt_response, tokens: round.chatgpt_tokens },
            ],
        };
    }
    return round;
}

function buildRound(round, pending = false) {
    round = normalizeRound(round);
    const roundEl = document.createElement('div');
    roundEl.className = 'round';

    if (round.round) {
        const marker = document.createElement('div');
        marker.className = 'round-marker';
        let label = `Round ${round.round}`;
        if (MODE_LABELS[round.mode]) label += ` · ${MODE_LABELS[round.mode]}`;
        if (round.turn_style === 'sequential') label += ' · 🔁';
        if ((round.responses || []).length > 2) label += ` · ${round.responses.length} AIs`;
        marker.textContent = label;
        roundEl.appendChild(marker);
    }

    // Chris — full-width gold block at the top of the round
    const chrisRow = document.createElement('div');
    chrisRow.className = 'chris-row';
    const chrisBubble = document.createElement('div');
    chrisBubble.className = 'bubble chris-bubble';
    chrisBubble.appendChild(speakerEl('#e8b04b', 'Chris (you)'));
    const chrisText = document.createElement('div');
    chrisText.className = 'bubble-text';
    chrisText.textContent = round.chris_message;
    chrisBubble.appendChild(chrisText);

    // Attached pictures/files shown inside Chris's bubble
    for (const att of round.attachments || []) {
        if (att.kind === 'image') {
            const img = document.createElement('img');
            img.className = 'chat-img';
            img.src = `/api/uploads/${att.id}`;
            img.alt = att.name;
            img.loading = 'lazy';
            chrisBubble.appendChild(img);
        } else {
            const fileChip = document.createElement('a');
            fileChip.className = 'chip file-chip';
            fileChip.textContent = `📄 ${att.name}`;
            fileChip.href = `/api/uploads/${att.id}`;
            fileChip.target = '_blank';
            chrisBubble.appendChild(fileChip);
        }
    }

    chrisRow.appendChild(chrisBubble);
    roundEl.appendChild(chrisRow);

    // AI replies — stacked full-width blocks, each clearly named and colored.
    // Stacking (not columns) scales to any number of AIs.
    const stack = document.createElement('div');
    stack.className = 'ai-stack';
    if (pending) {
        const roster = participantsCache.length
            ? participantsCache
            : [{ id: 'ai1', name: 'AI', color: '#93a0b8' }];
        for (const p of roster) stack.appendChild(typingBubble(p));
    } else {
        const names = (round.responses || []).map((r) => r.name);
        for (const resp of round.responses || []) {
            stack.appendChild(aiBubble(resp, names));
        }
    }
    roundEl.appendChild(stack);

    return roundEl;
}

// Who is this reply engaging with? Look for other participants' names
// early in the message and show a small "➤ to ..." thread hint.
function replyTargets(resp, allNames) {
    const head = (resp.text || '').slice(0, 250);
    const targets = [];
    for (const name of allNames) {
        if (name !== resp.name && head.includes(name)) targets.push(name);
    }
    if (/\bChris\b/.test(head)) targets.push('Chris');
    return targets;
}

function speakerEl(color, name) {
    const el = document.createElement('div');
    el.className = 'speaker';
    el.style.color = color;
    const dot = document.createElement('span');
    dot.className = 'dot';
    dot.style.background = color;
    el.appendChild(dot);
    el.appendChild(document.createTextNode(name));
    return el;
}

function aiBubble(resp, allNames = []) {
    const el = document.createElement('div');
    const isError = (resp.text || '').startsWith('Error:');
    el.className = `bubble ai-bubble${isError ? ' error-bubble' : ''}`;
    const color = resp.color || '#93a0b8';
    el.style.borderColor = hexWithAlpha(color, 0.55);
    el.style.background = hexWithAlpha(color, 0.09);
    const speaker = speakerEl(color, resp.name);
    if (resp.persona) {
        const badge = document.createElement('span');
        badge.className = 'persona-badge';
        badge.textContent = `🎭 ${resp.persona}`;
        speaker.appendChild(badge);
    }
    el.appendChild(speaker);

    const targets = isError ? [] : replyTargets(resp, allNames);
    if (targets.length) {
        const thread = document.createElement('div');
        thread.className = 'reply-hint';
        thread.textContent = `➤ to ${targets.join(', ')}`;
        el.appendChild(thread);
    }

    const body = document.createElement('div');
    body.className = 'bubble-text';
    body.textContent = resp.text || '';
    el.appendChild(body);

    if (resp.tokens !== undefined && !isError) {
        const t = document.createElement('span');
        t.className = 'tokens-inline';
        let extra = '';
        if (resp.memories_saved) {
            extra = `  ·  💾 saved ${resp.memories_saved === 1 ? 'a memory' : resp.memories_saved + ' memories'}`;
        }
        t.textContent = `tokens: ${resp.tokens}${extra}`;
        el.appendChild(t);
    }
    return el;
}

function typingBubble(p) {
    const el = document.createElement('div');
    el.className = 'bubble ai-bubble';
    const color = p.color || '#93a0b8';
    el.style.borderColor = hexWithAlpha(color, 0.55);
    el.style.background = hexWithAlpha(color, 0.09);
    el.appendChild(speakerEl(color, p.name));
    const typing = document.createElement('div');
    typing.className = 'typing';
    typing.innerHTML = '<span></span><span></span><span></span>';
    el.appendChild(typing);
    return el;
}

function hexWithAlpha(hex, alpha) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

function renderLegend() {
    legendDiv.innerHTML = '';
    const chris = document.createElement('span');
    chris.className = 'legend-item';
    chris.innerHTML = '<span class="dot" style="background:#e8b04b"></span>Chris (you)';
    legendDiv.appendChild(chris);
    for (const p of participantsCache) {
        const item = document.createElement('span');
        item.className = 'legend-item';
        const dot = document.createElement('span');
        dot.className = 'dot';
        dot.style.background = p.color || '#93a0b8';
        item.appendChild(dot);
        item.appendChild(document.createTextNode(p.name));
        legendDiv.appendChild(item);
    }
}

// --- Settings ------------------------------------------------------------

const settingsBtn = document.getElementById('settings-btn');
const settingsOverlay = document.getElementById('settings-overlay');
const settingsClose = document.getElementById('settings-close');
const anthropicKeyInput = document.getElementById('set-anthropic-key');
const openaiKeyInput = document.getElementById('set-openai-key');
const anthropicKeyNote = document.getElementById('anthropic-key-note');
const openaiKeyNote = document.getElementById('openai-key-note');
const participantsList = document.getElementById('participants-list');
const addAiSelect = document.getElementById('add-ai-select');

// Presets: adding an AI = pick it, paste key, done. Cheapest model per
// provider, base URL prefilled — all editable under the card's Advanced.
const AI_PRESETS = {
    claude:   { name: 'Claude',   provider: 'anthropic', model: 'claude-haiku-4-5', base_url: '', keyHint: 'sk-ant-... (blank = shared Anthropic key)' },
    chatgpt:  { name: 'ChatGPT',  provider: 'openai',    model: 'gpt-4o-mini',      base_url: '', keyHint: 'sk-... (blank = shared OpenAI key)' },
    grok:     { name: 'Grok',     provider: 'openai',    model: 'grok-3-mini',      base_url: 'https://api.x.ai/v1', keyHint: 'xai-... (get one at console.x.ai)' },
    gemini:   { name: 'Gemini',   provider: 'openai',    model: 'gemini-2.5-flash-lite', base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/', keyHint: 'AIza... (get one at aistudio.google.com)' },
    deepseek: { name: 'DeepSeek', provider: 'openai',    model: 'deepseek-chat',    base_url: 'https://api.deepseek.com/v1', keyHint: 'sk-... (get one at platform.deepseek.com)' },
    ollama:   { name: 'Ollama',   provider: 'openai',    model: 'llama3.2',         base_url: 'http://localhost:11434/v1', key: 'ollama', keyHint: 'no key needed — prefilled' },
    custom:   { name: '',         provider: 'openai',    model: '',                 base_url: '', keyHint: 'API key' },
};
const hostInput = document.getElementById('set-host');
const portInput = document.getElementById('set-port');
const testResults = document.getElementById('test-results');
const testKeysBtn = document.getElementById('test-keys-btn');
const saveSettingsBtn = document.getElementById('save-settings-btn');
const resetSettingsBtn = document.getElementById('reset-settings-btn');
const settingsStatus = document.getElementById('settings-status');

function keyNoteText(masked, source) {
    if (!masked) return 'not set';
    const from = source === 'settings' ? 'saved in Settings' : 'from .env / environment';
    return `saved: ${masked} (${from}) — leave blank to keep`;
}

function applySettingsSnapshot(data) {
    // Keys are never shown in full — inputs stay blank, notes show masked values
    anthropicKeyInput.value = '';
    openaiKeyInput.value = '';
    anthropicKeyInput.placeholder = data.anthropic_api_key_masked || 'sk-ant-...';
    openaiKeyInput.placeholder = data.openai_api_key_masked || 'sk-...';
    anthropicKeyNote.textContent = keyNoteText(data.anthropic_api_key_masked, data.anthropic_key_source);
    openaiKeyNote.textContent = keyNoteText(data.openai_api_key_masked, data.openai_key_source);
    hostInput.value = data.host || '';
    portInput.value = data.port || '';

    participantsCache = data.participants || [];
    renderLegend();
    renderParticipantCards();
}

function renderParticipantCards() {
    participantsList.innerHTML = '';
    for (const p of participantsCache) participantsList.appendChild(participantCard(p));
}

function participantCard(p = {}, keyHint = null) {
    const card = document.createElement('div');
    card.className = 'participant-card';
    card.dataset.pid = p.id || '';

    const head = document.createElement('div');
    head.className = 'participant-head';
    const dot = document.createElement('span');
    dot.className = 'dot';
    dot.style.background = p.color || '#93a0b8';
    head.appendChild(dot);

    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.className = 'p-name';
    nameInput.placeholder = 'Name (e.g. Grok)';
    nameInput.value = p.name || '';
    head.appendChild(nameInput);

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'p-remove danger';
    removeBtn.textContent = 'Remove';
    removeBtn.addEventListener('click', () => card.remove());
    head.appendChild(removeBtn);
    card.appendChild(head);

    // The simple part: just the key
    let hint = keyHint;
    if (!hint) {
        hint = p.api_key_masked ? `saved: ${p.api_key_masked} — leave blank to keep`
            : (p.uses_shared_key ? 'uses shared key above' : 'API key');
    }
    const keyInput = pInput('p-key', 'password', p.prefill_key || '', hint);
    card.appendChild(pField('API Key', keyInput));

    // The fun part: give it a character
    const personaInput = pInput('p-persona', 'text', p.persona || '',
        'e.g. a pirate who doesn\'t give a shit / Jack Black energy');
    card.appendChild(pField('Personality (optional)', personaInput));

    // Everything else lives behind Advanced
    const adv = document.createElement('details');
    adv.className = 'p-advanced';
    const summary = document.createElement('summary');
    summary.textContent = 'Advanced (model, provider, endpoint)';
    adv.appendChild(summary);

    const grid = document.createElement('div');
    grid.className = 'participant-grid';
    grid.appendChild(pField('Provider', providerSelect(p.provider)));
    grid.appendChild(pField('Model', pInput('p-model', 'text', p.model || '', 'e.g. gpt-4o-mini')));
    grid.appendChild(pField('Base URL', pInput('p-url', 'text', p.base_url || '', 'blank for Anthropic/OpenAI')));
    adv.appendChild(grid);
    card.appendChild(adv);

    return card;
}

function pField(labelText, input) {
    const wrap = document.createElement('label');
    wrap.className = 'p-field';
    const span = document.createElement('span');
    span.textContent = labelText;
    wrap.appendChild(span);
    wrap.appendChild(input);
    return wrap;
}

function pInput(cls, type, value, placeholder) {
    const input = document.createElement('input');
    input.className = cls;
    input.type = type;
    input.value = value;
    input.placeholder = placeholder || '';
    input.autocomplete = 'off';
    return input;
}

function providerSelect(value) {
    const sel = document.createElement('select');
    sel.className = 'p-provider';
    for (const [v, label] of [['anthropic', 'Anthropic (Claude)'], ['openai', 'OpenAI-compatible']]) {
        const opt = document.createElement('option');
        opt.value = v;
        opt.textContent = label;
        sel.appendChild(opt);
    }
    sel.value = value === 'anthropic' ? 'anthropic' : 'openai';
    return sel;
}

addAiSelect.addEventListener('change', () => {
    const preset = AI_PRESETS[addAiSelect.value];
    addAiSelect.value = '';
    if (!preset) return;
    const card = participantCard({
        name: preset.name,
        provider: preset.provider,
        model: preset.model,
        base_url: preset.base_url,
        prefill_key: preset.key || '',
    }, preset.keyHint);
    if (!preset.name) card.querySelector('.p-advanced').open = true;  // custom: show fields
    participantsList.appendChild(card);
    card.querySelector(preset.name ? '.p-key' : '.p-name').focus();
});

function collectParticipants() {
    const cards = participantsList.querySelectorAll('.participant-card');
    const roster = [];
    for (const card of cards) {
        roster.push({
            id: card.dataset.pid || null,
            name: card.querySelector('.p-name').value.trim(),
            provider: card.querySelector('.p-provider').value,
            model: card.querySelector('.p-model').value.trim(),
            api_key: card.querySelector('.p-key').value.trim() || null,
            base_url: card.querySelector('.p-url').value.trim() || null,
            persona: card.querySelector('.p-persona').value.trim() || null,
        });
    }
    return roster;
}

async function openSettings() {
    setSettingsStatus('');
    testResults.classList.add('hidden');
    try {
        const res = await fetch('/api/settings');
        applySettingsSnapshot(await res.json());
    } catch (err) {
        setSettingsStatus(`Could not load settings: ${err.message}`, 'fail');
    }
    settingsOverlay.classList.remove('hidden');
}

function closeSettings() {
    settingsOverlay.classList.add('hidden');
}

function setSettingsStatus(text, kind) {
    settingsStatus.textContent = text;
    settingsStatus.className = `settings-status${kind ? ' ' + kind : ''}`;
}

settingsBtn.addEventListener('click', openSettings);
settingsClose.addEventListener('click', closeSettings);
settingsOverlay.addEventListener('click', (e) => {
    if (e.target === settingsOverlay) closeSettings();
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !settingsOverlay.classList.contains('hidden')) closeSettings();
});

saveSettingsBtn.addEventListener('click', async () => {
    const payload = { participants: collectParticipants() };
    if (anthropicKeyInput.value.trim()) payload.anthropic_api_key = anthropicKeyInput.value.trim();
    if (openaiKeyInput.value.trim()) payload.openai_api_key = openaiKeyInput.value.trim();
    if (hostInput.value.trim()) payload.host = hostInput.value.trim();
    if (portInput.value) payload.port = parseInt(portInput.value, 10);

    saveSettingsBtn.disabled = true;
    setSettingsStatus('Saving...');
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (res.status === 404) {
            throw new Error('server is running old code — run: sudo systemctl restart team-talk');
        }
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `Save failed (${res.status})`);
        applySettingsSnapshot(data);
        setSettingsStatus(data.note ? `Settings saved. ${data.note}` : 'Settings saved.', 'ok');
    } catch (err) {
        setSettingsStatus(`Save failed: ${err.message}`, 'fail');
    } finally {
        saveSettingsBtn.disabled = false;
    }
});

testKeysBtn.addEventListener('click', async () => {
    testResults.classList.remove('hidden');
    testResults.innerHTML = '<div class="test-line pending">Testing every AI on the roster...</div>';
    testKeysBtn.disabled = true;

    try {
        const res = await fetch('/api/settings/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });
        if (res.status === 404) {
            throw new Error('server is running old code — run: sudo systemctl restart team-talk');
        }
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `request failed (${res.status})`);
        }
        const data = await res.json();
        testResults.innerHTML = '';
        for (const r of data.results) {
            const line = document.createElement('div');
            line.className = `test-line ${r.ok ? 'ok' : 'fail'}`;
            line.textContent = `${r.name}: ${r.ok ? '✓' : '✗'} ${r.detail}`;
            testResults.appendChild(line);
        }
    } catch (err) {
        testResults.innerHTML = '';
        const line = document.createElement('div');
        line.className = 'test-line fail';
        line.textContent = `✗ ${err.message}`;
        testResults.appendChild(line);
    } finally {
        testKeysBtn.disabled = false;
    }
});

resetSettingsBtn.addEventListener('click', async () => {
    if (!confirm('Delete all saved settings (keys and AI roster)? The app will fall back to .env / defaults.')) return;
    try {
        const res = await fetch('/api/settings', { method: 'DELETE' });
        const data = await res.json();
        applySettingsSnapshot(data);
        setSettingsStatus('Saved settings cleared — now using .env / default values.', 'ok');
    } catch (err) {
        setSettingsStatus(`Reset failed: ${err.message}`, 'fail');
    }
});

// --- Memory ------------------------------------------------------------------

const memoryBtn = document.getElementById('memory-btn');
const memoryOverlay = document.getElementById('memory-overlay');
const memoryClose = document.getElementById('memory-close');
const memoryList = document.getElementById('memory-list');
const memoryClearBtn = document.getElementById('memory-clear-btn');

memoryBtn.addEventListener('click', openMemory);
memoryClose.addEventListener('click', () => memoryOverlay.classList.add('hidden'));
memoryOverlay.addEventListener('click', (e) => {
    if (e.target === memoryOverlay) memoryOverlay.classList.add('hidden');
});

async function openMemory() {
    memoryOverlay.classList.remove('hidden');
    await renderMemories();
}

async function renderMemories() {
    memoryList.innerHTML = '<p class="field-note">Loading…</p>';
    try {
        const res = await fetch('/api/memory');
        const data = await res.json();
        memoryList.innerHTML = '';
        if (!data.memories.length) {
            memoryList.innerHTML = '<p class="field-note">Nothing saved yet. The AIs save memories on their own when something seems worth keeping — or tell them: "remember that ..."</p>';
            return;
        }
        for (const m of [...data.memories].reverse()) {
            const row = document.createElement('div');
            row.className = 'memory-item';
            const text = document.createElement('div');
            text.className = 'memory-text';
            text.textContent = m.text;
            const meta = document.createElement('div');
            meta.className = 'memory-meta';
            meta.textContent = `${m.by} · ${(m.created_at || '').slice(0, 10)}`;
            const del = document.createElement('button');
            del.className = 'memory-del danger';
            del.textContent = '×';
            del.title = 'Forget this';
            del.addEventListener('click', async () => {
                await fetch(`/api/memory/${encodeURIComponent(m.id)}`, { method: 'DELETE' });
                renderMemories();
            });
            row.appendChild(text);
            row.appendChild(meta);
            row.appendChild(del);
            memoryList.appendChild(row);
        }
    } catch (err) {
        memoryList.innerHTML = `<p class="field-note">Could not load memory: ${err.message}</p>`;
    }
}

memoryClearBtn.addEventListener('click', async () => {
    if (!confirm('Delete ALL long-term memories? The AIs will forget everything saved so far.')) return;
    await fetch('/api/memory', { method: 'DELETE' });
    renderMemories();
});

// --- Stale-server detection ------------------------------------------------
// Static files come fresh from disk; the API's version is loaded at process
// start. If they differ (or /api/version doesn't exist), the server process
// is older than the page — tell Chris to restart instead of failing weirdly.

async function checkServerVersion() {
    const banner = document.getElementById('stale-banner');
    try {
        const [pageRes, apiRes] = await Promise.all([
            fetch(`/static/version.txt?t=${Date.now()}`),
            fetch('/api/version'),
        ]);
        const pageVersion = pageRes.ok ? (await pageRes.text()).trim() : null;
        if (!apiRes.ok) {
            banner.classList.remove('hidden');
            return;
        }
        const apiVersion = (await apiRes.json()).version;
        if (pageVersion && apiVersion !== pageVersion) {
            banner.classList.remove('hidden');
        }
    } catch (err) {
        // network hiccup — don't nag
    }
}

// --- Init ----------------------------------------------------------------

async function init() {
    checkServerVersion();
    try {
        const res = await fetch('/api/settings');
        const data = await res.json();
        participantsCache = data.participants || [];
        renderLegend();
    } catch (err) {
        // legend just stays minimal if settings can't load
    }
    await refreshSessions();
    chrisInput.focus();
}

init();
