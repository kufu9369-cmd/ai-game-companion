import type { CapacitorConfig } from '@capacitor/cli'

const config: CapacitorConfig = {
  appId: 'ai.game.companion.mobile',
  appName: 'AI游戏搭子',
  webDir: 'www',
  android: {
    allowMixedContent: true
  },
  server: {
    // 允许 WebView 导航到后端页面（同源加载，避免混合内容拦截资源）。
    // 用具体网段而非 '*'，避免影响 Capacitor 本地配置页(https://localhost)。
    // 注意：必须包含 127.0.0.1 —— adb reverse 端口转发 / 本机后端场景下，
    // 后端地址就是 127.0.0.1:12393。此前缺失该条目会导致 WebView 拒绝导航，
    // 页面永远停在 Capacitor 的 https://localhost 配置页（"点了没反应"的根因）。
    allowNavigation: [
      '127.0.0.1',
      'localhost',
      '192.168.*',
      '10.*',
      '172.16.*', '172.17.*', '172.18.*', '172.19.*', '172.20.*',
      '172.21.*', '172.22.*', '172.23.*', '172.24.*', '172.25.*',
      '172.26.*', '172.27.*', '172.28.*', '172.29.*', '172.30.*', '172.31.*'
    ]
  }
}

export default config
