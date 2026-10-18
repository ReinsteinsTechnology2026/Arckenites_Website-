/* Personal MFA controls shared by Admin Security, Student/Trainer Profile, and Community. */
document.addEventListener('DOMContentLoaded', async () => {
  const root = document.getElementById('mfaControls');
  if (!root) return;
  let state;
  const message = (value, error = false) => {
    const el = root.querySelector('#mfaMessage');
    el.textContent = value;
    el.style.color = error ? '#c74343' : '';
  };
  const codes = (list) => {
    const panel = root.querySelector('#mfaRecoveryOutput');
    panel.style.display = 'block';
    panel.querySelector('pre').textContent = list.join('\n');
  };
  const render = () => {
    const superAdmin = ArckAPI.getStoredUser()?.admin_role?.slug === 'super_admin';
    root.innerHTML = `
      <p>Use an authenticator app to protect your account even if your password is known.</p>
      <p><strong>Status: <span id="mfaStatus"></span></strong></p>
      <p id="mfaPolicyNote"></p>
      <button type="button" class="btn btn-accent" data-mfa-action="toggle"></button>
      <div id="mfaSetup" style="display:none; margin-top:16px;">
        <p>Enter your current password, scan the QR code with Google Authenticator or another TOTP app, then enter its six-digit code. Setup is active only after confirmation.</p>
        <input type="password" class="form-control" id="mfaSetupPassword" placeholder="Current password" autocomplete="current-password">
        <button type="button" class="btn btn-primary-outline mt-2" data-mfa-action="begin">Generate QR code</button>
        <div id="mfaQrPanel" style="display:none; margin-top:16px;">
          <img id="mfaQr" alt="Authenticator setup QR code" style="max-width:220px;">
          <input type="text" class="form-control mt-2" id="mfaSetupCode" placeholder="Six-digit authenticator code" autocomplete="one-time-code">
          <button type="button" class="btn btn-accent mt-2" data-mfa-action="confirm">Confirm MFA setup</button>
        </div>
      </div>
      <div id="mfaDisable" style="display:none; margin-top:16px;">
        <p>Disabling MFA signs out every device. Enter your current password and an authenticator or recovery code.</p>
        <input type="password" class="form-control" id="mfaDisablePassword" placeholder="Current password" autocomplete="current-password">
        <input type="text" class="form-control mt-2" id="mfaDisableCode" placeholder="Authenticator or recovery code" autocomplete="one-time-code">
        <button type="button" class="btn btn-primary-outline is-danger mt-2" data-mfa-action="disable">Confirm disable</button>
      </div>
      <div id="mfaRegenerate" style="display:none; margin-top:20px;">
        <h3>Recovery codes</h3>
        <p><span id="mfaRemaining"></span> codes remaining. Each works once. Save new codes offline; they appear only when generated.</p>
        <input type="password" class="form-control" id="mfaRegeneratePassword" placeholder="Current password" autocomplete="current-password">
        <input type="text" class="form-control mt-2" id="mfaRegenerateCode" placeholder="Authenticator or recovery code" autocomplete="one-time-code">
        <button type="button" class="btn btn-primary-outline mt-2" data-mfa-action="regenerate">Generate new recovery codes</button>
      </div>
      <div id="mfaRecoveryOutput" class="dashboard-notice" style="display:none; margin-top:16px;">
        <strong>Save these recovery codes now. They will not appear again.</strong><pre style="white-space:pre-wrap;"></pre>
        <button type="button" class="btn btn-primary-outline" data-mfa-action="hide-codes">I saved these codes</button>
      </div>
      ${superAdmin ? `<div id="mfaOrganization" style="margin-top:28px; border-top:1px solid var(--border-c); padding-top:16px;">
        <h3>Organization MFA policy</h3>
        <p>This policy applies to all interactive roles. Unenrolled users must set up MFA before receiving a portal session.</p>
        <input type="password" class="form-control" id="mfaPolicyPassword" placeholder="Current password" autocomplete="current-password">
        <input type="text" class="form-control mt-2" id="mfaPolicyCode" placeholder="Authenticator or recovery code" autocomplete="one-time-code">
        <button type="button" class="btn btn-primary-outline mt-2" data-mfa-action="policy"></button>
      </div>` : ''}
      <p id="mfaMessage" role="status" style="margin-top:12px;"></p>`;
    root.querySelector('#mfaStatus').textContent = state.enabled ? 'Enabled' : 'Disabled';
    root.querySelector('#mfaPolicyNote').textContent = state.required_by_policy
      ? 'Organization policy requires MFA. Personal disabling is unavailable.'
      : 'Your personal MFA setting is optional under the current organization policy.';
    const toggle = root.querySelector('[data-mfa-action="toggle"]');
    toggle.textContent = state.enabled ? 'Disable MFA' : 'Enable MFA';
    toggle.disabled = state.enabled && state.required_by_policy;
    root.querySelector('#mfaRegenerate').style.display = state.enabled ? 'block' : 'none';
    root.querySelector('#mfaRemaining').textContent = state.recovery_codes_remaining;
    const policy = root.querySelector('[data-mfa-action="policy"]');
    if (policy) policy.textContent = state.required_by_policy ? 'Turn off mandatory MFA' : 'Require MFA for all users';
  };
  const load = async () => {
    state = await ArckAPI.request('/auth/mfa/status');
    render();
  };
  try { await load(); } catch (_) { root.textContent = 'Could not load MFA settings.'; return; }
  root.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-mfa-action]');
    if (!button) return;
    const action = button.dataset.mfaAction;
    if (action === 'toggle') {
      root.querySelector(state.enabled ? '#mfaDisable' : '#mfaSetup').style.display = 'block';
      return;
    }
    if (action === 'hide-codes') {
      root.querySelector('#mfaRecoveryOutput pre').textContent = '';
      root.querySelector('#mfaRecoveryOutput').style.display = 'none';
      await load();
      return;
    }
    if (action === 'disable' && !window.confirm('Disable MFA and sign out every device?')) return;
    if (action === 'policy' && !window.confirm(state.required_by_policy
      ? 'Turn off the organization MFA requirement?' : 'Require MFA for every interactive user?')) return;
    button.disabled = true;
    try {
      if (action === 'begin') {
        const password = root.querySelector('#mfaSetupPassword').value;
        const setup = await ArckAPI.request('/auth/mfa/setup/start', {method: 'POST', body: {password}});
        root.querySelector('#mfaSetupPassword').value = '';
        root.querySelector('#mfaQr').src = setup.qr_code;
        root.querySelector('#mfaQrPanel').style.display = 'block';
        message('Scan the QR code, then enter the current code.');
      } else if (action === 'confirm') {
        const code = root.querySelector('#mfaSetupCode').value.trim();
        const result = await ArckAPI.request('/auth/mfa/setup/confirm', {method: 'POST', body: {code}});
        await load();
        codes(result.recovery_codes);
      } else if (action === 'disable') {
        const password = root.querySelector('#mfaDisablePassword').value;
        const code = root.querySelector('#mfaDisableCode').value.trim();
        await ArckAPI.request('/auth/mfa/disable', {method: 'POST', body: {password, code}});
        ArckAPI.clearSession();
        window.location.href = 'login.html';
      } else if (action === 'regenerate') {
        const password = root.querySelector('#mfaRegeneratePassword').value;
        const code = root.querySelector('#mfaRegenerateCode').value.trim();
        const result = await ArckAPI.request('/auth/mfa/recovery/regenerate', {method: 'POST', body: {password, code}});
        await load();
        codes(result.recovery_codes);
      } else if (action === 'policy') {
        const password = root.querySelector('#mfaPolicyPassword').value;
        const code = root.querySelector('#mfaPolicyCode').value.trim();
        await ArckAPI.request('/auth/mfa/policy', {method: 'POST',
          body: {password, code, required: !state.required_by_policy}});
        await load();
        message('Organization MFA policy updated. Existing password-only sessions are revoked when enabled.');
      }
    } catch (err) {
      message(err.detail || 'MFA action failed. Try again.', true);
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  });
});
