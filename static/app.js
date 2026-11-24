// 공통: 전체선택 체크박스, 성공 플래시 자동 닫힘, 모바일 사이드바 닫기
document.querySelectorAll('[data-check-all]').forEach(function (m) {
  m.addEventListener('change', function () {
    var name = m.getAttribute('data-check-all');
    m.closest('form').querySelectorAll('input[type=checkbox][name="' + name + '"]:not([disabled])').forEach(function (c) { c.checked = m.checked; });
  });
});
setTimeout(function () { document.querySelectorAll('.flash.ok').forEach(function (f) { f.style.transition = 'opacity .6s'; f.style.opacity = '0'; setTimeout(function () { f.remove(); }, 700); }); }, 5000);
document.addEventListener('click', function (e) {
  if (document.body.classList.contains('sb-open') && !e.target.closest('#sidebar') && !e.target.closest('.menu-btn')) document.body.classList.remove('sb-open');
});
