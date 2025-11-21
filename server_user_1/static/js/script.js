document.addEventListener('DOMContentLoaded', function() {
  const tabs = document.querySelectorAll('.tab-trigger');
  const tabContents = document.querySelectorAll('.tab-content');

  tabs.forEach(tab => {
    tab.addEventListener('click', function() {
      const tabId = this.getAttribute('data-tab');

      tabs.forEach(t => t.classList.remove('active'));
      tabContents.forEach(c => c.classList.remove('active'));

      this.classList.add('active');
      document.getElementById(tabId).classList.add('active');
    });
  });

  // Активируем первую вкладку по умолчанию
  if (tabs.length > 0 && tabContents.length > 0) {
    tabs[0].classList.add('active');
    tabContents[0].classList.add('active');
  }
});
