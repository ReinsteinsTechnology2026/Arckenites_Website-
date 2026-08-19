document.addEventListener('DOMContentLoaded', async () => {

  const user = await ArckAuth.requireRole('staff');
  if (!user) return; // requireRole already redirected

  document.getElementById('staffWelcome').textContent = `Welcome, ${user.full_name}`;

  document.getElementById('staffLogoutBtn').addEventListener('click', () => {
    ArckAuth.logout();
  });

});
