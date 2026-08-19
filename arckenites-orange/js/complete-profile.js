/* Keep in sync with PROGRAM_LABELS in backend/app/models/student.py */
const PROGRAMS = [
  { value: 'official_certification', label: 'Official Certification Program' },
  { value: 'corporate_training', label: 'Corporate Training Program' },
  { value: 'institutional', label: 'Institutional Program' },
  { value: 'placement_training', label: 'Placement Training Program' },
  { value: 'trainers_program', label: 'Arckenites Trainers Program' },
  { value: 'internship', label: 'Internship Program' },
  { value: 'interview_crack', label: "Interview 'n' Crack Program" },
  { value: 'job_assist', label: 'Arckenites Job Assist Program' },
  { value: 'college_projects', label: 'College Projects Program' },
];

document.addEventListener('DOMContentLoaded', async () => {

  let user;
  try {
    user = await ArckAuth.getCurrentUser();
  } catch (_) {
    return; // onUnauthorized already redirected to login.html
  }

  if (user.role !== 'student') {
    ArckAPI.clearSession();
    window.location.href = 'login.html';
    return;
  }
  if (user.must_change_password) {
    window.location.href = 'change-password.html';
    return;
  }
  if (user.program) {
    window.location.href = 'student-dashboard.html';
    return;
  }

  const nameInput = document.getElementById('profileName');
  const mobileInput = document.getElementById('profileMobile');
  const emailInput = document.getElementById('profileEmail');
  const programSelect = document.getElementById('profileProgram');
  const roleToggle = document.getElementById('roleToggle');
  const errorBox = document.getElementById('completeProfileError');
  const form = document.getElementById('completeProfileForm');
  const submitBtn = form.querySelector('button[type="submit"]');

  nameInput.value = user.full_name || '';

  PROGRAMS.forEach((p) => {
    const opt = document.createElement('option');
    opt.value = p.value;
    opt.textContent = p.label;
    programSelect.appendChild(opt);
  });

  let selectedRole = null;
  roleToggle.querySelectorAll('.role-toggle-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      roleToggle.querySelectorAll('.role-toggle-btn').forEach((b) => b.classList.remove('is-active'));
      btn.classList.add('is-active');
      selectedRole = btn.dataset.value;
    });
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorBox.style.display = 'none';

    if (!selectedRole) {
      errorBox.textContent = 'Please choose your current role — Student or Employer.';
      errorBox.style.display = 'block';
      return;
    }
    if (!programSelect.value) {
      errorBox.textContent = 'Please choose a program to continue.';
      errorBox.style.display = 'block';
      return;
    }

    submitBtn.disabled = true;
    try {
      const updatedUser = await ArckAPI.request('/students/me/profile', {
        method: 'POST',
        body: {
          full_name: nameInput.value.trim(),
          mobile_number: mobileInput.value.trim(),
          email: emailInput.value.trim(),
          current_role: selectedRole,
          program: programSelect.value,
        },
      });
      ArckAuth.redirectToRoleEntryPoint(updatedUser);
    } catch (err) {
      errorBox.textContent = err.detail || 'Could not save your details. Please try again.';
      errorBox.style.display = 'block';
      submitBtn.disabled = false;
    }
  });

});
