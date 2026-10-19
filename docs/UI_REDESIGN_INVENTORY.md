# Arckenites UI inventory and migration plan

Prepared before redesign edits. The primary portal is static HTML, CSS, and JavaScript in `arckenites-orange`. The inauguration microsite is an independent frontend with its own stylesheet. FastAPI in `backend/app` supplies data and authorization; it does not render these pages.

## Page inventory

| Area | Entry points and existing purpose | Shared structure |
| --- | --- | --- |
| Admin and Super Admin | `admin-dashboard.html`; management: `admin-students.html`, `admin-staff.html`, `admin-programs.html`, `admin-program-detail.html`, `admin-batches.html`, `admin-batch-detail.html`, `admin-class-schedule.html`, `admin-meetings.html`, `admin-support.html`, `admin-support-detail.html`, `admin-leads.html`, `admin-community.html`, `admin-interviews.html`, `admin-job-applications.html`, `admin-contact-enquiries.html`; system: `admin-users.html`, `admin-roles.html`, `admin-activity-logs.html`, `admin-settings.html` | Shared sidebar/topbar, `admin-main`, cards, tables, inline forms, modal components. Super Admin is an admin RBAC role, with the same entry page and additional permission gated operations. |
| Student | `student-dashboard.html`, `complete-profile.html`, `change-password.html` | Two dashboard modes: simple layout and institutional sidebar layout. Settings, account, MFA, sessions, and ID card are panels inside `student-dashboard.html`. |
| Staff/Trainer | `staff-dashboard.html`, `staff-batch-workspace.html`, `staff-support.html`, `change-password.html` | Topbar with profile menu, batch cards, tables, profile/settings forms, and MFA controls. |
| Community | `community.html`, `community-register.html`, `community-dashboard.html` | Public landing, staged registration, and a sidebar dashboard using the shared stylesheet. |
| Authentication | `login.html`, `student-login.html`, `staff-login.html`, `admin-login.html` | Portal chooser and role specific forms in `login.html`; legacy pages redirect to those forms. MFA verification and enrollment panels are inside the login page. No public password recovery page exists. |
| Public site | `index.html`, `about.html`, `contact.html`, `courses.html`, `course-details.html`, `corporate-training.html`, `certifications.html`, `certification-program.html`, `placements.html`, `trainers.html`, `careers.html`, `blog.html`, `free-demo.html`, `it360-max.html`, `interview-crack-program.html`, `job-assist-program.html`, `college-projects-program.html`, `privacy-policy.html`, `terms.html`, `404.html` | Shared navbar, hero, footer, content sections, cards and forms. Several feature pages add their own CSS. |
| Shared tools | `meeting-room.html` | Dedicated meeting stylesheet and conference UI. |
| Microsite | `inauguration/index.html` | Independent theme, switch, registration UI, and `inauguration/css/style.css`. |

The Admin sidebar contains disabled placeholders for Companies, Placement Candidates, Placement Statistics, Website Content, and Careers. There are no dedicated pages or reliable metrics for those modules. No new controls or data will be invented for the redesign.

## Existing design and behavior

- `arckenites-orange/css/style.css` is the common visual system for portal, public, login, and community pages. It defines theme tokens, sidebar/topbar, buttons, forms, tables, panels, badges, empty states, chat, and mobile rules.
- `css/meeting-room.css`, `css/it360-max.css`, `css/depth-text.css`, and `css/certification-program.css` supplement specialized pages.
- `js/theme.js` manages the existing light/dark choice. `js/auth.js`, `js/api.js`, and each page module manage role guards, data, actions, and session behavior. The redesign must not change their authorization rules.
- Admin dashboard values come from `/dashboard/stats`, `/dashboard/activity`, and `/admin/system/health`. Student/staff/community pages use their current APIs. Student growth, company totals, and job totals have no verified dashboard source in the current implementation.

## Design migration plan

1. Replace shared dark tokens with the specified charcoal/orange palette while preserving the existing light theme choice.
2. Refine existing shared buttons, sidebar, topbar, cards, forms, tables, modals, notices, and focus/reduced motion rules in the common stylesheet.
3. Give Admin Dashboard a cinematic, data driven welcome panel and a clearer layout for existing metrics, activity, and health; retain current API data and permissions.
4. Apply role appropriate hero and surface treatments to Student, Staff, and Community dashboards without copying admin content or altering navigation.
5. Bring login and public pages, specialized CSS pages, and the independent microsite into the same token family.
6. Check syntax, existing UI/backend tests, viewport behavior, and interaction hooks. Compare to the reference screenshot once it is available.
