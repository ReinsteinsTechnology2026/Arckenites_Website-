"""READ-ONLY check of what the existing Microsoft Graph connection may do with files.

Run it where the production Graph credentials already live (it uses the same
settings the email sender uses), e.g.:

    cd backend && ./venv/bin/python -m maintenance.check_onedrive_access
    ./venv/bin/python -m maintenance.check_onedrive_access --probe-drive

What it does:
  1. Acquires a Graph token exactly as the email code does (client-credentials
     flow, i.e. APPLICATION permissions - there is no signed-in user).
  2. Decodes the token locally (no signature check, nothing sent anywhere) and
     lists the granted application roles, so you can see whether any file
     permission has been consented. `Mail.Send` does NOT grant file access.
  3. With --probe-drive: ONE read-only GET of the sender mailbox's drive
     (id/type/quota). No file listing, no upload, no folder creation, no
     sharing link.

It never prints the client secret, the access token, or any file content, and
it changes nothing in the tenant.
"""
import argparse
import sys

import httpx
import jwt

from app.config import settings
from app.core import ms_graph_email

FILE_WRITE_ROLES = {
    "Files.ReadWrite.All": "write to every drive in the tenant (broadest)",
    "Sites.ReadWrite.All": "write to every SharePoint site and its drives",
    "Sites.FullControl.All": "full control of every site (far more than needed)",
}
FILE_SCOPED_ROLE = "Sites.Selected"  # only usable once an admin grants it on a specific site/drive
FILE_READ_ROLES = {"Files.Read.All", "Sites.Read.All"}


def classify_roles(roles: list[str]) -> dict:
    """Pure function: what the granted application roles allow."""
    granted = set(roles)
    writers = sorted(granted & set(FILE_WRITE_ROLES))
    return {
        "can_send_mail": "Mail.Send" in granted,
        "file_write_roles": writers,
        "scoped_file_role": FILE_SCOPED_ROLE in granted,
        "file_read_roles": sorted(granted & FILE_READ_ROLES),
        "can_upload_files": bool(writers),  # Sites.Selected alone proves nothing until granted on a site
        "unrelated_roles": sorted(granted - {"Mail.Send", FILE_SCOPED_ROLE, *FILE_WRITE_ROLES, *FILE_READ_ROLES}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe-drive", action="store_true", help="one read-only GET of the sender's drive")
    args = parser.parse_args(argv)

    print("Microsoft Graph configured:", settings.ms_graph_configured)
    if not settings.ms_graph_configured:
        print("Nothing to check: set the four MS_GRAPH_* values in this environment first.")
        return 2

    token = ms_graph_email._acquire_token()
    if token is None:
        print("Could not acquire a token (see the 'ms_graph_email' log line for the error code).")
        return 1

    claims = jwt.decode(token, options={"verify_signature": False})
    roles = sorted(claims.get("roles", []))
    delegated = claims.get("scp")
    print("Permission model:", "DELEGATED (signed-in user)" if delegated else "APPLICATION (app-only, no user)")
    print("Granted application roles:", ", ".join(roles) if roles else "(none)")

    verdict = classify_roles(roles)
    print("Mail.Send granted:", verdict["can_send_mail"])
    print("File-write roles granted:", verdict["file_write_roles"] or "NONE")
    if verdict["file_write_roles"]:
        for role in verdict["file_write_roles"]:
            print(f"  - {role}: {FILE_WRITE_ROLES[role]}")
    elif verdict["scoped_file_role"]:
        print("Sites.Selected is present: uploads work only to sites/drives an admin explicitly granted. "
              "Use --probe-drive or a test grant to confirm the destination.")
    else:
        print("=> This connection CANNOT upload files today. An administrator must consent a file permission.")
        print("   Least privilege: grant 'Sites.Selected' and then grant this app write access to ONE")
        print("   destination site/drive only. 'Files.ReadWrite.All' also works but covers every drive.")

    if args.probe_drive:
        url = f"https://graph.microsoft.com/v1.0/users/{settings.ms_graph_sender_email}/drive?$select=id,driveType,quota"
        try:
            response = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        except httpx.HTTPError as exc:
            print("Drive probe failed to connect:", type(exc).__name__)
            return 1
        print("Drive probe HTTP status:", response.status_code)
        if response.status_code == 200:
            body = response.json()
            quota = body.get("quota", {})
            print("  driveType:", body.get("driveType"), "| quota state:", quota.get("state"),
                  "| remaining bytes:", quota.get("remaining"))
        else:
            code = response.json().get("error", {}).get("code") if response.headers.get("content-type", "").startswith("application/json") else None
            print("  error code:", code, "(an authorization error here means no file permission is consented)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
