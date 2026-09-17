"""
Pure enforcement logic for the Arckenites VM Lab Agent, deliberately kept
free of pywin32/Windows-service imports so it can be unit-tested on any
machine (including this dev environment) without touching a real Windows
account, group membership, or session.

UNVERIFIED ON REAL HARDWARE: everything in this module has been exercised
with mocked subprocess calls only. Whether `net localgroup` and `logoff`
behave exactly as assumed here has not been confirmed against a real
Windows lab VM. See windows-lab-agent/README.md.
"""

from __future__ import annotations

import configparser
import logging
import subprocess
import time
import urllib.error
import urllib.request
import json
from dataclasses import dataclass

logger = logging.getLogger("arckenites_lab_agent")

# Accounts enforcement must never touch, regardless of what the server
# reports as the "student" account — a misconfigured VM inventory entry
# must never be able to lock out or log off the machine's own admin.
# Match is case-insensitive; extend via config's [safety] extra_protected_accounts.
DEFAULT_PROTECTED_ACCOUNTS = {"administrator"}

RDP_GROUP_NAME = "Remote Desktop Users"


@dataclass
class AgentConfig:
    server_base_url: str
    agent_token: str
    poll_interval_sec: int
    consecutive_failures_before_failsafe: int
    protected_accounts: set[str]

    @classmethod
    def from_file(cls, path: str) -> "AgentConfig":
        parser = configparser.ConfigParser()
        read_files = parser.read(path)
        if not read_files:
            raise FileNotFoundError(f"Could not read config file: {path}")

        server_base_url = parser.get("server", "base_url").rstrip("/")
        agent_token = parser.get("server", "agent_token")
        poll_interval_sec = parser.getint("agent", "poll_interval_sec", fallback=30)
        consecutive_failures_before_failsafe = parser.getint(
            "agent", "consecutive_failures_before_failsafe", fallback=5
        )
        extra_protected = parser.get("safety", "extra_protected_accounts", fallback="")
        protected = set(DEFAULT_PROTECTED_ACCOUNTS)
        protected.update(a.strip().lower() for a in extra_protected.split(",") if a.strip())

        if not agent_token or agent_token == "REPLACE_WITH_TOKEN_FROM_ADMIN_PANEL":
            raise ValueError(
                "config.ini has no real agent_token set — get one from "
                "Super Admin > VM Lab Management > VM Inventory > Add VM "
                "(shown once at creation) before starting the service."
            )

        return cls(
            server_base_url=server_base_url,
            agent_token=agent_token,
            poll_interval_sec=poll_interval_sec,
            consecutive_failures_before_failsafe=consecutive_failures_before_failsafe,
            protected_accounts=protected,
        )


def is_protected_account(username: str, config: AgentConfig) -> bool:
    return username.strip().lower() in config.protected_accounts


class HeartbeatError(Exception):
    pass


def fetch_heartbeat(config: AgentConfig, timeout: float = 10.0) -> dict:
    """Calls POST /api/lab-vm-agent/heartbeat using this VM's own bearer
    token — never the JWT secret, DB password, or any other global secret.
    Raises HeartbeatError on any network/HTTP failure so the caller can
    apply the grace-period fail-safe policy."""
    url = f"{config.server_base_url}/lab-vm-agent/heartbeat"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.agent_token}",
            "Content-Type": "application/json",
        },
        data=b"{}",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return json.loads(body)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        raise HeartbeatError(str(exc)) from exc


def report_event(config: AgentConfig, action: str, reason: str | None = None, timeout: float = 10.0) -> None:
    """Best-effort audit event report — never allowed to raise, since a
    failed event report must never interrupt the enforcement loop."""
    url = f"{config.server_base_url}/lab-vm-agent/events"
    payload = json.dumps({"action": action, "reason": reason}).encode("utf-8")
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.agent_token}",
            "Content-Type": "application/json",
        },
        data=payload,
    )
    try:
        urllib.request.urlopen(req, timeout=timeout)
    except Exception:
        logger.exception("Failed to report event %s (non-fatal)", action)


# ---------------------------------------------------------------------------
# Local Windows enforcement — every function here shells out to a built-in
# Windows command rather than calling a lower-level API, so its exact
# behavior can be read and verified independently of this codebase (and so
# it can be reviewed by someone who never has to trust pywin32 bindings).
# ---------------------------------------------------------------------------

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    logger.debug("running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15)


def is_member_of_rdp_group(username: str) -> bool:
    result = _run(["net", "localgroup", RDP_GROUP_NAME])
    if result.returncode != 0:
        logger.error("net localgroup query failed: %s", result.stderr)
        return False
    return any(line.strip().lower() == username.strip().lower() for line in result.stdout.splitlines())


def grant_rdp_group_membership(username: str, config: AgentConfig) -> None:
    if is_protected_account(username, config):
        logger.error("Refusing to touch RDP group membership for protected account %r", username)
        return
    if is_member_of_rdp_group(username):
        return
    result = _run(["net", "localgroup", RDP_GROUP_NAME, username, "/add"])
    if result.returncode != 0:
        logger.error("Failed to add %s to %s: %s", username, RDP_GROUP_NAME, result.stderr)
    else:
        logger.info("Added %s to %s", username, RDP_GROUP_NAME)


def revoke_rdp_group_membership(username: str, config: AgentConfig) -> None:
    if is_protected_account(username, config):
        logger.error("Refusing to touch RDP group membership for protected account %r", username)
        return
    if not is_member_of_rdp_group(username):
        return
    result = _run(["net", "localgroup", RDP_GROUP_NAME, username, "/delete"])
    if result.returncode != 0:
        logger.error("Failed to remove %s from %s: %s", username, RDP_GROUP_NAME, result.stderr)
    else:
        logger.info("Removed %s from %s", username, RDP_GROUP_NAME)


def find_active_session_id(username: str) -> str | None:
    """Parses `query user` output to find a logged-in session for this
    username. Returns the session ID string, or None if not currently
    logged in. Format (fixed-width columns, header row first):
      USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME
    """
    result = _run(["query", "user"])
    if result.returncode != 0:
        # A nonzero exit here commonly just means "no one is logged in" —
        # not a real error, so this doesn't get logged as one.
        return None
    lines = result.stdout.splitlines()[1:]
    for line in lines:
        parts = line.strip().lstrip(">").split()
        if not parts:
            continue
        if parts[0].strip().lower() == username.strip().lower():
            # ID is the 3rd column when SESSIONNAME is present, 2nd otherwise
            # (a disconnected session has no SESSIONNAME) — `query user`'s
            # column count varies for exactly this reason.
            for token in parts[1:]:
                if token.isdigit():
                    return token
    return None


def force_logoff(username: str, config: AgentConfig) -> None:
    if is_protected_account(username, config):
        logger.error("Refusing to log off protected account %r", username)
        return
    session_id = find_active_session_id(username)
    if session_id is None:
        return
    result = _run(["logoff", session_id])
    if result.returncode != 0:
        logger.error("Failed to log off session %s (%s): %s", session_id, username, result.stderr)
    else:
        logger.info("Logged off %s (session %s) — access expired/revoked", username, session_id)


# ---------------------------------------------------------------------------
# The poll loop itself
# ---------------------------------------------------------------------------

class LabAgentRunner:
    """Holds the state that must persist across poll iterations: which
    student account currently has (or last had) RDP group membership, and
    the consecutive-heartbeat-failure count that drives the fail-safe
    policy. A plain object (not module globals) so tests can create fresh,
    isolated instances."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self.consecutive_failures = 0
        self.last_known_active_username: str | None = None
        self.stop_requested = False

    def run_forever(self) -> None:
        while not self.stop_requested:
            self.run_once()
            time.sleep(self.config.poll_interval_sec)

    def run_once(self) -> None:
        try:
            heartbeat = fetch_heartbeat(self.config)
            self.consecutive_failures = 0
        except HeartbeatError:
            logger.warning(
                "Heartbeat failed (%s/%s consecutive)",
                self.consecutive_failures + 1,
                self.config.consecutive_failures_before_failsafe,
            )
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.config.consecutive_failures_before_failsafe:
                # Fail-safe: cannot confirm authorization, so assume none.
                # This is a deliberate policy choice (not "instantly lock on
                # any blip") — see README's Network-Failure Policy section.
                if self.last_known_active_username:
                    logger.error(
                        "Grace period exceeded with no server contact — revoking %s as a fail-safe",
                        self.last_known_active_username,
                    )
                    revoke_rdp_group_membership(self.last_known_active_username, self.config)
                    force_logoff(self.last_known_active_username, self.config)
                    self.last_known_active_username = None
            return

        status = heartbeat.get("status")
        student_username = heartbeat.get("student_username")

        if status == "active" and student_username:
            grant_rdp_group_membership(student_username, self.config)
            self.last_known_active_username = student_username
        else:
            if self.last_known_active_username:
                revoke_rdp_group_membership(self.last_known_active_username, self.config)
                force_logoff(self.last_known_active_username, self.config)
                report_event(self.config, "DISCONNECTED", "Access no longer active")
                self.last_known_active_username = None
