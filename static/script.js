// Team Talk frontend — sends Chris's message once, renders both AI
// responses side-by-side, and manages saved sessions.

const sessionSelect = document.getElementById('session-select');
const exportBtn = document.getElementById('export-btn');
const deleteBtn = document.getElementById('delete-btn');
const historyDiv = document.getElementById('history');
const chrisInput = document.getElementById('chris-input');
const sendBtn = document.getElementById('send-btn');
const claudeResponse = document.getElementById('claude-response');
const claudeTokens = document.getElementById('claude-tokens');
const chatgptResponse = document.getElementById('chatgpt-response');
const chatgptTokens = document.getElementById('chatgpt-tokens');

let currentSessionId = null;

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
    const last = data.rounds[data.rounds.length - 1];
    if (last) {
        showResponses(last);
    } else {
        resetPanels();
    }
    historyDiv.scrollTop = historyDiv.scrollHeight;
}

function startNewSession() {
    currentSessionId = null;
    historyDiv.innerHTML = '<p class="empty-hint">Start a new conversation below — both AIs will answer at the same time.</p>';
    resetPanels();
}

sessionSelect.addEventListener('change', () => {
    if (sessionSelect.value === 'new') {
        startNewSession();
    } else {
        loadSession(sessionSelect.value);
    }
});

exportBtn.addEventListener('click', async () => {
    if (!currentSessionId) {
        alert('No active session to export yet.');
        return;
    }
    const res = await fetch(`/api/sessions/${encodeURIComponent(currentSessionId)}/export`, { method: 'POST' });
    if (!res.ok) {
        alert('Export failed.');
        return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${currentSessionId}.md`;
    a.click();
    URL.revokeObjectURL(url);
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
    startNewSession();
    await refreshSessions();
});

// --- Chat ----------------------------------------------------------------

sendBtn.addEventListener('click', sendMessage);
chrisInput.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'Enter') sendMessage();
});

async function sendMessage() {
    const message = chrisInput.value.trim();
    if (!message) return;

    showLoading();
    sendBtn.disabled = true;

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message, session_id: currentSessionId }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `Request failed (${res.status})`);
        }
        const data = await res.json();

        const isNewSession = !currentSessionId;
        currentSessionId = data.session_id;
        chrisInput.value = '';

        appendRoundToHistory(data);
        showResponses(data);
        historyDiv.scrollTop = historyDiv.scrollHeight;

        if (isNewSession) await refreshSessions();
    } catch (err) {
        claudeResponse.textContent = `Error: ${err.message}`;
        claudeResponse.className = 'response-content error';
        chatgptResponse.textContent = `Error: ${err.message}`;
        chatgptResponse.className = 'response-content error';
    } finally {
        sendBtn.disabled = false;
        chrisInput.focus();
    }
}

// --- Rendering -----------------------------------------------------------

function appendRoundToHistory(round) {
    const hint = historyDiv.querySelector('.empty-hint');
    if (hint) hint.remove();

    const roundEl = document.createElement('div');
    roundEl.className = 'round';

    roundEl.appendChild(messageEl('chris', `Chris (Round ${round.round})`, round.chris_message));

    const pair = document.createElement('div');
    pair.className = 'round-responses';
    pair.appendChild(messageEl('claude', 'Claude', round.claude_response, round.claude_tokens));
    pair.appendChild(messageEl('chatgpt', 'ChatGPT', round.chatgpt_response, round.chatgpt_tokens));
    roundEl.appendChild(pair);

    historyDiv.appendChild(roundEl);
}

function messageEl(who, label, text, tokens) {
    const el = document.createElement('div');
    el.className = `message ${who}`;

    const strong = document.createElement('strong');
    strong.textContent = `${label}:`;
    el.appendChild(strong);
    el.appendChild(document.createTextNode(text));

    if (tokens !== undefined) {
        const t = document.createElement('span');
        t.className = 'tokens-inline';
        t.textContent = `tokens: ${tokens}`;
        el.appendChild(t);
    }
    return el;
}

function showResponses(round) {
    const claudeIsError = round.claude_response.startsWith('Error:');
    const chatgptIsError = round.chatgpt_response.startsWith('Error:');

    claudeResponse.textContent = round.claude_response;
    claudeResponse.className = claudeIsError ? 'response-content error' : 'response-content';
    claudeTokens.textContent = `tokens: ${round.claude_tokens}`;

    chatgptResponse.textContent = round.chatgpt_response;
    chatgptResponse.className = chatgptIsError ? 'response-content error' : 'response-content';
    chatgptTokens.textContent = `tokens: ${round.chatgpt_tokens}`;
}

function showLoading() {
    claudeResponse.textContent = 'Thinking...';
    claudeResponse.className = 'response-content loading';
    chatgptResponse.textContent = 'Thinking...';
    chatgptResponse.className = 'response-content loading';
    claudeTokens.textContent = 'tokens: --';
    chatgptTokens.textContent = 'tokens: --';
}

function resetPanels() {
    claudeResponse.textContent = '(waiting...)';
    claudeResponse.className = 'response-content';
    chatgptResponse.textContent = '(waiting...)';
    chatgptResponse.className = 'response-content';
    claudeTokens.textContent = 'tokens: --';
    chatgptTokens.textContent = 'tokens: --';
}

// --- Settings ------------------------------------------------------------

const settingsBtn = document.getElementById('settings-btn');
const settingsOverlay = document.getElementById('settings-overlay');
const settingsClose = document.getElementById('settings-close');
const anthropicKeyInput = document.getElementById('set-anthropic-key');
const openaiKeyInput = document.getElementById('set-openai-key');
const anthropicKeyNote = document.getElementById('anthropic-key-note');
const openaiKeyNote = document.getElementById('openai-key-note');
const claudeModelInput = document.getElementById('set-claude-model');
const chatgptModelInput = document.getElementById('set-chatgpt-model');
const hostInput = document.getElementById('set-host');
const portInput = document.getElementById('set-port');
const testResults = document.getElementById('test-results');
const testClaude = document.getElementById('test-claude');
const testChatgpt = document.getElementById('test-chatgpt');
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
    // Keys are never shown in full — the inputs stay blank and the note
    // shows the masked saved value.
    anthropicKeyInput.value = '';
    openaiKeyInput.value = '';
    anthropicKeyInput.placeholder = data.anthropic_api_key_masked || 'sk-ant-...';
    openaiKeyInput.placeholder = data.openai_api_key_masked || 'sk-...';
    anthropicKeyNote.textContent = keyNoteText(data.anthropic_api_key_masked, data.anthropic_key_source);
    openaiKeyNote.textContent = keyNoteText(data.openai_api_key_masked, data.openai_key_source);
    claudeModelInput.value = data.claude_model || '';
    chatgptModelInput.value = data.chatgpt_model || '';
    hostInput.value = data.host || '';
    portInput.value = data.port || '';
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
    const payload = {};
    if (anthropicKeyInput.value.trim()) payload.anthropic_api_key = anthropicKeyInput.value.trim();
    if (openaiKeyInput.value.trim()) payload.openai_api_key = openaiKeyInput.value.trim();
    if (claudeModelInput.value.trim()) payload.claude_model = claudeModelInput.value.trim();
    if (chatgptModelInput.value.trim()) payload.chatgpt_model = chatgptModelInput.value.trim();
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
    testClaude.textContent = 'Claude: testing...';
    testClaude.className = 'test-line pending';
    testChatgpt.textContent = 'ChatGPT: testing...';
    testChatgpt.className = 'test-line pending';
    testKeysBtn.disabled = true;

    // Test keys typed into the form; falls back to the saved/env keys
    const payload = {};
    if (anthropicKeyInput.value.trim()) payload.anthropic_api_key = anthropicKeyInput.value.trim();
    if (openaiKeyInput.value.trim()) payload.openai_api_key = openaiKeyInput.value.trim();

    try {
        const res = await fetch('/api/settings/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        testClaude.textContent = `Claude: ${data.claude.ok ? '✓' : '✗'} ${data.claude.detail}`;
        testClaude.className = `test-line ${data.claude.ok ? 'ok' : 'fail'}`;
        testChatgpt.textContent = `ChatGPT: ${data.chatgpt.ok ? '✓' : '✗'} ${data.chatgpt.detail}`;
        testChatgpt.className = `test-line ${data.chatgpt.ok ? 'ok' : 'fail'}`;
    } catch (err) {
        testClaude.textContent = `Claude: ✗ test request failed (${err.message})`;
        testClaude.className = 'test-line fail';
        testChatgpt.textContent = `ChatGPT: ✗ test request failed (${err.message})`;
        testChatgpt.className = 'test-line fail';
    } finally {
        testKeysBtn.disabled = false;
    }
});

resetSettingsBtn.addEventListener('click', async () => {
    if (!confirm('Delete all saved settings? The app will fall back to .env / environment variables.')) return;
    try {
        const res = await fetch('/api/settings', { method: 'DELETE' });
        const data = await res.json();
        applySettingsSnapshot(data);
        setSettingsStatus('Saved settings cleared — now using .env / environment values.', 'ok');
    } catch (err) {
        setSettingsStatus(`Reset failed: ${err.message}`, 'fail');
    }
});

// --- Init ----------------------------------------------------------------

refreshSessions();
chrisInput.focus();
