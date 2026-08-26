document.addEventListener('DOMContentLoaded', () => {

  const token = ArckAPI.getToken();
  const trigger = document.getElementById('chatFloatBtn') || document.getElementById('chatTopbarTrigger');
  if (!token || !trigger) return; // not logged in, or this page doesn't include the chat widget

  const isFloating = !!document.getElementById('chatFloatBtn');
  const badge = document.getElementById('chatBadge');
  const backdrop = document.getElementById('chatBackdrop');
  const panel = document.getElementById('chatPanel');
  const closeBtn = document.getElementById('chatClose');
  const backBtn = document.getElementById('chatBack');
  const titleEl = document.getElementById('chatTitle');
  const listView = document.getElementById('chatListView');
  const threadView = document.getElementById('chatThreadView');
  const searchInput = document.getElementById('chatSearchInput');
  const searchResultsEl = document.getElementById('chatSearchResults');
  const conversationListEl = document.getElementById('chatConversationList');
  const messagesEl = document.getElementById('chatMessages');
  const form = document.getElementById('chatForm');
  const input = document.getElementById('chatInput');

  if (isFloating) panel.classList.add('chat-panel--floating');

  let conversations = [];
  let activePeerId = null;
  let ws = null;
  let reconnectDelay = 2000;
  let searchDebounce = null;

  const wsUrl = () => {
    const base = new URL(API_BASE, window.location.href);
    const proto = base.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${base.host}${base.pathname}/chat/ws?token=${encodeURIComponent(ArckAPI.getToken())}`;
  };

  const escapeHtml = (str) => str.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const roleLabel = (role) => (role ? role.charAt(0).toUpperCase() + role.slice(1) : '');

  const totalUnread = () => conversations.reduce((sum, c) => sum + c.unread_count, 0);

  const updateBadge = () => {
    const count = totalUnread();
    if (count > 0) {
      badge.textContent = count > 9 ? '9+' : String(count);
      badge.style.display = '';
    } else {
      badge.style.display = 'none';
    }
  };

  const relativeTime = (iso) => {
    if (!iso) return '';
    const diffMs = Date.now() - new Date(iso).getTime();
    const mins = Math.round(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.round(hrs / 24)}d ago`;
  };

  const renderConversationList = () => {
    conversationListEl.innerHTML = conversations.length
      ? conversations.map((c) => `
          <button type="button" class="chat-conv-item" data-peer-id="${c.other_user_id}" data-peer-name="${escapeHtml(c.other_name)}">
            ${ArckAPI.avatarHtml(c.other_name, c.other_photo_url, 40)}
            <div class="chat-conv-item-main">
              <strong>${escapeHtml(c.other_name)}${c.other_role === 'admin' ? ' <span class="chat-pin-badge">Admin</span>' : ''}</strong>
              <span class="chat-conv-preview">${c.last_message ? escapeHtml(c.last_message) : (c.is_pinned_admin ? 'Say hello' : 'No messages yet')}</span>
            </div>
            <div class="chat-conv-item-meta">
              <span class="chat-conv-time">${relativeTime(c.last_message_at)}</span>
              ${c.unread_count > 0 ? `<span class="admin-notif-badge admin-chat-unread">${c.unread_count > 9 ? '9+' : c.unread_count}</span>` : ''}
            </div>
          </button>
        `).join('')
      : '<div class="admin-panel-empty">No conversations yet. Search a name above to start one.</div>';
  };

  const loadConversations = async () => {
    try {
      conversations = await ArckAPI.request('/chat/conversations');
    } catch (_) {
      conversationListEl.innerHTML = '<div class="admin-panel-empty">Couldn\'t load conversations.</div>';
      conversations = [];
    }
    renderConversationList();
    updateBadge();
  };

  const appendMessage = (msg) => {
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${msg.is_me ? 'is-me' : 'is-them'}`;
    const time = new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const avatar = msg.is_me ? '' : ArckAPI.avatarHtml(msg.sender_name, msg.sender_photo_url, 18);
    bubble.innerHTML = `
      <div class="chat-bubble-meta" style="display:flex;align-items:center;gap:6px;">${avatar}<span>${msg.is_me ? 'You' : escapeHtml(msg.sender_name)} &middot; ${time}</span></div>
      <div class="chat-bubble-body">${escapeHtml(msg.body)}</div>
    `;
    messagesEl.appendChild(bubble);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  };

  const openThread = async (peerId, peerName) => {
    activePeerId = peerId;
    titleEl.textContent = peerName;
    backBtn.style.display = '';
    listView.style.display = 'none';
    threadView.style.display = 'flex';
    messagesEl.innerHTML = '<div class="admin-panel-empty">Loading&hellip;</div>';
    searchInput.value = '';
    searchResultsEl.style.display = 'none';

    try {
      const history = await ArckAPI.request(`/chat/messages/${peerId}`);
      messagesEl.innerHTML = '';
      if (!history.length) {
        messagesEl.innerHTML = '<div class="admin-panel-empty">Send a message to start the conversation.</div>';
      } else {
        history.forEach(appendMessage);
      }
    } catch (_) {
      messagesEl.innerHTML = '<div class="admin-panel-empty">Couldn\'t load this conversation.</div>';
    }

    const c = conversations.find((x) => x.other_user_id === peerId);
    if (c) c.unread_count = 0;
    updateBadge();
    input.focus();
  };

  const backToList = () => {
    activePeerId = null;
    titleEl.textContent = 'Messages';
    backBtn.style.display = 'none';
    threadView.style.display = 'none';
    listView.style.display = 'block';
    renderConversationList();
  };

  conversationListEl.addEventListener('click', (e) => {
    const item = e.target.closest('.chat-conv-item');
    if (!item) return;
    openThread(Number(item.dataset.peerId), item.dataset.peerName);
  });

  backBtn.addEventListener('click', backToList);

  const renderSearchResults = (results) => {
    searchResultsEl.innerHTML = results.length
      ? results.map((u) => `
          <button type="button" class="chat-conv-item" data-peer-id="${u.id}" data-peer-name="${escapeHtml(u.full_name)}">
            ${ArckAPI.avatarHtml(u.full_name, u.photo_url, 40)}
            <div class="chat-conv-item-main">
              <strong>${escapeHtml(u.full_name)}</strong>
              <span class="chat-conv-preview">${roleLabel(u.role)}</span>
            </div>
          </button>
        `).join('')
      : '<div class="admin-panel-empty">No matching users.</div>';
    searchResultsEl.style.display = 'block';
  };

  searchInput.addEventListener('input', () => {
    const q = searchInput.value.trim();
    clearTimeout(searchDebounce);
    if (!q) {
      searchResultsEl.style.display = 'none';
      searchResultsEl.innerHTML = '';
      return;
    }
    searchDebounce = setTimeout(async () => {
      try {
        const results = await ArckAPI.request(`/chat/users/search?q=${encodeURIComponent(q)}`);
        renderSearchResults(results);
      } catch (_) {
        searchResultsEl.innerHTML = '<div class="admin-panel-empty">Search failed.</div>';
        searchResultsEl.style.display = 'block';
      }
    }, 300);
  });

  searchResultsEl.addEventListener('click', (e) => {
    const item = e.target.closest('.chat-conv-item');
    if (!item) return;
    openThread(Number(item.dataset.peerId), item.dataset.peerName);
  });

  const openPanel = () => {
    panel.classList.add('is-open');
    if (!isFloating) backdrop.classList.add('is-visible');
    trigger.classList.add('is-active');
  };
  const closePanel = () => {
    panel.classList.remove('is-open');
    backdrop.classList.remove('is-visible');
    trigger.classList.remove('is-active');
  };

  trigger.addEventListener('click', () => {
    if (panel.classList.contains('is-open')) closePanel(); else openPanel();
  });
  closeBtn.addEventListener('click', closePanel);
  backdrop.addEventListener('click', closePanel);

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || !activePeerId || !ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ recipient_id: activePeerId, body: text }));
    input.value = '';
  });

  const connect = () => {
    ws = new WebSocket(wsUrl());

    ws.addEventListener('open', () => { reconnectDelay = 2000; });

    ws.addEventListener('message', (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (_) {
        return;
      }
      if (data.type !== 'message') {
        // Not a 1:1 chat frame (e.g. batch group chat) — hand it off via a
        // DOM event so other pages can react without opening a second
        // socket; this one connection is shared org-wide.
        window.dispatchEvent(new CustomEvent('arck-ws-message', { detail: data }));
        return;
      }

      const peerId = data.peer_id;
      let c = conversations.find((x) => x.other_user_id === peerId);
      if (!c) {
        const inferredName = data.message.is_me ? titleEl.textContent : data.message.sender_name;
        c = {
          other_user_id: peerId, other_name: inferredName, other_role: '', other_photo_url: null,
          last_message: null, last_message_at: null, unread_count: 0, is_pinned_admin: false,
        };
        conversations.unshift(c);
      }
      c.last_message = data.message.body;
      c.last_message_at = data.message.created_at;
      c.is_pinned_admin = false;
      conversations = [c, ...conversations.filter((x) => x !== c)];

      if (activePeerId === peerId) {
        appendMessage(data.message);
      } else if (!data.message.is_me) {
        c.unread_count += 1;
      }

      if (!activePeerId) renderConversationList();
      updateBadge();
    });

    ws.addEventListener('close', () => {
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 1.5, 15000);
    });

    ws.addEventListener('error', () => ws.close());
  };

  loadConversations();
  connect();

});
