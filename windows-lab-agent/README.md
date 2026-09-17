# Arckenites VM Lab Agent

A lightweight Windows Service that runs on each lab VM and enforces, on
that machine itself, whatever the Arckenites Hub says about who is
currently authorized to RDP into it. The portal deciding who *should* have
access and this agent enforcing that decision *on the VM* are two
separate halves of one system — hiding the "Connect" button in the portal
is not security by itself; this agent is the other half.

> **Status: UNVERIFIED ON REAL HARDWARE.** This code has been written and
> its enforcement logic (`agent_core.py`) unit-tested with mocked Windows
> commands, but it has not yet been installed or run as an actual Windows
> Service on a real lab VM. Do not treat RDP enforcement as active until
> someone has installed this on a real VM, confirmed the service starts,
> and confirmed a revoked/expired student is actually kicked out.

## 1. What this agent does

Every `poll_interval_sec` (default 30s), the agent:

1. Calls `POST /api/lab-vm-agent/heartbeat` on the Hub, authenticated with
   **this VM's own bearer token** (never the JWT secret, database
   password, Cloudflare token, or any other global secret — a token
   compromised on one VM can never be used to impersonate another VM, a
   student, or an admin).
2. If the response says a student's access is currently `active`, adds
   that student's Windows account to the local **"Remote Desktop Users"**
   group (native Windows RDP allow-list) if not already a member.
3. If the response says `none` (no active grant, expired, or revoked),
   removes that student's account from the "Remote Desktop Users" group,
   and if that account currently has an active RDP session on this
   machine, runs `logoff <session>` to end it immediately.

That's the entire enforcement surface: one group membership, one
`logoff` command, scoped to exactly the account the server named.

## 2. What this agent deliberately never does

- **Never disables RDP globally** on the VM (the RDP service/firewall
  rule for RDP itself is untouched — only group membership changes).
- **Never touches the local `Administrator` account**, or any account
  listed in `config.ini`'s `[safety] extra_protected_accounts` — every
  enforcement function checks this before doing anything, and refuses
  (logging an error instead) if the server-reported username matches a
  protected account. This protects against a misconfigured VM inventory
  entry as much as a malicious one.
- **Never modifies any account's password.**
- **Never logs off or restricts anyone except the one specific student
  account** the Hub currently names — another logged-in session (e.g. an
  admin doing maintenance) is never touched.
- **Never sends the JWT secret, database password, or any other portal
  secret** anywhere — this agent only ever holds its own per-VM token.
- **Never logs passwords or secrets** to `agent.log`.

## 3. Requirements

- Windows 10/11 or Windows Server, with Python 3.9+ installed and on PATH.
- Network access from the VM to the Arckenites Hub's API
  (`https://arckenites.com/api` in production).
- Administrator privileges to install the Windows Service (it needs to
  run as LocalSystem to change local group membership).

## 4. Installation

1. Copy this whole `windows-lab-agent/` folder onto the lab VM.
2. In the Arckenites Hub, as Super Admin: **VM Lab Management → VM
   Inventory → Add VM** (or **Regenerate Token** for an existing entry).
   Copy the token shown — **it is shown exactly once.**
3. Copy `config.ini.example` to `config.ini` and paste that token into
   `agent_token`. Adjust `base_url` if not using production.
4. Open an **elevated** PowerShell prompt on the VM and run:
   ```powershell
   .\install.ps1
   ```
   This installs `pywin32`, registers the service (auto-start), and
   starts it.
5. Confirm it's running: `python service.py status`. Check `agent.log`
   for the startup line.
6. Back in the admin panel, this VM's **Agent** column should switch from
   "Never connected" to "Online" within one poll interval.

## 5. Configuration reference (`config.ini`)

| Section    | Key                                  | Meaning |
|------------|---------------------------------------|---------|
| `[server]` | `base_url`                            | Hub API base URL |
| `[server]` | `agent_token`                         | This VM's own secret, from the admin panel |
| `[agent]`  | `poll_interval_sec`                   | Seconds between heartbeats (default 30) |
| `[agent]`  | `consecutive_failures_before_failsafe`| See §6 below (default 5) |
| `[safety]` | `extra_protected_accounts`            | Comma-separated extra accounts never to touch |

`config.ini` contains a real secret — it is excluded from git via
`.gitignore` and must never be committed.

## 6. Network-failure / fail-safe policy

If the Hub can't be reached, the agent does **not** assume access is
still valid, and it does **not** panic on a single missed check either.
It waits for `consecutive_failures_before_failsafe` consecutive failed
heartbeats (default 5 × 30s = ~2.5 minutes) before treating the situation
as "authorization can no longer be confirmed," at which point it revokes
the last-known student's group membership and logs them off as a
fail-safe. This is a deliberate, configurable grace period — not an
instant lock on a single network blip, and not "assume still authorized
forever" either.

## 7. Logs & troubleshooting

- All activity is logged to `agent.log` next to the script (INFO level:
  grants, revokes, logoffs, heartbeat failures; no secrets are ever
  logged).
- `python service.py status` shows whether the Windows Service itself is
  running.
- If the admin panel keeps showing this VM as "Offline," check: the VM
  can reach `base_url`, the token in `config.ini` matches what the admin
  panel has (a **Regenerate Token** invalidates the old one immediately),
  and `agent.log` for the actual error.
- If enforcement doesn't seem to be applying, check `agent.log` for
  "Refusing to touch ... protected account" — this means the
  server-reported student username matches an entry in
  `extra_protected_accounts` or is `Administrator`, and the agent is
  correctly refusing to act on it as a safety measure. Fix the VM's
  configured student account name instead of removing the protection.

## 8. Uninstalling

Run, elevated:
```powershell
.\uninstall.ps1
```
This stops and removes the Windows Service only. It does not remove any
existing "Remote Desktop Users" membership the agent previously granted —
remove that manually via `net localgroup "Remote Desktop Users"` if this
VM is being decommissioned from the lab pool.

## 9. Testing without a real lab VM

`agent_core.py` contains all enforcement logic with no pywin32/Windows
Service dependency, so its logic (protected-account checks, heartbeat
parsing, the fail-safe counter) can be unit-tested anywhere Python runs,
by mocking `subprocess.run` and the HTTP calls. `service.py` is a thin
wrapper that has not itself been exercised — it requires an actual
Windows Service host to run meaningfully.

## 10. Known limitations / what's not verified yet

- `net localgroup` and `logoff` output parsing has only been read against
  documented Windows command behavior, not run against a real machine —
  minor formatting differences across Windows versions/locales could
  affect the `query user` column parsing in `find_active_session_id`.
- The Windows Service install/start/stop lifecycle (`service.py`) has not
  been run on a real Windows host.
- Whether "Remote Desktop Users" membership takes effect immediately for
  an *already-open* RDP session (vs. only the next connection attempt) has
  not been confirmed — this is why `force_logoff` is also called, so an
  already-open session doesn't linger past its expiration regardless.

Until each of these has been confirmed on a real lab VM, treat RDP
enforcement as **not yet proven**, only implemented.
