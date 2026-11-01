(() => {
  const menu = document.querySelector('.evidence-sidebar-menu');
  if (!menu) return;
  const smallScreen = matchMedia('(max-width:760px)');
  menu.open = !smallScreen.matches;
  smallScreen.addEventListener('change', event => {menu.open = !event.matches;});
})();
