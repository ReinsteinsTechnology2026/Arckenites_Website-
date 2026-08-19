document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('student');
  if (!user) return; // requireRole already redirected

  document.getElementById('studentWelcome').textContent = `Welcome, ${user.full_name}`;

  document.getElementById('studentLogoutBtn').addEventListener('click', () => {
    ArckAuth.logout();
  });

});
