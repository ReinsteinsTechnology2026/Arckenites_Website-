# Student account feature audit

| Requested item | Initial state → result | Existing implementation / dependency |
| --- | --- | --- |
| Personal Information | Incomplete → repaired | `PATCH /students/me/profile`, `User`, `StudentProfile`; name/address only, contact changes rejected server side. |
| Profile Photo | Existing → reused | `POST/DELETE /users/me/photo`, `GET /users/{id}/photo`, `core/uploads.py`; clearer status added. |
| Email & Phone | Incomplete → safely limited | `StudentProfile.email/phone`, `/auth/me`; shown read only. Verified contact-change workflow remains a dependency. |
| Student ID Card | Missing → added | Uses `User.student_id`, actual name/photo/assigned program; print view, with empty state if no official ID. |
| My Enrollments | Incomplete → added | `ProgramEnrollment`, `BatchEnrollment`, `Program`, `Batch`; `/students/me/enrollments` returns actual owned rows. |
| Certificates & Achievements | Blocked | No certificate or achievement model, issuance endpoint, or downloadable file in backend. UI explains unavailability. |
| Change Password | Existing → reused/repaired | `POST /auth/change-password`, `change-password.html`, session revocation; readable errors and accurate copy. |
| MFA | Existing → reused | `routes_mfa.py`, `mfa-controls.js`, TOTP/recovery/policy models and session checks. |
| Active Devices & Sessions | Incomplete → repaired | Existing `GET/DELETE /me/sessions` and `AuthSession`; student UI lists sessions and revocation now verifies the current password. |
| Login History | Missing → added | `AuthAuditLog`; `/students/me/login-history` is student scoped and paginated at 20 rows. |
| Privacy Settings | Incomplete → repaired | Existing `PATCH /students/me/notifications` email preference moved to settings; profile visibility has no enforceable backend. |
| Download My Data | Missing → added | `/students/me/data-export` returns bounded JSON for authenticated profile, program and batch memberships; excludes secrets. |
| Deactivate Account | Blocked | Admin disable exists; no approved self-service recovery/reactivation policy. UI explains and links to support. |

No schema changes or migration are needed. Tests and inspection used only the local workspace and test environment; no production environment or database was accessed.
