/* Private document sharing. Storage credentials and URLs never reach the browser. */
(function () {
  'use strict';
  let capabilities;
  function node(tag, text, cls) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }
  function mount(parent, scope, targetId, title) {
    if (!parent || !Number.isInteger(Number(targetId)) || Number(targetId) <= 0) return null;
    const key = scope + ':' + targetId;
    const existing = Array.from(parent.children).find(el => el.dataset.portalFiles === key);
    if (existing) return existing;
    const root = node('details', undefined, 'portal-file-box');
    root.dataset.portalFiles = key;
    root.appendChild(node('summary', title || 'Shared documents'));
    const content = node('div', undefined, 'portal-file-content');
    const status = node('p', '', 'portal-file-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const list = node('ul', undefined, 'portal-file-list');
    const controls = node('div', undefined, 'portal-file-controls');
    const refresh = node('button', 'Refresh', 'btn btn-primary-outline btn-sm');
    refresh.type = 'button';
    controls.appendChild(refresh);
    const isVideo = scope === 'videos';
    const label = node('label', isVideo ? 'Choose an MP4 recording (up to 250 MB)' : 'Choose a file (up to 10 MB)');
    const input = document.createElement('input');
    input.type = 'file'; input.disabled = true;
    label.appendChild(input); label.hidden = true;
    const upload = node('button', 'Upload file', 'btn btn-accent btn-sm');
    upload.type = 'button'; upload.hidden = true; upload.disabled = true;
    controls.append(label, upload); content.append(status, controls, list); root.append(content); parent.append(root);
    const more = node('button', 'Load more files', 'btn btn-primary-outline btn-sm');
    more.type = 'button'; more.hidden = true; content.appendChild(more);
    let nextOffset = null;
    const path = '/portal-files/scopes/' + scope + '/' + Number(targetId);
    let busy = false, loaded = false;
    function state(message, error) {
      status.textContent = message;
      status.classList.toggle('is-error', Boolean(error));
    }
    async function download(file, button) {
      button.disabled = true;
      try {
        const response = await fetch(API_BASE + '/portal-files/' + file.id + '/download', {
          headers: { Authorization: 'Bearer ' + ArckAPI.getToken() }, cache: 'no-store'
        });
        if (response.status === 401) {
          ArckAPI.clearSession();
          if (ArckAPI.onUnauthorized) ArckAPI.onUnauthorized();
        }
        if (!response.ok) throw new Error('Download unavailable. Please refresh or try again.');
        const url = URL.createObjectURL(await response.blob());
        const link = node('a'); link.href = url; link.download = file.file_name;
        document.body.appendChild(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        state('Download ready.');
      } catch (err) { state(err.message || 'Download failed.', true); }
      finally { button.disabled = false; }
    }
    async function load(append = false) {
      if (busy || !root.isConnected) return;
      busy = true; refresh.disabled = true; state('Loading files…');
      try {
        if (!capabilities) capabilities = ArckAPI.request('/portal-files/capabilities').catch(err => { capabilities = null; throw err; });
        const [collection, cap] = await Promise.all([ArckAPI.request(path + (append ? '?offset=' + nextOffset : '')), capabilities]);
        if (!root.isConnected) return;
        loaded = true; if (!append) list.replaceChildren();
        nextOffset = collection.next_offset; more.hidden = nextOffset == null;
        label.hidden = upload.hidden = !collection.can_upload;
        input.accept = isVideo ? '.mp4' : cap.extensions.join(',');
        input.disabled = !cap.uploads_enabled;
        upload.disabled = !cap.uploads_enabled;
        for (const file of collection.files) {
          const item = node('li');
          const button = node('button', file.file_name + ' (' + Math.ceil(file.size_bytes / 1024) + ' KB)', 'portal-file-download');
          button.type = 'button'; button.addEventListener('click', () => download(file, button)); item.appendChild(button);
          if (file.can_delete) {
            const remove = node('button', 'Remove', 'btn btn-primary-outline btn-sm'); remove.type = 'button';
            remove.setAttribute('aria-label', 'Remove ' + file.file_name);
            remove.addEventListener('click', async () => {
              if (!window.confirm('Remove this shared file for everyone in this collection?')) return;
              remove.disabled = true;
              try { await ArckAPI.request('/portal-files/' + file.id, { method: 'DELETE' }); await load(); }
              catch (err) { state(err.detail || 'Could not remove file.', true); remove.disabled = false; }
            });
            item.appendChild(remove);
          }
          if (isVideo) {
            const watch = node('button', 'Watch', 'btn btn-primary-outline btn-sm'); watch.type = 'button';
            watch.addEventListener('click', async () => {
              watch.disabled = true;
              try {
                const playback = await ArckAPI.request('/portal-files/' + file.id + '/playback-token', { method: 'POST' });
                let video = item.querySelector('video');
                if (!video) { video = document.createElement('video'); video.controls = true; video.preload = 'metadata'; item.appendChild(video); }
                video.src = API_BASE + playback.stream_url.replace(/^\/api/, '');
                video.addEventListener('error', () => state('Playback unavailable. Select Watch again to refresh the link.', true), { once: true });
                video.load();
              } catch (err) { state(err.detail || 'Playback unavailable.', true); }
              finally { watch.disabled = false; }
            });
            item.appendChild(watch);
          }
          list.appendChild(item);
        }
        state(!cap.uploads_enabled && collection.can_upload ? 'New uploads are not enabled yet. Existing files can still be downloaded.' : collection.files.length ? '' : 'No shared files yet.');
      } catch (err) { state(err.detail || 'Could not load files. Use Refresh to retry.', true); }
      finally { busy = false; refresh.disabled = false; }
    }
    upload.addEventListener('click', async () => {
      if (!input.files.length || busy) { if (!input.files.length) state('Choose a file first.', true); return; }
      const cap = await capabilities;
      if (input.files[0].size > (isVideo ? cap.video_max_bytes : cap.max_bytes)) { state(isVideo ? 'Maximum recording size is 250 MB.' : 'Maximum file size is 10 MB.', true); return; }
      if (input.files[0].size === 0) { state('Empty files cannot be uploaded.', true); return; }
      busy = true; upload.disabled = true; input.disabled = true; state('Uploading and verifying…');
      try {
        await ArckAPI.uploadFile(path, input.files[0]); input.value = '';
        busy = false; await load();
        if (root.isConnected) state('File uploaded and verified.');
      } catch (err) { state(err.detail || 'Upload failed. Please try again.', true); }
      finally { busy = false; upload.disabled = false; input.disabled = false; }
    });
    refresh.addEventListener('click', () => load());
    more.addEventListener('click', () => load(true));
    root.addEventListener('toggle', () => { if (root.open && !loaded) load(); });
    return root;
  }
  window.PortalFiles = { mount };
})();
