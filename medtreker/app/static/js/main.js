
document.addEventListener('DOMContentLoaded', function () {
  var flashes = document.querySelectorAll('.flash-msg');
  flashes.forEach(function (f) {
    setTimeout(function () {
      f.style.transition = 'opacity .4s';
      f.style.opacity = '0';
      setTimeout(function () { f.remove(); }, 400);
    }, 4000);
  });
});

// Подтверждение удаления
function confirmDelete(msg) {
  return confirm(msg || 'Вы уверены?');
}

// Форматирование даты для отображения
function formatDate(d) {
  var months = ['января','февраля','марта','апреля','мая','июня',
                'июля','августа','сентября','октября','ноября','декабря'];
  var dt = new Date(d);
  return dt.getDate() + ' ' + months[dt.getMonth()] + ' ' + dt.getFullYear();
}
