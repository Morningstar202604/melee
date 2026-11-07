/* 主题引导：必须在 CSS 绘制前同步执行，否则会先闪一帧默认亮色再跳到深色。
   必须写成经典脚本（非 module）：module 是延迟执行，等它跑完页面已经画完了。
   CSP 只放行同源脚本，所以这里不能内联，只能外链一个极小的同步脚本。 */
(function () {
  var KEY = 'melee.theme'
  var DEFAULT = 'light'
  var root = document.documentElement

  function normalize(value) {
    return value === 'dark' || value === 'light' ? value : ''
  }

  var theme = ''
  try {
    var params = new URLSearchParams(location.search)
    theme = normalize(params.get('theme'))
    if (!theme) theme = normalize(localStorage.getItem(KEY))
  } catch (error) {
    /* 隐私模式 / 无痕窗口下 localStorage 与 URLSearchParams 都可能抛，退回默认 */
  }
  /* 默认亮色是产品决定，不跟随系统深色偏好：转播台的第一屏要在任何机器上
     都是同一个亮场演播室。要深色的人点一次按钮，选择会被记住。 */
  root.setAttribute('data-theme', theme || DEFAULT)
  /* 这一行是给浏览器画滚动条与原生控件用的，和 data-theme 必须同步。 */
  root.style.colorScheme = theme || DEFAULT
})()
