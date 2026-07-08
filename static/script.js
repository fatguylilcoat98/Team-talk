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

// --- Init ----------------------------------------------------------------

refreshSessions();
chrisInput.focus();
