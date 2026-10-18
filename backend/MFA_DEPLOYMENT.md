# TOTP MFA release and recovery notes

This change is an opt-in authenticator-app MFA feature for Portal and Community accounts. It does not use Microsoft Entra or Google sign-in as the second factor. Google sign-in, if configured, also enters the local MFA challenge before an Arckenites session is issued.

## Before release

1. Deploy the code and apply Alembic revision a9d4e7f1c2b6 in staging. The migration adds MFA tables, auth_sessions.mfa_verified, and system_settings.mfa_required=false. It does not enroll users or change their passwords.
2. Generate a dedicated Fernet key offline with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())". Store it as MFA_ENCRYPTION_KEY in a protected server secret store or environment file. Do not put it in Git, logs, browser code, or an issue. Preserve a secure backup: losing the key makes enrolled accounts unable to sign in or use recovery codes. Rotation requires a separate migration that decrypts and re-encrypts every credential and replaces recovery-code hashes.
3. Back up PostgreSQL and the MFA key together. Test restoration in a disposable environment. Keep the API behind HTTPS and restrict access to the server environment file and database backups.
4. Verify a pilot with test Student, Trainer, Community, Admin, and Super Admin accounts. Check QR enrollment, one-time recovery codes, password and Google sign-in, logout, session revocation, disabled accounts, and role isolation.
5. Enroll and verify at least two authorized Super Admins with independently stored recovery codes before enabling the organization policy. POST /api/auth/mfa/policy requires the signed-in Super Admin's password and current MFA factor. The policy is initially off. Turning it on revokes existing password-only sessions and requires unenrolled users to set up MFA during sign-in.

## User recovery

Each enrollment or regeneration displays eight recovery codes once. Each code works once. A user who loses their authenticator can sign in with an unused recovery code and regenerate codes from their own MFA settings. Disabling personal MFA requires the password and an authenticator or recovery code, and is forbidden while organization policy requires MFA. There is no remote administrator bypass or unaudited reset endpoint. A user who loses both device and codes cannot self-recover; an identity-verified, audited recovery process must be designed and approved before mandatory rollout.

## Operational limits

- TOTP uses a 30-second period with a one-step clock tolerance. Keep server time synchronized. A successful code cannot be reused in the same or an earlier step.
- Challenge tickets expire in five minutes, have five attempts, and are stored only as hashes. The API rate limiter is process-local; use an upstream shared rate limit when running multiple workers or instances.
- Recovery codes are stored as keyed HMAC digests and disappear from the UI after enrollment or regeneration. Treat a visible QR code, recovery-code screen, or browser session as sensitive.
- MFA mutation routes use the existing bearer-token Authorization header, not browser cookies, for authenticated actions. The Google callback's state cookie retains its existing SameSite and nonce checks. Reassess CSRF if authentication later moves to cookies.
- The migration downgrade refuses if MFA rows, a required policy, or verified sessions exist. Do not clear those records merely to force a rollback. Restoring the prior release requires an approved retention and recovery plan.
- Local tests and isolated PostgreSQL migration checks do not verify production configuration, email, browser compatibility, or an external MFA provider.
