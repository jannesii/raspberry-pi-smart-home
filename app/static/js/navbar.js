// JS (vanilla): toggle the mobile dropdown and handle outside-click / Escape
(() => {
  const nav = document.querySelector('nav.navbar');
  const btn = document.getElementById('menuToggle');
  const menu = document.getElementById('mobileMenu');
  if (!nav || !btn || !menu) return;

  const closeMenu = () => {
    nav.classList.remove('open');
    btn.setAttribute('aria-expanded', 'false');
  };

  btn.addEventListener('click', () => {
    const isOpen = nav.classList.toggle('open');
    btn.setAttribute('aria-expanded', String(isOpen));
  });

  document.addEventListener('click', (e) => {
    if (!nav.classList.contains('open')) return;
    if (!nav.contains(e.target)) closeMenu();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeMenu();
  });
})();
