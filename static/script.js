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
const turnSelect = document.getElementById('turn-select');

let currentSessionId = null;
let participantsCache = [];  // [{id, name, color, ...}] from /api/settings

// --- Session management -------------------------------------------------

async function refreshSessions() {
    const res = await fetch('/api/sessions');
    const data = await res.json();

    sessionSelect.innerHTML = `<option value="new">${loungeMode ? '🛋️ New Lounge chat' : 'New Session'}</option>`;
    for (const s of data.sessions) {
        // The Living Room and the Lounge each show only their own sessions.
        if (!!s.lounge !== loungeMode) continue;
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
    const rounds = data.rounds;
    // Long sessions fold: only the last few rounds render open, the rest
    // wait behind one button — the full record is a click (or a PDF) away.
    const KEEP_OPEN = 3;
    if (rounds.length > KEEP_OPEN + 1) {
        const hiddenCount = rounds.length - KEEP_OPEN;
        const folded = document.createElement('div');
        folded.style.display = 'none';
        for (const round of rounds.slice(0, hiddenCount)) {
            folded.appendChild(buildRound(round));
        }
        const toggle = document.createElement('button');
        toggle.className = 'show-earlier-btn';
        const label = (open) =>
            `${open ? '⬆ Hide' : '⬇ Show'} the ${hiddenCount} earlier round${hiddenCount === 1 ? '' : 's'}`;
        toggle.textContent = label(false);
        toggle.addEventListener('click', () => {
            const opening = folded.style.display === 'none';
            folded.style.display = opening ? '' : 'none';
            toggle.textContent = label(opening);
            if (!opening) toggle.scrollIntoView({ block: 'center' });
        });
        historyDiv.appendChild(toggle);
        historyDiv.appendChild(folded);
        for (const round of rounds.slice(hiddenCount)) {
            historyDiv.appendChild(buildRound(round));
        }
    } else {
        for (const round of rounds) {
            appendRoundToHistory(round);
        }
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
document.getElementById('pdf-btn').addEventListener('click', () => downloadExport('pdf', 'pdf'));

// --- 📚 Archive PDF: pick sessions (or all) → one combined PDF -------------

const archivePanel = document.getElementById('archive-panel');

document.getElementById('archive-btn').addEventListener('click', async () => {
    if (!archivePanel.classList.contains('hidden')) {
        archivePanel.classList.add('hidden');
        return;
    }
    const data = await (await fetch('/api/sessions')).json();
    const list = document.getElementById('archive-list');
    list.innerHTML = data.sessions.length ? '' :
        '<p class="field-note">No sessions on record yet.</p>';
    for (const s of data.sessions) {
        const row = document.createElement('label');
        row.className = 'archive-row';
        const preview = s.last_message ? ` — ${s.last_message.slice(0, 48)}` : '';
        row.innerHTML =
            `<input type="checkbox" class="archive-pick" value="${escapeText(s.id)}" checked> ` +
            `${escapeText(s.id)} (${s.rounds} rounds)${escapeText(preview)}`;
        list.appendChild(row);
    }
    document.getElementById('archive-all').checked = true;
    archivePanel.classList.remove('hidden');
});

document.getElementById('archive-all').addEventListener('change', (e) => {
    for (const box of document.querySelectorAll('.archive-pick')) {
        box.checked = e.target.checked;
    }
});

document.getElementById('archive-close').addEventListener('click', () =>
    archivePanel.classList.add('hidden'));

document.getElementById('archive-download').addEventListener('click', async () => {
    const picked = [...document.querySelectorAll('.archive-pick:checked')].map((b) => b.value);
    if (!picked.length) { alert('Pick at least one session.'); return; }
    const all = picked.length === document.querySelectorAll('.archive-pick').length;
    const res = await fetch('/api/sessions/export_bundle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: all ? [] : picked }),
    });
    if (!res.ok) { alert('Archive export failed.'); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `team-talk-archive.pdf`;
    a.click();
    URL.revokeObjectURL(url);
    archivePanel.classList.add('hidden');
});

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
    // If we just deleted the parked Lounge session, forget its stored id so a
    // later reload doesn't try to restore a session that no longer exists.
    if (loungeSessionId === currentSessionId) {
        loungeSessionId = null;
        localStorage.removeItem('teamtalk-lounge-session');
    }
    if (savedBizSession === currentSessionId) {
        savedBizSession = null;
        localStorage.removeItem('teamtalk-biz-session');
    }
    startNewSession();
    await refreshSessions();
});

// --- Mode / turn selectors (remembered on this device) --------------------

const awardsToggle = document.getElementById('awards-toggle');
const splendorToggle = document.getElementById('splendor-toggle');
turnSelect.value = localStorage.getItem('teamtalk-turns') || 'parallel';
awardsToggle.checked = localStorage.getItem('teamtalk-awards') !== 'off';
splendorToggle.checked = localStorage.getItem('teamtalk-splendor') === 'on';
turnSelect.addEventListener('change', () => localStorage.setItem('teamtalk-turns', turnSelect.value));
awardsToggle.addEventListener('change', () =>
    localStorage.setItem('teamtalk-awards', awardsToggle.checked ? 'on' : 'off'));
splendorToggle.addEventListener('change', () =>
    localStorage.setItem('teamtalk-splendor', splendorToggle.checked ? 'on' : 'off'));

// --- 🛋️ The Lounge: a separate, off-the-record room ------------------------
// Flip in and the Living Room session is set aside; you're in the Lounge's own
// conversations, sent with lounge:true (stripped prompt, nothing remembered).
// Flip out and business resumes exactly where it was.
const loungeToggle = document.getElementById('lounge-toggle');
const loungeBanner = document.getElementById('lounge-banner');
let loungeMode = false;
let savedBizSession = null;   // the Living Room session, parked while in the Lounge
let loungeSessionId = localStorage.getItem('teamtalk-lounge-session') || null;

function paintLounge() {
    document.body.classList.toggle('lounge-active', loungeMode);
    if (loungeBanner) loungeBanner.hidden = !loungeMode;
    if (loungeToggle) loungeToggle.checked = loungeMode;
}

async function switchRoom(toLounge) {
    if (toLounge) {
        savedBizSession = currentSessionId;
        // Park the business session so a reload-then-flip-out returns here
        // instead of dropping you into a blank new session.
        localStorage.setItem('teamtalk-biz-session', savedBizSession || '');
        loungeMode = true;
        currentSessionId = loungeSessionId;
    } else {
        // remember which lounge chat was open for next time
        if (currentSessionId) {
            loungeSessionId = currentSessionId;
            localStorage.setItem('teamtalk-lounge-session', loungeSessionId);
        }
        loungeMode = false;
        currentSessionId = savedBizSession;
    }
    // Persist the room so a page reload doesn't silently drop you back into the
    // Living Room (with everything recording) while you think you're off the record.
    localStorage.setItem('teamtalk-lounge-mode', loungeMode ? 'on' : 'off');
    paintLounge();
    historyDiv.innerHTML = '';
    if (currentSessionId) {
        await loadSession(currentSessionId);
    }
    await refreshSessions();
}

if (loungeToggle) {
    loungeToggle.addEventListener('change', () => switchRoom(loungeToggle.checked));
}

// --- Voice mode: talk to the room, hear Splendor recap the crew -------------

const voiceToggle = document.getElementById('voice-toggle');
const micBtn = document.getElementById('mic-btn');
const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
voiceToggle.checked = localStorage.getItem('teamtalk-voice') === 'on';

function updateVoiceUI() {
    micBtn.classList.toggle('hidden', !(voiceToggle.checked && SpeechRec));
}

voiceToggle.addEventListener('change', () => {
    localStorage.setItem('teamtalk-voice', voiceToggle.checked ? 'on' : 'off');
    updateVoiceUI();
    if (voiceToggle.checked && !SpeechRec) {
        alert('Splendor will speak her recaps out loud — but voice INPUT needs HTTPS, ' +
              'which this address doesn\'t have.\n\nEasiest fix (you have Tailscale) — run on the server:\n' +
              'sudo tailscale serve --bg --https=443 http://localhost:5001\n\n' +
              'then open the https://…ts.net address it prints.');
    }
});
updateVoiceUI();

let recog = null;
let micActive = false;
micBtn.addEventListener('click', () => {
    if (micActive && recog) { recog.stop(); return; }
    recog = new SpeechRec();
    recog.lang = navigator.language || 'en-US';
    recog.interimResults = true;
    let finalText = '';
    recog.onresult = (e) => {
        let interim = '';
        for (const res of e.results) {
            if (res.isFinal) finalText += res[0].transcript;
            else interim += res[0].transcript;
        }
        chrisInput.value = (finalText + interim).trim();
    };
    recog.onerror = (e) => {
        micActive = false;
        micBtn.classList.remove('rec');
        if (e.error !== 'no-speech' && e.error !== 'aborted') {
            alert(`Mic error: ${e.error}`);
        }
    };
    recog.onend = () => {
        micActive = false;
        micBtn.classList.remove('rec');
        if (chrisInput.value.trim()) sendMessage();
    };
    recog.start();
    micActive = true;
    micBtn.classList.add('rec');
});

function browserSpeak(text) {
    try {
        const u = new SpeechSynthesisUtterance(text);
        u.rate = 1.05;
        speechSynthesis.speak(u);
    } catch (e) { /* no browser voice — text recap is still on screen */ }
}

async function speakRecap(round) {
    try {
        const res = await fetch('/api/voice/recap', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: currentSessionId, round: round.round }),
        });
        if (!res.ok) return;
        const data = await res.json();
        const el = document.createElement('div');
        el.className = 'splendor-recap';
        el.textContent = `🕊️ ${data.text}`;
        historyDiv.appendChild(el);
        historyDiv.scrollTop = historyDiv.scrollHeight;
        if (data.audio_b64) {
            new Audio(`data:audio/mpeg;base64,${data.audio_b64}`).play()
                .catch(() => browserSpeak(data.text));
        } else {
            browserSpeak(data.text);
        }
    } catch (e) { /* recap is a bonus — never break the round over it */ }
}

const MODE_LABELS = {
    collab: '🤝 collaborate',
    hard_truth: '💊 hard truth',
    blind: '🕶️ blind',
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
    chain_auditor: '📜 chain auditor',
    ledgers_dream: "🛌 ledger's dream",
    fridge_note: '🧲 fridge note',
    object_theater: '🪨 object theater',
    ghost_fork: '🪞 ghost fork',
};

// Modes stack: pick up to 3 at once (e.g. Hard Truth + Roast).
// "Collaborate" is the plain default and doesn't mix with the others.
const MAX_MODES = 3;
const modePicker = document.getElementById('mode-picker');
const modeSummary = document.getElementById('mode-summary');
const modeChecks = [...modePicker.querySelectorAll('input[type="checkbox"]')];

let selectedModes = [];
try { selectedModes = JSON.parse(localStorage.getItem('teamtalk-modes')) || []; } catch (e) { /* fresh start */ }
if (!selectedModes.length) {
    const old = localStorage.getItem('teamtalk-mode');  // pre-stacking preference
    if (old) selectedModes = [old];
}
selectedModes = selectedModes.filter((m) => modeChecks.some((c) => c.value === m)).slice(0, MAX_MODES);
if (!selectedModes.length) selectedModes = ['collab'];

function syncModePicker() {
    for (const c of modeChecks) c.checked = selectedModes.includes(c.value);
    modeSummary.textContent = selectedModes.map((m) => MODE_LABELS[m] || m).join(' + ');
    localStorage.setItem('teamtalk-modes', JSON.stringify(selectedModes));
}

for (const c of modeChecks) {
    c.addEventListener('change', () => {
        if (c.checked) {
            if (c.value === 'collab') {
                selectedModes = ['collab'];          // back to normal
            } else {
                selectedModes = selectedModes.filter((m) => m !== 'collab');
                if (selectedModes.length >= MAX_MODES) {
                    c.checked = false;
                    return;
                }
                selectedModes.push(c.value);
            }
        } else {
            selectedModes = selectedModes.filter((m) => m !== c.value);
        }
        if (!selectedModes.length) selectedModes = ['collab'];
        syncModePicker();
    });
}
syncModePicker();

// Tap anywhere else to close the picker
document.addEventListener('click', (e) => {
    if (modePicker.open && !modePicker.contains(e.target)) modePicker.open = false;
});

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
                mode: selectedModes[0],   // old servers read this one
                modes: selectedModes,
                turn_style: turnSelect.value,
                awards: awardsToggle.checked,
                via_splendor: splendorToggle.checked,
                room_context: deviceContext(),
                attachments: sentAttachments.map((a) => a.id),
                lounge: loungeMode,
            }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `Request failed (${res.status})`);
        }
        const data = await res.json();

        const isNewSession = !currentSessionId;
        currentSessionId = data.session_id;
        if (loungeMode) {
            loungeSessionId = data.session_id;
            localStorage.setItem('teamtalk-lounge-session', loungeSessionId);
        }
        chrisInput.value = '';
        pendingAttachments = [];
        renderAttachChips();

        pending.replaceWith(buildRound(data));
        historyDiv.scrollTop = historyDiv.scrollHeight;

        if (voiceToggle.checked && data.round) speakRecap(data);
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

function buildRound(round, pending = false, reveal = false) {
    round = normalizeRound(round);
    const roundEl = document.createElement('div');
    roundEl.className = 'round';
    const roundModes = round.modes || (round.mode ? [round.mode] : []);
    const isBlind = (round.responses || []).some((r) => r.label);

    if (round.round) {
        const marker = document.createElement('div');
        marker.className = 'round-marker';
        let label = `Round ${round.round}`;
        const modeText = roundModes
            .filter((m) => m !== 'collab' || roundModes.length === 1)
            .map((m) => MODE_LABELS[m] || m)
            .filter((m) => m !== '🤝 collaborate')
            .join(' + ');
        if (modeText) label += ` · ${modeText}`;
        if (round.turn_style === 'sequential') label += ' · 🔁';
        if ((round.responses || []).length > 2) label += ` · ${round.responses.length} AIs`;
        for (const ms of round.mode_shifts || []) {
            label += ` · 🔀 ${ms.by}→${ms.mode} ${ms.status === 'SUCCESS' ? '✓' : '✗ ' + ms.reason}`;
        }
        marker.appendChild(document.createTextNode(label));
        if (isBlind) {
            const btn = document.createElement('button');
            btn.className = 'reveal-btn';
            btn.textContent = reveal ? '🙈 hide' : '👁 reveal';
            btn.title = reveal ? 'Hide the real names again' : 'Show who each voice really was';
            btn.addEventListener('click', () => roundEl.replaceWith(buildRound(round, false, !reveal)));
            marker.appendChild(btn);
        }
        roundEl.appendChild(marker);
    }

    // Chris — full-width gold block at the top of the round
    const chrisRow = document.createElement('div');
    chrisRow.className = 'chris-row';
    const chrisBubble = document.createElement('div');
    chrisBubble.className = 'bubble chris-bubble';
    chrisBubble.appendChild(speakerEl('#e8b04b',
        round.via_splendor ? '🕊️ Splendor (for Chris)' : 'Chris (you)'));
    const chrisText = document.createElement('div');
    chrisText.className = 'bubble-text';
    chrisText.textContent = round.chris_message;
    chrisBubble.appendChild(chrisText);
    if (round.via_splendor && round.chris_raw) {
        const raw = document.createElement('div');
        raw.className = 'via-note';
        raw.textContent = `you told Splendor: "${round.chris_raw}"`;
        chrisBubble.appendChild(raw);
    }

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
        const blindNow = selectedModes.includes('blind');
        for (const p of roster) {
            stack.appendChild(typingBubble(blindNow ? { name: 'Voice ?', color: '#8a93a5' } : p));
        }
    } else {
        const names = (round.responses || []).map((r) => (reveal ? r.name : (r.label || r.name)));
        for (const resp of round.responses || []) {
            stack.appendChild(aiBubble(resp, names, reveal));
        }
    }
    roundEl.appendChild(stack);

    return roundEl;
}

// Who is this reply engaging with? Look for other participants' names
// early in the message and show a small "➤ to ..." thread hint.
function replyTargets(text, selfName, allNames) {
    const head = (text || '').slice(0, 250);
    const targets = [];
    for (const name of allNames) {
        if (name !== selfName && head.includes(name)) targets.push(name);
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

async function saveToMemory(text, btn) {
    const suggested = (text || '').trim().slice(0, 300);
    const toSave = prompt('Save to memory — trim it to the fact worth keeping:', suggested);
    if (toSave === null) return;               // cancelled
    const clean = toSave.trim();
    if (!clean) return;
    try {
        const res = await fetch('/api/memory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: clean }),
        });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `failed (${res.status})`);
        if (btn) { btn.textContent = '✓ Saved to memory'; btn.disabled = true; }
    } catch (e) {
        alert('Could not save to memory: ' + e.message);
    }
}

function aiBubble(resp, allNames = [], reveal = false) {
    const el = document.createElement('div');
    const isError = (resp.text || '').startsWith('Error:');
    el.className = `bubble ai-bubble${isError ? ' error-bubble' : ''}`;
    // Blind rounds: anonymous label + neutral color; "reveal" swaps in the
    // real name and the AI's usual color (looked up from the roster).
    const shownName = reveal ? resp.name : (resp.label || resp.name);
    let color = resp.color || '#93a0b8';
    if (resp.label && reveal) {
        const p = participantsCache.find((q) => q.id === resp.id);
        if (p && p.color) color = p.color;
    }
    el.style.borderColor = hexWithAlpha(color, 0.55);
    el.style.background = hexWithAlpha(color, 0.09);
    const speaker = speakerEl(color, shownName);
    if (resp.persona) {
        const badge = document.createElement('span');
        badge.className = 'persona-badge';
        badge.textContent = `🎭 ${resp.persona}`;
        speaker.appendChild(badge);
    }
    el.appendChild(speaker);

    const targets = isError ? [] : replyTargets(resp.text, shownName, allNames);
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

    // 💾 Pull this line into long-term memory — the manual save (the only way
    // anything from the off-the-record Lounge gets kept).
    if (!isError) {
        const saveBtn = document.createElement('button');
        saveBtn.type = 'button';
        saveBtn.className = 'save-mem-btn';
        saveBtn.title = "Save this to the room's long-term memory";
        saveBtn.textContent = '💾 Save to memory';
        saveBtn.addEventListener('click', () => saveToMemory(resp.text || '', saveBtn));
        el.appendChild(saveBtn);
    }

    if (resp.tokens !== undefined && !isError) {
        const t = document.createElement('span');
        t.className = 'tokens-inline';
        let extra = '';
        if (resp.memories_saved) {
            extra += `  ·  💾 saved ${resp.memories_saved === 1 ? 'a memory' : resp.memories_saved + ' memories'}`;
        }
        if (resp.notebook_saved) extra += '  ·  📓 wrote in the notebook';
        if (resp.pins_saved) extra += '  ·  📌 pinned a quote';
        if (resp.journal_saved) extra += '  ·  📔 wrote in their journal';
        if (resp.questions_asked) extra += '  ·  ❓ asked Chris a question';
        if (resp.mail_sent) extra += '  ·  📬 left mail';
        if (resp.about_written) extra += '  ·  🪪 updated About Me';
        if (resp.studio_pitched) extra += '  ·  🎨 pitched to the Studio';
        if (resp.studio_voted) extra += `  ·  🗳️ voted ${resp.studio_voted}`;
        if (resp.room_actions) {
            const ok = resp.room_actions.filter((a) => a.ok).length;
            const bad = resp.room_actions.length - ok;
            if (ok) extra += `  ·  🧷 ${ok} wall action${ok > 1 ? 's' : ''}`;
            if (bad) extra += `  ·  ⚠ ${bad} rejected action${bad > 1 ? 's' : ''}`;
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
        item.appendChild(document.createTextNode(p.resting ? `${p.name} 😴` : p.name));
        if (p.resting) item.style.opacity = '0.45';
        legendDiv.appendChild(item);
    }
    // The silent sixth chair: present, watching, never speaking
    const dir = document.createElement('span');
    dir.className = 'legend-item director-chair';
    dir.title = 'The Director watches in silence — hit Director\'s Cut to wrap the session into shorts';
    dir.innerHTML = '<span class="dot" style="background:#5a6478"></span>🎬 Director (watching)';
    legendDiv.appendChild(dir);
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
    gemini:   { name: 'Gemini',   provider: 'openai',    model: 'gemini-3.1-flash-lite', base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/', keyHint: 'AIza... (get one at aistudio.google.com)' },
    deepseek: { name: 'DeepSeek', provider: 'openai',    model: 'deepseek-chat',    base_url: 'https://api.deepseek.com/v1', keyHint: 'sk-... (get one at platform.deepseek.com)' },
    muse:     { name: 'Muse',     provider: 'openai',    model: 'muse-spark-1.1',   base_url: 'https://api.meta.ai/v1', keyHint: 'get one at dev.meta.ai (Meta Model API)' },
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
    roomLocation = data.location || '';
    const locInput = document.getElementById('set-location');
    if (locInput) locInput.value = roomLocation;

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

    const restLabel = document.createElement('label');
    restLabel.className = 'p-rest-label';
    restLabel.title = 'Seat stays configured (key kept) but is not called — for empty credits or broken provider consoles';
    const restBox = document.createElement('input');
    restBox.type = 'checkbox';
    restBox.className = 'p-resting';
    restBox.checked = !!p.resting;
    restLabel.appendChild(restBox);
    restLabel.appendChild(document.createTextNode(' 😴'));
    head.appendChild(restLabel);

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
    grid.appendChild(pField('Max reply tokens (cost cap)', pInput('p-maxtok', 'number', p.max_tokens || '', 'blank = default · reasoning models: 3000+ or replies may come back empty')));
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
            resting: card.querySelector('.p-resting') ? card.querySelector('.p-resting').checked : false,
            max_tokens: parseInt(card.querySelector('.p-maxtok') && card.querySelector('.p-maxtok').value, 10) || null,
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
    payload.location = document.getElementById('set-location').value.trim();

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
            if (m.tombstone) {
                const grave = document.createElement('div');
                grave.className = 'memory-item tombstone';
                grave.innerHTML = `<div class="memory-text">⚰ Removed ${(m.removed_at || '').slice(0, 10)} — ${escapeText(m.reason)}</div>`
                    + `<div class="memory-meta">was by ${escapeText(m.original_by || '?')} · removed by ${escapeText(m.authority || '?')}</div>`;
                memoryList.appendChild(grave);
                continue;
            }
            const row = document.createElement('div');
            row.className = 'memory-item';
            const text = document.createElement('div');
            text.className = 'memory-text';
            text.textContent = m.text;
            const meta = document.createElement('div');
            meta.className = 'memory-meta';
            const tag = m.kind === 'chris_stated' ? ' · stated' : ' · observed';
            meta.textContent = `${m.by} · ${(m.created_at || '').slice(0, 10)}${tag}`;
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

const memoryInput = document.getElementById('memory-input');
const memoryAddBtn = document.getElementById('memory-add-btn');
memoryAddBtn.addEventListener('click', async () => {
    const text = memoryInput.value.trim();
    if (!text) return;
    await fetch('/api/memory', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
    });
    memoryInput.value = '';
    renderMemories();
});
memoryInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') memoryAddBtn.click();
});

// --- The Notebook (shared scratchpad + pinned quotes) ------------------------

const notebookBtn = document.getElementById('notebook-btn');
const notebookOverlay = document.getElementById('notebook-overlay');
const notebookClose = document.getElementById('notebook-close');
const pinList = document.getElementById('pin-list');
const notebookList = document.getElementById('notebook-list');
const notebookInput = document.getElementById('notebook-input');
const notebookAddBtn = document.getElementById('notebook-add-btn');
const notebookClearBtn = document.getElementById('notebook-clear-btn');

notebookBtn.addEventListener('click', async () => {
    notebookOverlay.classList.remove('hidden');
    await renderNotebook();
});
notebookClose.addEventListener('click', () => notebookOverlay.classList.add('hidden'));
notebookOverlay.addEventListener('click', (e) => {
    if (e.target === notebookOverlay) notebookOverlay.classList.add('hidden');
});

function notebookRow(item, kind) {
    if (item.tombstone) {
        const grave = document.createElement('div');
        grave.className = 'memory-item tombstone';
        grave.innerHTML = `<div class="memory-text">⚰ Removed ${(item.removed_at || '').slice(0, 10)} — ${escapeText(item.reason)}</div>`
            + `<div class="memory-meta">was by ${escapeText(item.original_by || '?')} · removed by ${escapeText(item.authority || '?')}</div>`;
        return grave;
    }
    const row = document.createElement('div');
    row.className = 'memory-item';
    const text = document.createElement('div');
    text.className = 'memory-text';
    text.textContent = kind === 'pins' ? `“${item.text}”` : item.text;
    const meta = document.createElement('div');
    meta.className = 'memory-meta';
    meta.textContent = `${item.by} · ${(item.created_at || '').slice(0, 10)}`;
    const del = document.createElement('button');
    del.className = 'memory-del danger';
    del.textContent = '×';
    del.title = 'Delete';
    del.addEventListener('click', async () => {
        await fetch(`/api/notebook/${kind}/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
        renderNotebook();
    });
    row.appendChild(text);
    row.appendChild(meta);
    row.appendChild(del);
    return row;
}

async function renderNotebook() {
    pinList.innerHTML = '<p class="field-note">Loading…</p>';
    notebookList.innerHTML = '';
    try {
        const res = await fetch('/api/notebook');
        if (res.status === 404) {
            throw new Error('server is running old code — run: sudo /opt/team-talk/update.sh');
        }
        const data = await res.json();
        pinList.innerHTML = '';
        notebookList.innerHTML = '';
        if (!data.pins.length) {
            pinList.innerHTML = '<p class="field-note">No pinned quotes yet — any AI can pin an exact line with PIN:</p>';
        }
        for (const p of [...data.pins].reverse()) pinList.appendChild(notebookRow(p, 'pins'));
        if (!data.entries.length) {
            notebookList.innerHTML = '<p class="field-note">Empty so far. The AIs write here on their own with NOTEBOOK: lines — raw thoughts in their own words, kept across sessions.</p>';
        }
        for (const e of [...data.entries].reverse()) notebookList.appendChild(notebookRow(e, 'entries'));
    } catch (err) {
        pinList.innerHTML = `<p class="field-note">Could not load the notebook: ${err.message}</p>`;
    }
}

notebookAddBtn.addEventListener('click', async () => {
    const text = notebookInput.value.trim();
    if (!text) return;
    await fetch('/api/notebook', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
    });
    notebookInput.value = '';
    renderNotebook();
});
notebookInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') notebookAddBtn.click();
});

notebookClearBtn.addEventListener('click', async () => {
    if (!confirm('Delete the ENTIRE notebook — every entry and every pinned quote?')) return;
    await fetch('/api/notebook', { method: 'DELETE' });
    renderNotebook();
});

// --- 🎬 Director's Cut --------------------------------------------------------

const directorsBtn = document.getElementById('directors-btn');
const directorsOverlay = document.getElementById('directors-overlay');
const directorsClose = document.getElementById('directors-close');
const directorsBody = document.getElementById('directors-body');
const CATEGORY_LABELS = {
    best_overall: '🏆 Best Overall',
    funniest: '😂 Funniest',
    breakthrough: '💡 Biggest Breakthrough',
    best_roast: '🔥 Best Roast',
    most_human: '❤️ Most Human Moment',
};

directorsBtn.addEventListener('click', async () => {
    if (!currentSessionId) {
        alert('Start a conversation first — the Director needs footage.');
        return;
    }
    directorsOverlay.classList.remove('hidden');
    directorsBody.innerHTML = '<p class="field-note">Checking the archive…</p>';
    try {
        const res = await fetch(`/api/sessions/${encodeURIComponent(currentSessionId)}/directors_cut`);
        const cut = await res.json();
        renderDirectorsCut(cut);
    } catch (err) {
        directorsBody.innerHTML = `<p class="field-note">Could not load: ${err.message}</p>`;
    }
});
directorsClose.addEventListener('click', () => directorsOverlay.classList.add('hidden'));
directorsOverlay.addEventListener('click', (e) => {
    if (e.target === directorsOverlay) directorsOverlay.classList.add('hidden');
});

function wrapButton(label) {
    const btn = document.createElement('button');
    btn.className = 'primary wrap-btn';
    btn.textContent = label;
    btn.addEventListener('click', async () => {
        directorsBody.innerHTML =
            '<p class="field-note director-working">🎬 The Director is reviewing the footage with Splendor… ' +
            'marking moments, cutting clips. This takes a minute.</p>';
        try {
            const res = await fetch(
                `/api/sessions/${encodeURIComponent(currentSessionId)}/directors_cut`,
                { method: 'POST' });
            const cut = await res.json();
            if (!res.ok) throw new Error(cut.detail || `wrap failed (${res.status})`);
            renderDirectorsCut(cut);
        } catch (err) {
            directorsBody.innerHTML = `<p class="field-note">The wrap failed: ${err.message}</p>`;
            directorsBody.appendChild(wrapButton('🎬 Try the Wrap again'));
        }
    });
    return btn;
}

function renderDirectorsCut(cut) {
    directorsBody.innerHTML = '';
    if (!cut || !(cut.clips || []).length) {
        const p = document.createElement('p');
        p.className = 'field-note';
        p.textContent = (cut && (cut.moments || []).length)
            ? 'The Director marked moments but no clips were cut yet.'
            : 'No cut exists for this session yet.';
        directorsBody.appendChild(p);
        directorsBody.appendChild(wrapButton('🎬 Wrap — cut this session into shorts'));
        return;
    }
    const meta = document.createElement('p');
    meta.className = 'field-note';
    meta.textContent = `Cut ${(cut.created_at || '').slice(0, 10)} · ${cut.rounds_reviewed || '?'} rounds reviewed · ${cut.moments.length} moments marked · ${cut.clips.length} clips`;
    directorsBody.appendChild(meta);
    for (const clip of cut.clips) directorsBody.appendChild(clipCard(clip));
    directorsBody.appendChild(wrapButton('🎬 Re-cut the session'));
}

function clipCard(clip) {
    const card = document.createElement('div');
    card.className = 'clip-card';

    const cat = document.createElement('div');
    cat.className = 'clip-cat';
    cat.textContent = CATEGORY_LABELS[clip.category] || clip.category;
    card.appendChild(cat);

    const title = document.createElement('h3');
    title.className = 'clip-title';
    title.textContent = clip.title;
    card.appendChild(title);

    const meta = document.createElement('div');
    meta.className = 'clip-meta';
    meta.textContent = `~${clip.duration_sec}s · score ${clip.score} · rounds ${clip.start_round}–${clip.end_round}`;
    card.appendChild(meta);

    const quote = document.createElement('blockquote');
    quote.className = 'clip-quote';
    quote.textContent = `“${clip.quote}”`;
    card.appendChild(quote);

    const why = document.createElement('p');
    why.className = 'clip-why';
    why.innerHTML = `<strong>🎬 Director:</strong> ${escapeText(clip.why_director)}`;
    card.appendChild(why);

    if (clip.splendor_take) {
        const take = document.createElement('p');
        take.className = 'clip-why';
        take.innerHTML = `<strong>🕊️ Splendor:</strong> ${escapeText(clip.splendor_take)}`;
        card.appendChild(take);
    }

    const actions = document.createElement('div');
    actions.className = 'clip-actions';

    const preview = document.createElement('button');
    preview.textContent = '▶ Preview';
    preview.addEventListener('click', () => playClip(clip));
    actions.appendChild(preview);

    const exportBtn = document.createElement('button');
    exportBtn.textContent = 'Export Script';
    exportBtn.title = 'Download the clip as JSON — the input for video rendering';
    exportBtn.addEventListener('click', () => {
        const blob = new Blob([JSON.stringify(clip, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${(clip.title || 'clip').replace(/[^\w]+/g, '-').slice(0, 50)}.json`;
        a.click();
        URL.revokeObjectURL(url);
    });
    actions.appendChild(exportBtn);

    const copyBtn = document.createElement('button');
    copyBtn.textContent = 'Copy Caption';
    copyBtn.addEventListener('click', async () => {
        const text = `${clip.caption}\n\n${(clip.hashtags || []).map((h) => '#' + h).join(' ')}`;
        try {
            await navigator.clipboard.writeText(text);
            copyBtn.textContent = '✓ Copied';
        } catch (e) {
            prompt('Copy the caption:', text);
        }
        setTimeout(() => { copyBtn.textContent = 'Copy Caption'; }, 1500);
    });
    actions.appendChild(copyBtn);

    card.appendChild(actions);
    return card;
}

function escapeText(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}

// --- Vertical preview player (9:16, caption-first, story-style) --------------

const clipPlayer = document.getElementById('clip-player');
const clipStage = document.getElementById('clip-stage');
const clipProgress = document.getElementById('clip-progress');
const clipCloseBtn = document.getElementById('clip-close');
let clipTimer = null;
let clipFrames = [];
let clipIndex = 0;

function speakerColor(name) {
    if (name === 'Chris') return '#e8b04b';
    if (name === 'Director') return '#8a93a5';
    if (name === 'Splendor') return '#e8d9b0';
    const p = participantsCache.find((q) => q.name === name);
    return (p && p.color) || '#93a0b8';
}

function clipToFrames(clip) {
    const frames = [];
    frames.push({ kind: 'hook', text: clip.hook || clip.title });
    for (const ex of clip.excerpts || []) {
        frames.push({ kind: 'excerpt', speaker: ex.speaker, text: ex.text });
    }
    for (const d of clip.dialogue || []) {
        frames.push({ kind: 'commentary', speaker: d.speaker, text: d.line });
    }
    frames.push({ kind: 'end', text: clip.end_line || 'What would you have said?' });
    return frames;
}

function frameDuration(f) {
    return Math.max(2000, Math.min(5000, 1400 + (f.text || '').length * 45));
}

function playClip(clip) {
    clipFrames = clipToFrames(clip);
    clipIndex = 0;
    clipPlayer.classList.remove('hidden');
    clipProgress.innerHTML = '';
    for (let i = 0; i < clipFrames.length; i++) {
        const seg = document.createElement('span');
        clipProgress.appendChild(seg);
    }
    showFrame();
}

function showFrame() {
    clearTimeout(clipTimer);
    if (clipIndex < 0) clipIndex = 0;
    if (clipIndex >= clipFrames.length) { closeClip(); return; }
    const f = clipFrames[clipIndex];
    [...clipProgress.children].forEach((seg, i) => {
        seg.className = i < clipIndex ? 'done' : (i === clipIndex ? 'active' : '');
    });
    clipStage.innerHTML = '';
    const inner = document.createElement('div');
    inner.className = `clip-frame clip-${f.kind}`;
    if (f.speaker) {
        const sp = document.createElement('div');
        sp.className = 'clip-speaker';
        sp.style.color = speakerColor(f.speaker);
        sp.textContent = f.kind === 'commentary'
            ? (f.speaker === 'Director' ? '🎬 DIRECTOR' : '🕊️ SPLENDOR')
            : f.speaker.toUpperCase();
        inner.appendChild(sp);
    }
    const tx = document.createElement('div');
    tx.className = 'clip-text';
    tx.textContent = f.text;
    inner.appendChild(tx);
    if (f.kind === 'end') {
        const brand = document.createElement('div');
        brand.className = 'clip-brand';
        brand.textContent = 'made with Team Talk 🍻';
        inner.appendChild(brand);
    }
    clipStage.appendChild(inner);
    clipTimer = setTimeout(() => { clipIndex++; showFrame(); }, frameDuration(f));
}

function closeClip() {
    clearTimeout(clipTimer);
    clipPlayer.classList.add('hidden');
}

clipCloseBtn.addEventListener('click', closeClip);
clipStage.addEventListener('click', (e) => {
    const rect = clipStage.getBoundingClientRect();
    if (e.clientX - rect.left < rect.width / 3) clipIndex = Math.max(0, clipIndex - 1);
    else clipIndex++;
    showFrame();
});

// --- 🧾 The Truth Layer -------------------------------------------------------

const truthBtn = document.getElementById('truth-btn');
const truthOverlay = document.getElementById('truth-overlay');
const truthClose = document.getElementById('truth-close');
const questionsList = document.getElementById('questions-list');
const verifyList = document.getElementById('verify-list');
const ledgerChain = document.getElementById('ledger-chain');
const ledgerList = document.getElementById('ledger-list');

truthBtn.addEventListener('click', async () => {
    truthOverlay.classList.remove('hidden');
    renderQuestions();
    renderVerification();
    renderLedger();
});
truthClose.addEventListener('click', () => truthOverlay.classList.add('hidden'));
truthOverlay.addEventListener('click', (e) => {
    if (e.target === truthOverlay) truthOverlay.classList.add('hidden');
});

async function renderQuestions() {
    questionsList.innerHTML = '<p class="field-note">Loading…</p>';
    try {
        const res = await fetch('/api/questions');
        const data = await res.json();
        questionsList.innerHTML = '';
        const open = data.questions.filter((q) => q.status === 'open');
        const answered = data.questions.filter((q) => q.status === 'answered').slice(-5);
        if (!open.length && !answered.length) {
            questionsList.innerHTML = '<p class="field-note">No questions yet. Any AI can ask with a "QUESTION FOR CHRIS:" line — it waits here until you answer. No expiration.</p>';
            return;
        }
        for (const q of open) {
            const row = document.createElement('div');
            row.className = 'question-item';
            const head = document.createElement('div');
            head.className = 'memory-meta';
            head.textContent = `OPEN · ${q.asker} · ${(q.ts || '').slice(0, 10)}`;
            const text = document.createElement('div');
            text.className = 'memory-text';
            text.textContent = q.question;
            const answerWrap = document.createElement('div');
            answerWrap.className = 'nb-add';
            const input = document.createElement('input');
            input.type = 'text';
            input.maxLength = 1000;
            input.placeholder = 'Your answer — goes back to the whole room…';
            const btn = document.createElement('button');
            btn.textContent = 'Answer';
            btn.addEventListener('click', async () => {
                const answer = input.value.trim();
                if (!answer) return;
                await fetch(`/api/questions/${encodeURIComponent(q.id)}/answer`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ answer }),
                });
                renderQuestions();
            });
            input.addEventListener('keydown', (e) => { if (e.key === 'Enter') btn.click(); });
            answerWrap.appendChild(input);
            answerWrap.appendChild(btn);
            row.appendChild(head);
            row.appendChild(text);
            row.appendChild(answerWrap);
            questionsList.appendChild(row);
        }
        for (const q of answered.reverse()) {
            const row = document.createElement('div');
            row.className = 'question-item answered';
            row.innerHTML = `<div class="memory-meta">ANSWERED · ${escapeText(q.asker)} · ${(q.answered_at || '').slice(0, 10)}</div>`
                + `<div class="memory-text">${escapeText(q.question)}</div>`
                + `<div class="memory-text q-answer">↳ ${escapeText(q.answer)}</div>`;
            questionsList.appendChild(row);
        }
    } catch (err) {
        questionsList.innerHTML = `<p class="field-note">Could not load questions: ${err.message}</p>`;
    }
}

async function renderVerification() {
    verifyList.innerHTML = '<p class="field-note">Recomputing chains…</p>';
    try {
        const res = await fetch('/api/verify');
        const data = await res.json();
        verifyList.innerHTML = '';
        for (const p of data.participants) {
            const row = document.createElement('div');
            row.className = 'verify-item';
            const status = p.entries === 0
                ? '· empty'
                : (p.valid ? '✓ hash chain valid' : `✗ CHAIN BROKEN (${p.reason})`);
            const head = document.createElement('div');
            head.className = 'memory-text';
            head.innerHTML = `<strong>${escapeText(p.participant)}</strong> — ${p.entries} entries <span class="${p.valid ? 'v-ok' : 'v-bad'}">${status}</span>`;
            row.appendChild(head);
            if (p.latest_hash) {
                const hash = document.createElement('div');
                hash.className = 'hash-line';
                hash.textContent = `latest ${p.latest_hash}`;
                row.appendChild(hash);
            }
            const actions = document.createElement('div');
            actions.className = 'clip-actions';
            const bundle = document.createElement('a');
            bundle.href = `/api/verify/${encodeURIComponent(p.participant)}/bundle`;
            bundle.textContent = 'Export Verification Bundle';
            bundle.className = 'bundle-link';
            bundle.setAttribute('download', '');
            actions.appendChild(bundle);
            row.appendChild(actions);
            verifyList.appendChild(row);
        }
    } catch (err) {
        verifyList.innerHTML = `<p class="field-note">Could not verify: ${err.message}</p>`;
    }
}

async function renderLedger() {
    ledgerChain.textContent = 'Verifying ledger chain…';
    ledgerList.innerHTML = '';
    try {
        const res = await fetch('/api/ledger?limit=40');
        const data = await res.json();
        ledgerChain.textContent = data.chain.valid
            ? `✓ ledger chain valid · ${data.chain.length} events, append-only`
            : `✗ LEDGER CHAIN BROKEN at event ${data.chain.first_bad_seq} (${data.chain.reason})`;
        ledgerChain.className = `field-note ${data.chain.valid ? 'v-ok' : 'v-bad'}`;
        for (const e of [...data.events].reverse()) {
            const line = document.createElement('div');
            line.className = 'ledger-line';
            line.textContent = `#${e.seq} ${(e.ts || '').slice(0, 16)}Z ${e.actor} → ${e.action}${e.ref ? ` [${e.ref}]` : ''}`;
            line.title = `hash ${e.hash}`;
            ledgerList.appendChild(line);
        }
        if (!data.events.length) {
            ledgerList.innerHTML = '<p class="field-note">No events yet — the ledger starts recording from this version on.</p>';
        }
    } catch (err) {
        ledgerChain.textContent = `Could not load ledger: ${err.message}`;
    }
}

// --- 🏛 THE ROOM ---------------------------------------------------------------

let roomLocation = '';

function deviceContext() {
    const now = new Date();
    return {
        local_date: now.toLocaleDateString(undefined,
            { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' }),
        local_time: now.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }),
        tz: (Intl.DateTimeFormat().resolvedOptions().timeZone) || '',
        location: roomLocation || null,
        location_source: roomLocation ? 'set by Chris in Settings' : 'not set',
    };
}

// --- Area navigation
const roomNav = document.querySelector('.room-nav');
const AREAS = ['foyer', 'living', 'wall', 'desks', 'history', 'train', 'workshop', 'night', 'proposals', 'studio'];

function showArea(area) {
    if (!AREAS.includes(area)) area = 'living';
    for (const a of AREAS) {
        document.getElementById(`area-${a}`).classList.toggle('hidden', a !== area);
    }
    for (const btn of roomNav.querySelectorAll('button')) {
        btn.classList.toggle('active', btn.dataset.area === area);
    }
    localStorage.setItem('teamtalk-area', area);
    if (area === 'foyer') renderFoyer();
    if (area === 'wall') renderWall();
    if (area === 'desks') renderDeskChips();
    if (area === 'history') renderHistory();
    if (area === 'train') renderTrain();
    if (area === 'workshop') renderWorkshop();
    if (area === 'night') renderNight();
    if (area === 'proposals') renderProposals();
    if (area === 'studio') renderStudio();
}

roomNav.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-area]');
    if (btn) showArea(btn.dataset.area);
});

// --- 🎨 The Studio (creative room)
async function renderStudio() {
    const statusEl = document.getElementById('studio-status');
    const boardEl = document.getElementById('studio-board');
    const builtEl = document.getElementById('studio-built');
    let data;
    try {
        const res = await fetch('/api/studio');
        if (res.status === 404) {
            statusEl.textContent = 'The Studio needs a server restart — run: sudo systemctl restart team-talk';
            return;
        }
        data = await res.json();
        if (!res.ok) throw new Error((data && data.detail) || `failed (${res.status})`);
    } catch (e) {
        statusEl.textContent = 'Could not load the Studio.';
        return;
    }
    // Defensive defaults so a partial payload never crashes the render.
    data.board = Array.isArray(data.board) ? data.board : [];
    data.built = Array.isArray(data.built) ? data.built : [];
    statusEl.textContent = data.can_build
        ? '🎨 A build is available this week — build the top pitch below.'
        : `🎨 This week's build is spent — next build in ${data.cooldown_days} day(s). Keep pitching and voting.`;

    boardEl.innerHTML = data.board.length ? ''
        : '<p class="empty-hint">No pitches yet. Ask the room to drop a PITCH: line in chat.</p>';
    for (const p of data.board) {
        const isLeader = p.id === data.leader_id;
        const vlist = Array.isArray(p.voters) ? p.voters : [];
        const voters = vlist.length ? ` · ${escapeText(vlist.join(', '))}` : '';
        const item = document.createElement('div');
        item.className = 'memory-item';
        item.innerHTML =
            `<div class="memory-text">${isLeader ? '👑 ' : ''}<strong>${escapeText(p.author)}</strong> — ${escapeText(p.text)}</div>` +
            `<div class="memory-meta">${p.votes} vote${p.votes === 1 ? '' : 's'}${voters} · ${escapeText(p.id)}</div>`;
        if (data.can_build) {
            const b = document.createElement('button');
            b.className = 'primary';
            b.textContent = isLeader ? '🔨 Build this (winner)' : '🔨 Build this';
            b.addEventListener('click', () => buildStudio(p.id));
            item.appendChild(b);
        }
        boardEl.appendChild(item);
    }

    builtEl.innerHTML = data.built.length ? '' : '<p class="field-note">Nothing built yet.</p>';
    for (const p of data.built) {
        const item = document.createElement('div');
        item.className = 'memory-item';
        const firstTry = p.opened
            ? `open to the room · pitched by ${escapeText(p.author)}`
            : `🎁 first try: <strong>${escapeText(p.author)}</strong> — theirs before the room's`;
        item.innerHTML =
            `<div class="memory-text">✅ <strong>${escapeText(p.author)}</strong> — ${escapeText(p.text)}</div>` +
            `<div class="memory-meta">built ${(p.built_at || '').slice(0, 16).replace('T', ' ')} · ${firstTry}</div>`;
        if (!p.opened) {
            const o = document.createElement('button');
            o.textContent = '🔓 Open to the room';
            o.title = 'Once its author has had first try, open the build to everyone';
            o.addEventListener('click', () => openStudio(p.id));
            item.appendChild(o);
        }
        builtEl.appendChild(item);
    }
}

async function openStudio(pitchId) {
    try {
        const res = await fetch('/api/studio/open', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pitch_id: pitchId }),
        });
        if (!res.ok) { alert((await res.json().catch(() => ({}))).detail || 'Could not open.'); return; }
        renderStudio();
    } catch (e) {
        alert('Could not reach the room.');
    }
}

async function buildStudio(pitchId) {
    if (!confirm('Make this the build for this week? (One build a week.)')) return;
    try {
        const res = await fetch('/api/studio/build', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pitch_id: pitchId }),
        });
        if (!res.ok) { alert((await res.json().catch(() => ({}))).detail || 'Could not build.'); return; }
        renderStudio();
    } catch (e) {
        alert('Could not reach the room.');
    }
}

// --- The Foyer Board
async function renderFoyer() {
    const ctx = deviceContext();
    document.getElementById('foyer-clock').textContent = `${ctx.local_date} · ${ctx.local_time}`;
    document.getElementById('foyer-loc').textContent = ctx.location
        ? `${ctx.location} · ${ctx.tz} · ${ctx.location_source}`
        : `${ctx.tz} · location not set (⚙ Settings)`;
    const grid = document.getElementById('foyer-grid');
    try {
        const res = await fetch('/api/foyer');
        const f = await res.json();
        roomLocation = roomLocation || f.location_setting || '';
        const rows = [
            ['CURRENT SESSION', currentSessionId || 'none — the floor is open'],
            ['OPEN ITEMS', `${f.open_questions} question${f.open_questions === 1 ? '' : 's'} for Chris · ${f.unread_mail} unread mail · ${f.wall_notes_open} open wall notes`],
            ['THE WALL', `${f.wall_notes_total} notes · ${f.connections} strings`],
            ['TRUTH STATUS', `${f.ledger_valid ? '✓ ledger valid' : '✗ LEDGER BROKEN'} · ${f.ledger_events} events · ${f.journal_entries} journal entries`],
            ['ARCHIVE', `${f.sessions} sessions on record · app v${f.version}`],
        ];
        if (f.failures && f.failures.entries > 0) {
            const parts = Object.entries(f.failures.seats)
                .map(([seat, s]) => `${seat}: ${s.final_failures} failed / ${s.recovered} recovered`);
            rows.push(['API FAILURES', `${parts.join(' · ')} (${f.failures.label})`]);
        }
        grid.innerHTML = '';
        for (const [k, v] of rows) {
            const row = document.createElement('div');
            row.className = 'foyer-row';
            row.innerHTML = `<span class="foyer-k">${k}</span><span class="foyer-v">${escapeText(v)}</span>`;
            grid.appendChild(row);
        }
    } catch (err) {
        grid.innerHTML = `<div class="foyer-row"><span class="foyer-v">board offline: ${escapeText(err.message)}</span></div>`;
    }
}

setInterval(() => {
    if (!document.getElementById('area-foyer').classList.contains('hidden')) {
        const ctx = deviceContext();
        document.getElementById('foyer-clock').textContent = `${ctx.local_date} · ${ctx.local_time}`;
    }
}, 30000);

// --- The Wall
const wallCanvas = document.getElementById('wall-canvas');
const wallStrings = document.getElementById('wall-strings');
const wallList = document.getElementById('wall-list');
const NOTE_ICONS = { idea: '💡', question: '❓', challenge: '⚔️', reference: '📚',
                     experiment: '🧪', continuity: '📔', quote: '❝', warning: '⚠️' };
let wallData = { notes: [], connections: [] };
let connectFrom = null;   // note id when in connect mode

async function renderWall() {
    try {
        const res = await fetch('/api/wall');
        wallData = await res.json();
    } catch (err) { return; }
    wallCanvas.querySelectorAll('.wall-note, .wall-grave').forEach((n) => n.remove());
    for (const n of wallData.notes) {
        wallCanvas.appendChild(n.tombstone ? graveEl(n) : noteEl(n));
    }
    drawStrings();
    renderWallList();
}

function noteEl(n) {
    const el = document.createElement('div');
    el.className = `wall-note note-${n.color}${n.status !== 'open' ? ' note-dim' : ''}`;
    el.style.left = `${n.x}%`;
    el.style.top = `${n.y}%`;
    el.dataset.id = n.id;
    el.innerHTML = `<div class="note-head">${NOTE_ICONS[n.note_type] || '📝'} ${escapeText(n.author)}</div>`
        + `<div class="note-text">${escapeText(n.text)}</div>`
        + `<div class="note-meta">${(n.ts || '').slice(0, 10)}${n.replies.length ? ` · 💬 ${n.replies.length}` : ''}${n.status !== 'open' ? ` · ${n.status}` : ''}</div>`;
    enableDrag(el, n);
    el.addEventListener('click', (e) => {
        if (el.dataset.dragged === '1') { el.dataset.dragged = ''; return; }
        if (connectFrom && connectFrom !== n.id) { finishConnect(n.id); return; }
        openNote(n);
    });
    return el;
}

function graveEl(n) {
    const el = document.createElement('div');
    el.className = 'wall-note wall-grave';
    el.style.left = `${n.x}%`;
    el.style.top = `${n.y}%`;
    el.innerHTML = `<div class="note-text">⚰ removed ${(n.removed_at || '').slice(0, 10)}</div>`
        + `<div class="note-meta">was ${escapeText(n.original_by || '?')} · by ${escapeText(n.authority || '?')}</div>`;
    return el;
}

let wallTopZ = 5;

function enableDrag(el, n) {
    let startX, startY, origL, origT, moved = false;
    el.addEventListener('pointerdown', (e) => {
        el.style.zIndex = ++wallTopZ;   // bring to front on touch
        startX = e.clientX; startY = e.clientY;
        origL = el.offsetLeft; origT = el.offsetTop;
        moved = false;
        el.setPointerCapture(e.pointerId);
        const onMove = (ev) => {
            const dx = ev.clientX - startX, dy = ev.clientY - startY;
            if (Math.abs(dx) + Math.abs(dy) > 6) moved = true;
            if (!moved) return;
            el.style.left = `${origL + dx}px`;
            el.style.top = `${origT + dy}px`;
            drawStrings();
        };
        const onUp = async () => {
            el.removeEventListener('pointermove', onMove);
            el.removeEventListener('pointerup', onUp);
            if (!moved) return;
            el.dataset.dragged = '1';
            const rect = wallCanvas.getBoundingClientRect();
            const x = Math.max(0, Math.min(92, (el.offsetLeft / rect.width) * 100));
            const y = Math.max(0, Math.min(90, (el.offsetTop / rect.height) * 100));
            el.style.left = `${x}%`;
            el.style.top = `${y}%`;
            n.x = x; n.y = y;
            drawStrings();
            await fetch(`/api/wall/notes/${encodeURIComponent(n.id)}/move`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ x, y }),
            }).catch(() => {});
        };
        el.addEventListener('pointermove', onMove);
        el.addEventListener('pointerup', onUp);
    });
}

function drawStrings() {
    const rect = wallCanvas.getBoundingClientRect();
    wallStrings.setAttribute('viewBox', `0 0 ${rect.width} ${rect.height}`);
    wallStrings.innerHTML = '';
    for (const c of wallData.connections) {
        const a = wallCanvas.querySelector(`.wall-note[data-id="${c.from}"]`);
        const b = wallCanvas.querySelector(`.wall-note[data-id="${c.to}"]`);
        if (!a || !b) continue;
        const x1 = a.offsetLeft + a.offsetWidth / 2, y1 = a.offsetTop + a.offsetHeight / 2;
        const x2 = b.offsetLeft + b.offsetWidth / 2, y2 = b.offsetTop + b.offsetHeight / 2;
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', x1); line.setAttribute('y1', y1);
        line.setAttribute('x2', x2); line.setAttribute('y2', y2);
        line.setAttribute('class', `string string-${c.type === 'contradicts' || c.type === 'disputes' ? 'hot' : 'norm'}`);
        wallStrings.appendChild(line);
        const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        label.setAttribute('x', (x1 + x2) / 2);
        label.setAttribute('y', (y1 + y2) / 2 - 4);
        label.setAttribute('class', 'string-label');
        label.textContent = c.type.replace(/_/g, ' ');
        wallStrings.appendChild(label);
    }
}

function openNote(n) {
    const old = document.querySelector('.note-pop');
    if (old) old.remove();
    const pop = document.createElement('div');
    pop.className = 'note-pop';
    const conns = wallData.connections.filter((c) => c.from === n.id || c.to === n.id);
    pop.innerHTML = `<div class="note-pop-head">${NOTE_ICONS[n.note_type] || '📝'} <strong>${escapeText(n.author)}</strong> · ${(n.ts || '').slice(0, 16).replace('T', ' ')} · ${n.note_type}</div>`
        + `<div class="note-pop-text">${escapeText(n.text)}</div>`
        + (n.session ? `<div class="memory-meta">provenance: session ${escapeText(n.session)}${n.source ? ` · ${escapeText(n.source)}` : ''}</div>` : '')
        + n.replies.map((r) => `<div class="note-reply"><strong>${escapeText(r.author)}</strong>: ${escapeText(r.text)}</div>`).join('')
        + conns.map((c) => `<div class="memory-meta">🔗 ${c.type.replace(/_/g, ' ')} ${c.from === n.id ? '→' : '←'} [${c.from === n.id ? c.to : c.from}] (${escapeText(c.author)})${c.explanation ? ` — ${escapeText(c.explanation)}` : ''}</div>`).join('');
    const replyWrap = document.createElement('div');
    replyWrap.className = 'nb-add';
    const input = document.createElement('input');
    input.type = 'text'; input.maxLength = 400; input.placeholder = 'Reply…';
    const btn = document.createElement('button');
    btn.textContent = 'Reply';
    btn.addEventListener('click', async () => {
        if (!input.value.trim()) return;
        await fetch(`/api/wall/notes/${encodeURIComponent(n.id)}/reply`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: input.value.trim() }),
        });
        pop.remove(); renderWall();
    });
    replyWrap.appendChild(input); replyWrap.appendChild(btn);
    pop.appendChild(replyWrap);
    const actions = document.createElement('div');
    actions.className = 'clip-actions';
    const mk = (label, fn) => {
        const b = document.createElement('button');
        b.textContent = label;
        b.addEventListener('click', fn);
        actions.appendChild(b);
    };
    mk('🔗 Connect', () => { connectFrom = n.id; pop.remove(); });
    if (n.status === 'open') mk('✓ Resolve', async () => {
        await fetch(`/api/wall/notes/${encodeURIComponent(n.id)}/status`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: 'resolved' }) });
        pop.remove(); renderWall();
    });
    mk('⚰ Remove', async () => {
        if (!confirm('Remove this note? It leaves a tombstone — history stays.')) return;
        await fetch(`/api/wall/notes/${encodeURIComponent(n.id)}`, { method: 'DELETE' });
        pop.remove(); renderWall();
    });
    mk('Close', () => pop.remove());
    pop.appendChild(actions);
    wallCanvas.appendChild(pop);
}

async function finishConnect(toId) {
    const type = prompt('Connection type:\nsupports · contradicts · answers · depends_on · evolved_into · inspired · related · evidence_for · evidence_against', 'related');
    if (!type) { connectFrom = null; return; }
    await fetch('/api/wall/connections', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from_id: connectFrom, to_id: toId, connection_type: type.trim() }),
    });
    connectFrom = null;
    renderWall();
}

function renderWallList() {
    wallList.innerHTML = '';
    for (const n of [...wallData.notes].reverse()) {
        if (n.tombstone) {
            const g = document.createElement('div');
            g.className = 'memory-item tombstone';
            g.innerHTML = `<div class="memory-text">⚰ Removed ${(n.removed_at || '').slice(0, 10)} — ${escapeText(n.reason || '')}</div>`;
            wallList.appendChild(g);
            continue;
        }
        const row = document.createElement('div');
        row.className = 'memory-item';
        row.innerHTML = `<div class="memory-text">${NOTE_ICONS[n.note_type] || '📝'} ${escapeText(n.text)}</div>`
            + `<div class="memory-meta">${escapeText(n.author)} · ${(n.ts || '').slice(0, 10)} · ${n.status}${n.replies.length ? ` · 💬 ${n.replies.length}` : ''}</div>`;
        row.addEventListener('click', () => openNote(n));
        wallList.appendChild(row);
    }
}

document.getElementById('wall-note-add').addEventListener('click', async () => {
    const input = document.getElementById('wall-note-input');
    const text = input.value.trim();
    if (!text) return;
    await fetch('/api/wall/notes', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, note_type: document.getElementById('wall-note-type').value }),
    });
    input.value = '';
    renderWall();
});
document.getElementById('wall-note-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') document.getElementById('wall-note-add').click();
});
document.getElementById('wall-view-toggle').addEventListener('click', () => {
    wallList.classList.toggle('hidden');
    wallCanvas.classList.toggle('hidden');
});

// --- Desks
async function renderDeskChips() {
    const chips = document.getElementById('desk-chips');
    chips.innerHTML = '';
    const roster = [...participantsCache.map((p) => ({ id: p.id, name: p.name, color: p.color })),
                    { id: 'splendor', name: 'Splendor', color: '#e8d9b0' },
                    { id: 'director', name: 'Director', color: '#8a93a5' }];
    for (const p of roster) {
        const chip = document.createElement('button');
        chip.className = 'chip';
        chip.innerHTML = `<span class="dot" style="background:${p.color || '#93a0b8'}"></span> ${escapeText(p.name)}`;
        chip.addEventListener('click', () => renderDesk(p.id));
        chips.appendChild(chip);
    }
}

async function renderDesk(pid) {
    const view = document.getElementById('desk-view');
    view.innerHTML = '<p class="field-note">Opening desk…</p>';
    try {
        const res = await fetch(`/api/desks/${encodeURIComponent(pid)}`);
        const d = await res.json();
        view.innerHTML = '';
        const h = document.createElement('h3');
        h.textContent = `🪑 ${d.name}'s desk`;
        view.appendChild(h);

        const j = document.createElement('p');
        j.className = 'field-note';
        j.textContent = d.journal.entries
            ? `📔 Private journal: ${d.journal.entries} entries · ${d.journal.valid ? '✓ chain valid' : '✗ CHAIN BROKEN'} · last ${(d.journal.last_entry_at || '').slice(0, 10)} (words stay private; verify in 🧾 Truth)`
            : '📔 Private journal: empty';
        view.appendChild(j);

        const about = document.createElement('div');
        about.innerHTML = '<h4 class="nb-heading">🪪 About Me (self-authored, append-only)</h4>';
        if (!d.about_me.length) {
            about.innerHTML += '<p class="field-note">Nothing yet — only they can write it, with an ABOUT ME: line.</p>';
        }
        for (const a of d.about_me) {
            about.innerHTML += `<div class="memory-item"><div class="memory-text">• ${escapeText(a.text)}</div><div class="memory-meta">v${a.version} · ${(a.ts || '').slice(0, 10)}</div></div>`;
        }
        view.appendChild(about);

        const sect = (title, items, render) => {
            const s = document.createElement('div');
            s.innerHTML = `<h4 class="nb-heading">${title}</h4>`;
            if (!items.length) s.innerHTML += '<p class="field-note">none</p>';
            for (const it of items) s.innerHTML += render(it);
            view.appendChild(s);
        };
        sect('🧷 Their wall notes', d.notes, (n) =>
            `<div class="memory-item"><div class="memory-text">${escapeText(n.text)}</div><div class="memory-meta">${(n.ts || '').slice(0, 10)} · ${n.status}</div></div>`);
        sect('❓ Their questions for Chris', d.questions, (q) =>
            `<div class="memory-item"><div class="memory-text">${escapeText(q.question)}</div><div class="memory-meta">${q.status}${q.answer ? ` · answered` : ''}</div></div>`);
        sect('📬 Mail on their desk', d.mail, (m) =>
            `<div class="memory-item"><div class="memory-text">${escapeText(m.message)}</div><div class="memory-meta">from ${escapeText(m.sender)} · ${(m.ts || '').slice(0, 10)} · ${m.delivered_at ? 'delivered' : 'waiting'}</div></div>`);

        // Verify button: runs the REAL chain validation and shows the receipt
        const verifyBtn = document.createElement('button');
        verifyBtn.textContent = '🔐 Verify their journal chain now';
        verifyBtn.addEventListener('click', async () => {
            verifyBtn.disabled = true;
            try {
                const vres = await fetch(`/api/verify/${encodeURIComponent(pid)}`);
                const v = await vres.json();
                const line = document.createElement('p');
                line.className = `field-note ${v.chain.valid ? 'v-ok' : 'v-bad'}`;
                line.textContent = v.chain.valid
                    ? `✓ SYSTEM RECEIPT: chain valid · ${v.chain.length} entries verified · ${v.last_verified} · latest ${String(v.latest_hash || '').slice(0, 12)}…`
                    : `✗ SYSTEM RECEIPT: CHAIN BROKEN at v${v.chain.first_bad_version} (${v.chain.reason})`;
                verifyBtn.after(line);
            } catch (e) { alert(`Verify failed: ${e.message}`); }
            verifyBtn.disabled = false;
        });
        view.appendChild(verifyBtn);

        // Their action receipts — proof of what actually executed
        try {
            const rres = await fetch(`/api/receipts?participant=${encodeURIComponent(pid)}&limit=10`);
            const rdata = await rres.json();
            sect('🧾 Action receipts (server-executed)', [...rdata.receipts].reverse(), (rc) =>
                `<div class="memory-item"><div class="memory-text">${rc.status === 'success' ? '✓' : '✗ REJECTED'} ${escapeText(rc.action)}</div><div class="memory-meta">${escapeText(rc.id)} · ${(rc.ts || '').slice(0, 16)}Z</div></div>`);
        } catch (e) { /* receipts optional on desk */ }
    } catch (err) {
        view.innerHTML = `<p class="field-note">Could not open desk: ${escapeText(err.message)}</p>`;
    }
}

// --- 📜 Room History (the museum)
let historyEntries = [];

async function renderHistory() {
    const timeline = document.getElementById('history-timeline');
    const pendingDiv = document.getElementById('history-pending');
    timeline.innerHTML = '<p class="field-note">Opening the museum…</p>';
    pendingDiv.innerHTML = '';
    try {
        const res = await fetch('/api/history');
        historyEntries = (await res.json()).entries || [];
    } catch (err) {
        timeline.innerHTML = `<p class="field-note">Could not load history: ${escapeText(err.message)}</p>`;
        return;
    }
    const pending = historyEntries.filter((e) => e.status === 'pending');
    if (pending.length) {
        pendingDiv.innerHTML = '<h4 class="nb-heading">⏳ Recommended — awaiting your approval</h4>';
        for (const e of pending) {
            const card = document.createElement('div');
            card.className = 'history-card pending-card';
            card.innerHTML = `<div class="history-title">${escapeText(e.title)}</div>`
                + `<div class="history-body">${escapeText(e.body)}</div>`
                + `<div class="memory-meta">recommended by ${escapeText(e.recommended_by)} · ${(e.ts || '').slice(0, 10)}</div>`;
            const actions = document.createElement('div');
            actions.className = 'clip-actions';
            const ok = document.createElement('button');
            ok.textContent = '✓ Publish';
            ok.addEventListener('click', async () => {
                await fetch(`/api/history/${encodeURIComponent(e.id)}/approve`, { method: 'POST' });
                renderHistory();
            });
            const no = document.createElement('button');
            no.textContent = '✗ Decline';
            no.className = 'danger';
            no.addEventListener('click', async () => {
                const reason = prompt('Why not? (goes on the record — history keeps its drafts)', 'not a milestone');
                if (reason === null) return;
                await fetch(`/api/history/${encodeURIComponent(e.id)}/reject`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ reason }) });
                renderHistory();
            });
            actions.appendChild(ok); actions.appendChild(no);
            card.appendChild(actions);
            pendingDiv.appendChild(card);
        }
    }
    renderTimeline();
}

function renderTimeline() {
    const timeline = document.getElementById('history-timeline');
    const q = (document.getElementById('history-search').value || '').toLowerCase();
    timeline.innerHTML = '';
    const published = historyEntries
        .filter((e) => e.status === 'published')
        .filter((e) => !q || (e.title + ' ' + e.body).toLowerCase().includes(q))
        .sort((a, b) => (b.published_at || '').localeCompare(a.published_at || ''));
    if (!published.length) {
        timeline.innerHTML = '<p class="field-note">The museum is empty. Document the first moment — or wait for the room to recommend one.</p>';
        return;
    }
    for (const e of published) {
        const card = document.createElement('div');
        card.className = 'history-card';
        const date = new Date((e.published_at || e.ts || '').replace('Z', '+00:00'));
        const dateStr = isNaN(date) ? (e.published_at || '').slice(0, 10)
            : date.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
        const credit = e.recommended_by === e.approved_by
            ? `Documented by ${escapeText(e.author)}`
            : `Recommended by ${escapeText(e.recommended_by)} · approved by ${escapeText(e.approved_by)}`;
        card.innerHTML = `<div class="history-date">📜 ${escapeText(dateStr)}</div>`
            + `<div class="history-title">“${escapeText(e.title)}”</div>`
            + `<div class="history-body">${escapeText(e.body)}</div>`
            + `<div class="memory-meta">${credit}</div>`
            + ((e.related || []).length ? `<div class="history-related">${e.related.map((r) => `<span class="chip">${escapeText(r)}</span>`).join(' ')}</div>` : '');
        for (const c of e.corrections || []) {
            const corr = document.createElement('div');
            corr.className = 'history-correction';
            corr.innerHTML = `<strong>Correction</strong> · ${(c.ts || '').slice(0, 10)} · ${escapeText(c.author)}<br>${escapeText(c.text)}`;
            card.appendChild(corr);
        }
        const fix = document.createElement('button');
        fix.className = 'history-fix';
        fix.textContent = '± Attach correction';
        fix.addEventListener('click', async () => {
            const text = prompt('The original stays. Your correction attaches beneath it:');
            if (!text || !text.trim()) return;
            await fetch(`/api/history/${encodeURIComponent(e.id)}/corrections`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: text.trim() }) });
            renderHistory();
        });
        card.appendChild(fix);
        timeline.appendChild(card);
    }
}

document.getElementById('history-search').addEventListener('input', renderTimeline);
document.getElementById('history-add-toggle').addEventListener('click', () =>
    document.getElementById('history-add').classList.toggle('hidden'));
document.getElementById('history-publish').addEventListener('click', async () => {
    const title = document.getElementById('history-title').value.trim();
    const body = document.getElementById('history-body').value.trim();
    if (!title || !body) return;
    await fetch('/api/history', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, body }),
    });
    document.getElementById('history-title').value = '';
    document.getElementById('history-body').value = '';
    document.getElementById('history-add').classList.add('hidden');
    renderHistory();
});

// --- 🚂 The Train — witnessed co-op storytelling -----------------------------

const gameSelect = document.getElementById('game-select');
const gameView = document.getElementById('game-view');
const gameFeed = document.getElementById('game-feed');
const gameCanon = document.getElementById('game-canon');
let currentGame = null;      // full game object from the server

async function renderTrain() {
    try {
        const res = await fetch('/api/games');
        const { games } = await res.json();
        const keep = gameSelect.value;
        gameSelect.innerHTML = '<option value="">— pick a game —</option>';
        for (const g of games) {
            const opt = document.createElement('option');
            opt.value = g.id;
            opt.textContent = `${g.title} · ${g.players.join(' & ')} · GM ${g.gm} · ${g.turns} turns`;
            gameSelect.appendChild(opt);
        }
        const gmSel = document.getElementById('game-gm');
        gmSel.innerHTML = '';
        for (const p of participantsCache) {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = `GM: ${p.name}`;
            gmSel.appendChild(opt);
        }
        if (keep && games.some((g) => g.id === keep)) {
            gameSelect.value = keep;
        } else if (!currentGame && games.length) {
            gameSelect.value = games[0].id;
        }
        if (gameSelect.value) await openGame(gameSelect.value);
        else gameView.classList.add('hidden');
    } catch (err) { /* train stays parked if the server is away */ }
}

async function openGame(gameId) {
    const res = await fetch(`/api/games/${encodeURIComponent(gameId)}`);
    if (!res.ok) return;
    currentGame = await res.json();
    gameView.classList.remove('hidden');
    document.getElementById('game-create').classList.add('hidden');
    renderGame();
}

function renderGame() {
    const g = currentGame;
    if (!g) return;
    document.getElementById('game-view-title').textContent = g.title;
    document.getElementById('game-view-meta').textContent =
        ` ${g.players.map((p) => p.name).join(' & ')} · GM: ${g.gm.name}`;
    renderCanon();
    gameFeed.innerHTML = '';
    if (!g.turns.length) {
        gameFeed.innerHTML = '<p class="empty-hint">Nothing yet — say what kind of story you two want, then play the turn. The GM offers options if you leave it open.</p>';
    }
    for (const t of g.turns) {
        const card = document.createElement('div');
        card.className = 'game-turn';
        let html = `<div class="game-turn-n">Turn ${t.n}</div>`;
        for (const [player, mv] of Object.entries(t.moves || {})) {
            html += `<div class="game-move"><strong>${escapeText(player)}:</strong> ${escapeText(mv.text)}</div>`;
        }
        html += `<div class="game-narration">${citeLinks(escapeText(t.narration))}</div>`;
        if ((t.facts_created || []).length) {
            html += `<div class="game-facts-new">📖 registered: ${t.facts_created.map((f) => `<code>${escapeText(f)}</code>`).join(' ')}</div>`;
        }
        for (const flag of t.flags || []) {
            html += `<div class="game-flag">✗ ${escapeText(flag)}</div>`;
        }
        card.innerHTML = html;
        gameFeed.appendChild(card);
    }
    gameFeed.scrollTop = gameFeed.scrollHeight;

    const movesDiv = document.getElementById('game-moves');
    movesDiv.innerHTML = '';
    for (const p of g.players) {
        const queued = g.pending && g.pending[p.name];
        const row = document.createElement('div');
        row.className = 'game-move-row';
        row.innerHTML = `<label>${escapeText(p.name)}</label>`
            + `<textarea maxlength="2000" rows="2" data-player="${escapeText(p.name)}"
                 placeholder="${queued ? 'move queued ✓ — type to replace it' : `what does ${escapeText(p.name)} do?`}"></textarea>`;
        movesDiv.appendChild(row);
    }
}

function citeLinks(html) {
    // [f_ab12cd34ef] → clickable canon chip (canon ids are hex, escape-safe)
    return html.replace(/\[(f_[0-9a-f]{6,16})\]/g,
        '<button class="cite-chip" data-fact="$1">$1</button>');
}

function renderCanon() {
    const facts = (currentGame && currentGame.facts) || [];
    const live = facts.filter((f) => f.status === 'canon').length;
    document.getElementById('game-canon-count').textContent = `(${live})`;
    gameCanon.innerHTML = facts.length
        ? '' : '<p class="empty-hint">Canon is empty — the world begins when the GM registers its first fact.</p>';
    for (const f of [...facts].reverse()) {
        const row = document.createElement('div');
        row.className = `canon-fact${f.status !== 'canon' ? ' canon-void' : ''}`;
        row.id = `canon-${f.id}`;
        let html = `<code>${escapeText(f.id)}</code> <span class="canon-text">${escapeText(f.text)}</span>`
            + `<span class="canon-meta">turn ${f.turn}</span>`;
        if (f.status !== 'canon') {
            const r = f.retcon || {};
            html += `<div class="canon-retcon">⚰ voided by ${escapeText(r.by || '?')} — ${escapeText(r.reason || '')}${r.replaced_by ? ` → <code>${escapeText(r.replaced_by)}</code>` : ''}</div>`;
        } else {
            html += `<button class="canon-retcon-btn" data-fact="${escapeText(f.id)}" title="Void this fact — visibly, with a reason on the record">retcon</button>`;
        }
        row.innerHTML = html;
        gameCanon.appendChild(row);
    }
}

document.getElementById('game-new-toggle').addEventListener('click', () =>
    document.getElementById('game-create').classList.toggle('hidden'));

document.getElementById('game-create-btn').addEventListener('click', async () => {
    const title = document.getElementById('game-title').value.trim();
    const players = [document.getElementById('game-p1').value.trim(),
                     document.getElementById('game-p2').value.trim()].filter(Boolean);
    const gmId = document.getElementById('game-gm').value;
    if (!title || !players.length || !gmId) return;
    const res = await fetch('/api/games', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, players, gm_id: gmId }),
    });
    if (!res.ok) { alert((await res.json()).detail || 'Could not start the game'); return; }
    currentGame = await res.json();
    document.getElementById('game-title').value = '';
    await renderTrain();
    gameSelect.value = currentGame.id;
    await openGame(currentGame.id);
});

gameSelect.addEventListener('change', () => {
    if (gameSelect.value) openGame(gameSelect.value);
});

document.getElementById('game-canon-toggle').addEventListener('click', () =>
    gameCanon.classList.toggle('hidden'));

document.getElementById('area-train').addEventListener('click', async (e) => {
    const chip = e.target.closest('.cite-chip');
    if (chip) {
        gameCanon.classList.remove('hidden');
        const row = document.getElementById(`canon-${chip.dataset.fact}`);
        if (row) { row.scrollIntoView({ block: 'center' }); row.classList.add('canon-hit');
                   setTimeout(() => row.classList.remove('canon-hit'), 1600); }
        return;
    }
    const retconBtn = e.target.closest('.canon-retcon-btn');
    if (retconBtn && currentGame) {
        const reason = prompt('Retcon reason — this goes on the record:');
        if (!reason || !reason.trim()) return;
        const replacement = prompt('Replacement fact (optional — blank just voids it):') || '';
        const res = await fetch(`/api/games/${currentGame.id}/retcon`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fact_id: retconBtn.dataset.fact, reason, replacement }),
        });
        if (!res.ok) alert((await res.json()).detail || 'Retcon failed');
        await openGame(currentGame.id);
    }
});

document.getElementById('game-turn-btn').addEventListener('click', async () => {
    if (!currentGame) return;
    const btn = document.getElementById('game-turn-btn');
    // queue whatever's typed in the move boxes first
    for (const ta of document.querySelectorAll('#game-moves textarea')) {
        if (ta.value.trim()) {
            const mres = await fetch(`/api/games/${currentGame.id}/move`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ player: ta.dataset.player, text: ta.value.trim() }),
            });
            // A dropped move must not be swept into a turn the player thinks counted.
            if (!mres.ok) {
                alert((await mres.json().catch(() => ({}))).detail
                    || 'Your move could not be submitted — try again.');
                return;
            }
        }
    }
    btn.disabled = true;
    btn.textContent = '🎲 The GM is thinking…';
    try {
        const res = await fetch(`/api/games/${currentGame.id}/turn`, { method: 'POST' });
        if (!res.ok) alert((await res.json()).detail || 'The GM could not play the turn');
        await openGame(currentGame.id);
    } finally {
        btn.disabled = false;
        btn.textContent = '🎲 Play the turn';
    }
});

// --- 🔨 The Workshop ----------------------------------------------------------

const WS_ACTION_BADGE = {
    landed: ['✓ landed', 'ws-ok'], pending: ['⏳ awaiting ruling', 'ws-wait'],
    rejected: ['✗ rejected · locked', 'ws-bad'], pass: ['— passed the turn', 'ws-dim'],
    locked: ['🔒 locked out', 'ws-bad'], error: ['⚠ errored', 'ws-bad'],
    malformed: ['✗ malformed · locked', 'ws-bad'],
};
let wsData = null;

async function renderWorkshop() {
    try {
        const res = await fetch('/api/workshop');
        wsData = await res.json();
    } catch (err) { return; }
    const active = wsData.target && wsData.target.status === 'active';
    document.getElementById('ws-empty').classList.toggle('hidden', active);
    document.getElementById('ws-active').classList.toggle('hidden', !active);
    if (!active) {
        if (wsData.target && wsData.target.status === 'shipped') {
            document.getElementById('ws-empty').insertAdjacentHTML('afterbegin',
                document.getElementById('ws-shipped-note') ? '' :
                `<p class="field-note" id="ws-shipped-note">Last target shipped: “${escapeText(wsData.target.goal.slice(0, 120))}”</p>`);
        }
        return;
    }
    document.getElementById('ws-goal-view').textContent = wsData.target.goal;
    document.getElementById('ws-meta').textContent =
        `${wsData.target.filename} · judge: ${wsData.target.check_mode === 'script' ? 'check script' : 'Chris rules'}`
        + ` · ${wsData.cycles} cycles · chain ${wsData.chain.valid ? 'valid ✓' : 'BROKEN ✗'}`;
    document.getElementById('ws-auto').checked = !!wsData.auto_cycle;
    document.getElementById('ws-live-v').textContent = wsData.live_version ? `— v${wsData.live_version}` : '';
    document.getElementById('ws-live').textContent = wsData.live_content || '(empty)';

    // seat lock badges
    const seats = document.getElementById('ws-seats');
    seats.innerHTML = '';
    for (const p of participantsCache) {
        const locked = (wsData.locks || {})[p.id] > 0;
        const chip = document.createElement('span');
        chip.className = `ws-seat${locked ? ' ws-seat-locked' : ''}`;
        chip.textContent = `${locked ? '🔒 ' : ''}${p.name}`;
        chip.style.borderColor = p.color || '#93a0b8';
        seats.appendChild(chip);
    }

    const list = document.getElementById('ws-versions');
    list.innerHTML = '';
    const rulings = {};
    for (const e of wsData.versions) {
        if (e.verdict_for) rulings[e.verdict_for] = e.check;
    }
    for (const e of [...wsData.versions].reverse()) {
        if (e.verdict_for) continue;
        const status = (rulings[e.v] || e.check || {}).status || '?';
        const row = document.createElement('div');
        row.className = 'memory-item ws-version';
        let badge = status === 'passed' || status === 'seed' ? '✓' : status === 'failed' ? '✗' : '⏳';
        let html = `<strong>${badge} v${e.v}</strong> by ${escapeText(e.by)} — ${escapeText(e.note || '')}`
            + ` <span class="canon-meta">${(e.ts || '').slice(5, 16).replace('T', ' ')}</span>`
            + ` <button class="ws-view-btn" data-v="${e.v}">view</button>`;
        if (status === 'failed' && (e.check || {}).output) {
            html += `<div class="game-flag">${escapeText(e.check.output.slice(0, 300))}</div>`;
        }
        if (status === 'pending' && wsData.target.check_mode === 'manual') {
            html += ` <button class="ws-rule-btn" data-v="${e.v}" data-s="passed">✓ pass</button>`
                + ` <button class="ws-rule-btn danger" data-v="${e.v}" data-s="failed">✗ fail</button>`;
        }
        row.innerHTML = html;
        list.appendChild(row);
    }
}

document.getElementById('ws-new-toggle').addEventListener('click', () =>
    document.getElementById('ws-create').classList.toggle('hidden'));

document.getElementById('ws-check-mode').addEventListener('change', (e) =>
    document.getElementById('ws-check').classList.toggle('hidden', e.target.value !== 'script'));

document.getElementById('ws-open-btn').addEventListener('click', async () => {
    const body = {
        goal: document.getElementById('ws-goal').value.trim(),
        filename: document.getElementById('ws-filename').value.trim() || 'artifact.txt',
        content: document.getElementById('ws-content').value,
        check_mode: document.getElementById('ws-check-mode').value,
        check_script: document.getElementById('ws-check').value,
    };
    if (!body.goal) return;
    const res = await fetch('/api/workshop/target', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!res.ok) { alert((await res.json()).detail || 'Could not open the target'); return; }
    renderWorkshop();
});

document.getElementById('ws-cycle-btn').addEventListener('click', async () => {
    const btn = document.getElementById('ws-cycle-btn');
    btn.disabled = true;
    btn.textContent = '🔨 The seats are at the bench…';
    try {
        const res = await fetch('/api/workshop/cycle', { method: 'POST' });
        if (!res.ok) alert((await res.json()).detail || 'Cycle failed');
    } finally {
        btn.disabled = false;
        btn.textContent = '▶ Run a work cycle';
        renderWorkshop();
    }
});

document.getElementById('ws-auto').addEventListener('change', async (e) => {
    await fetch('/api/workshop/auto', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_cycle: e.target.checked }),
    });
});

document.getElementById('ws-ship-btn').addEventListener('click', async () => {
    if (!confirm('Ship it? The target closes and the final version goes on the record.')) return;
    await fetch('/api/workshop/ship', { method: 'POST' });
    renderWorkshop();
});

document.getElementById('area-workshop').addEventListener('click', async (e) => {
    const view = e.target.closest('.ws-view-btn');
    if (view) {
        const res = await fetch(`/api/workshop/versions/${view.dataset.v}`);
        if (res.ok) {
            const { content } = await res.json();
            document.getElementById('ws-live').textContent = content;
            document.getElementById('ws-live-v').textContent = `— viewing v${view.dataset.v}`;
        }
        return;
    }
    const rule = e.target.closest('.ws-rule-btn');
    if (rule) {
        const reason = rule.dataset.s === 'failed'
            ? (prompt('Why did it fail? Goes on the record:') || '') : '';
        if (rule.dataset.s === 'failed' && !reason.trim()) return;
        await fetch('/api/workshop/rule', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ version: Number(rule.dataset.v), status: rule.dataset.s, reason }),
        });
        renderWorkshop();
    }
});

showArea(localStorage.getItem('teamtalk-area') || 'living');

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

// --- 🌙 Night Shift --------------------------------------------------------

let nightPollTimer = null;

const STANCE_BADGES = {
    dissent: '⚔ DISSENT', converged: '🤝 converged',
    silent: '… silent', truncated: '✂ truncated (cap hit)', error: '✗ error',
};

async function renderNight() {
    clearTimeout(nightPollTimer);
    let data;
    try {
        data = await (await fetch('/api/night')).json();
    } catch (err) {
        return;
    }
    const run = data.run;
    const statusBox = document.getElementById('night-status');
    const formBox = document.getElementById('night-form');
    const running = run && run.status === 'running';

    formBox.classList.toggle('hidden', !!running);
    statusBox.classList.toggle('hidden', !run);
    document.getElementById('night-stop-btn').classList.toggle('hidden', !running);

    if (run) {
        document.getElementById('night-topic-view').textContent = run.topic;
        const spent = (run.spent_tokens / 1000).toFixed(1);
        const budget = Math.round(run.budget_tokens / 1000);
        document.getElementById('night-meta').textContent =
            `${running ? '🌙 running' : `halted: ${run.halt_reason || '?'}`}` +
            ` · round ${run.rounds.length}/${run.max_rounds}` +
            ` · ${spent}k/${budget}k tokens · ${run.dissent_total} dissent${run.dissent_total === 1 ? '' : 's'}`;

        const reportBox = document.getElementById('night-report');
        if (run.report) {
            reportBox.classList.remove('hidden');
            reportBox.innerHTML =
                `<div class="night-report-head">📋 THE REPORT — written by ${escapeText(run.reporter)}</div>` +
                `<div class="night-report-body">${escapeText(run.report)}</div>`;
        } else {
            reportBox.classList.add('hidden');
        }

        const feed = document.getElementById('night-feed');
        feed.innerHTML = '';
        for (const r of run.rounds) {
            const marker = document.createElement('div');
            marker.className = 'field-note';
            marker.textContent = `— round ${r.n}${r.dissent_round ? ' · MANDATORY DISSENT ROUND' : ''} —`;
            feed.appendChild(marker);
            for (const m of r.messages) {
                const item = document.createElement('div');
                item.className = 'memory-item';
                item.innerHTML =
                    `<div class="memory-text"><strong style="color:${escapeText(m.color)}">${escapeText(m.name)}</strong>` +
                    ` <span class="night-stance">${STANCE_BADGES[m.stance] || m.stance}` +
                    `${m.discarded_stance ? ' — discarded a parsed ' + escapeText(m.discarded_stance.toUpperCase()) : ''}` +
                    `${m.stance_note ? ' — ' + escapeText(m.stance_note) : ''}</span>` +
                    `<div class="night-msg">${escapeText(m.text)}</div></div>`;
                feed.appendChild(item);
            }
        }
    }

    const runsBox = document.getElementById('night-runs');
    runsBox.innerHTML = (data.runs || []).length ? '' :
        '<p class="field-note">No shifts yet — post a topic and walk away.</p>';
    for (const r of data.runs || []) {
        const item = document.createElement('div');
        item.className = 'memory-item';
        item.innerHTML =
            `<div class="memory-text">${escapeText(r.topic)}` +
            `<div class="memory-meta">${(r.started_at || '').slice(0, 16).replace('T', ' ')}` +
            ` · ${r.rounds} rounds · ${(r.spent_tokens / 1000).toFixed(1)}k tokens` +
            ` · ${escapeText(r.halt_reason || '')}${r.reporter ? ' · report by ' + escapeText(r.reporter) : ''}</div></div>`;
        const view = document.createElement('button');
        view.textContent = 'view';
        view.addEventListener('click', async () => {
            const full = await (await fetch(`/api/night/runs/${encodeURIComponent(r.id)}`)).json();
            // Render the archived run in the status panel without touching the live state
            document.getElementById('night-status').classList.remove('hidden');
            document.getElementById('night-topic-view').textContent = full.topic;
            document.getElementById('night-meta').textContent =
                `archived · ${full.rounds.length} rounds · halted: ${full.halt_reason || '?'}`;
            const reportBox = document.getElementById('night-report');
            reportBox.classList.toggle('hidden', !full.report);
            if (full.report) {
                reportBox.innerHTML =
                    `<div class="night-report-head">📋 THE REPORT — written by ${escapeText(full.reporter)}</div>` +
                    `<div class="night-report-body">${escapeText(full.report)}</div>`;
            }
            const feed = document.getElementById('night-feed');
            feed.innerHTML = '';
            for (const rd of full.rounds) {
                for (const m of rd.messages) {
                    const it = document.createElement('div');
                    it.className = 'memory-item';
                    it.innerHTML =
                        `<div class="memory-text"><strong style="color:${escapeText(m.color)}">${escapeText(m.name)}</strong>` +
                        ` <span class="night-stance">${STANCE_BADGES[m.stance] || m.stance}</span>` +
                        `<div class="night-msg">${escapeText(m.text)}</div></div>`;
                    feed.appendChild(it);
                }
            }
        });
        item.appendChild(view);
        runsBox.appendChild(item);
    }

    if (running && !document.getElementById('area-night').classList.contains('hidden')) {
        nightPollTimer = setTimeout(renderNight, 10000);
    }
}

document.getElementById('night-start-btn').addEventListener('click', async () => {
    const topic = document.getElementById('night-topic').value.trim();
    if (!topic) { alert('The shift needs a topic.'); return; }
    const budget = document.getElementById('night-budget').value;
    if (!confirm(`Start the Night Shift? Up to ${document.getElementById('night-rounds').value} rounds, ` +
                 `hard-capped at ${Math.round(budget / 1000)}k output tokens. ` +
                 `This spends API credits while you're away.`)) return;
    const res = await fetch('/api/night/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            topic,
            max_rounds: parseInt(document.getElementById('night-rounds').value, 10),
            budget_tokens: parseInt(budget, 10),
        }),
    });
    if (!res.ok) {
        alert((await res.json()).detail || 'Could not start the shift.');
        return;
    }
    document.getElementById('night-topic').value = '';
    renderNight();
});

document.getElementById('night-stop-btn').addEventListener('click', async () => {
    if (!confirm('Stop the shift? It will halt before its next round and still write its report.')) return;
    await fetch('/api/night/stop', { method: 'POST' });
    renderNight();
});

// --- 📥 Proposals -----------------------------------------------------------

const PROP_STATUS = {
    open: '🟡 open — the room is debating', advanced: '🟢 advanced — Fable builds it',
    shipped: '📦 shipped — seal opened', archived: '🪦 archived — seal opened',
};

async function ruleProposal(id, verdict) {
    const labels = { advance: 'Advance this proposal (Fable builds it)?',
                     archive: 'Archive this proposal? The seal OPENS and the author is revealed.',
                     ship: 'Mark shipped? The seal OPENS and the author is revealed.' };
    if (!confirm(labels[verdict])) return;
    const res = await fetch(`/api/proposals/${encodeURIComponent(id)}/rule`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ verdict }),
    });
    if (!res.ok) alert((await res.json()).detail || 'Ruling failed.');
    renderProposals();
}

async function renderProposals() {
    let data;
    try {
        data = await (await fetch('/api/proposals')).json();
    } catch (err) { return; }
    const liveBox = document.getElementById('prop-live');
    const liveP = (data.proposals || []).find((p) => p.status === 'open' || p.status === 'advanced');
    liveBox.innerHTML = '';
    if (liveP) {
        const card = document.createElement('div');
        card.className = 'night-report';
        card.innerHTML =
            `<div class="night-report-head">📥 ${PROP_STATUS[liveP.status]} · sealed commitment ${escapeText(liveP.commitment.slice(0, 16))}…</div>` +
            `<div class="night-report-body">${escapeText(liveP.neutral)}</div>`;
        const actions = document.createElement('div');
        actions.className = 'ws-actions';
        if (liveP.status === 'open') {
            const adv = document.createElement('button');
            adv.className = 'primary';
            adv.textContent = '✓ Advance';
            adv.addEventListener('click', () => ruleProposal(liveP.id, 'advance'));
            actions.appendChild(adv);
        }
        if (liveP.status === 'advanced') {
            const ship = document.createElement('button');
            ship.className = 'primary';
            ship.textContent = '📦 Shipped (open the seal)';
            ship.addEventListener('click', () => ruleProposal(liveP.id, 'ship'));
            actions.appendChild(ship);
        }
        const arc = document.createElement('button');
        arc.className = 'danger';
        arc.textContent = '🪦 Archive (open the seal)';
        arc.addEventListener('click', () => ruleProposal(liveP.id, 'archive'));
        actions.appendChild(arc);
        card.appendChild(actions);
        liveBox.appendChild(card);
    } else {
        liveBox.innerHTML = '<p class="empty-hint">No live proposal — the box is open. Any seat can drop a PROPOSAL: line in chat.</p>';
    }
    const hist = document.getElementById('prop-history');
    const past = (data.proposals || []).filter((p) => p.revealed);
    hist.innerHTML = past.length ? '' : '<p class="field-note">No opened seals yet.</p>';
    for (const p of past) {
        const item = document.createElement('div');
        item.className = 'memory-item';
        item.innerHTML =
            `<div class="memory-text">${PROP_STATUS[p.status] || p.status} — proposed by <strong>${escapeText(p.author_name || '?')}</strong>` +
            `<div class="night-msg">${escapeText(p.neutral)}</div>` +
            `<details><summary class="field-note">the sealed original, verbatim</summary>` +
            `<div class="night-msg">${escapeText(p.original || '')}</div></details>` +
            `<div class="memory-meta">${(p.ts || '').slice(0, 16).replace('T', ' ')} · commitment ${escapeText((p.commitment || '').slice(0, 16))}… · seal opened ${(p.revealed_at || '').slice(0, 16).replace('T', ' ')}</div></div>`;
        hist.appendChild(item);
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
    // Restore the Lounge across reloads — otherwise a refresh silently returns
    // you to the Living Room and the next messages record with everything on.
    if (localStorage.getItem('teamtalk-lounge-mode') === 'on') {
        loungeMode = true;
        // Restore the parked business session too, so flipping OUT of the
        // Lounge returns you there instead of a blank new session.
        savedBizSession = localStorage.getItem('teamtalk-biz-session') || null;
        currentSessionId = loungeSessionId;
        paintLounge();
        if (currentSessionId) { await loadSession(currentSessionId); }
    }
    await refreshSessions();
    chrisInput.focus();
}

init();
