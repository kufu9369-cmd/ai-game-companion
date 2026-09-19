package ai.game.companion.mobile;

import android.Manifest;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.media.AudioManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.webkit.PermissionRequest;
import android.net.http.SslError;
import android.webkit.SslErrorHandler;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;

import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebChromeClient;
import com.getcapacitor.BridgeWebViewClient;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

public class MainActivity extends BridgeActivity {
    private static final int RC_RECORD_AUDIO = 1001;
    /** 网页 WebView 引用，用于 JS 注入发送图片 */
    private WebView mainWebView;
    private int panelRetries = 0;   // 设置中心打开重试计数

    /**
     * 诊断注入：把网页 console / 全局错误 / 实际生效的 WS 地址转发到原生 logcat（tag=GCWeb）。
     * 仅用于排查，不影响业务逻辑；重复注入有幂等保护。
     */
    private static final String DEBUG_INJECT_JS =
            "(function(){try{"
            + "if(window.__gcDbg)return;window.__gcDbg=1;"
            + "function rep(k,v){try{window.AndroidBridge.log(k,String(v).slice(0,600))}catch(e){}}"
            + "var _l=console.log,_w=console.warn,_e=console.error;"
            + "console.log=function(){rep('log',Array.prototype.join.call(arguments,' '));return _l.apply(console,arguments)};"
            + "console.warn=function(){rep('warn',Array.prototype.join.call(arguments,' '));return _w.apply(console,arguments)};"
            + "console.error=function(){rep('error',Array.prototype.join.call(arguments,' '));return _e.apply(console,arguments)};"
            + "window.onerror=function(m,s,l,c){rep('onerror',m+' @'+(s||'')+':'+l);return false};"
            + "window.addEventListener('unhandledrejection',function(ev){rep('reject',ev.reason&&ev.reason.message||ev.reason)});"
            + "rep('env','href='+location.href);"
            // 自动化测试通道：轮询读取原生侧下发的 JS 命令并执行
            + "window.__gcEval=function(code){try{return String(eval(code))}catch(e){return 'ERR:'+e.message}};"
            + "setInterval(function(){try{var c=window.AndroidBridge.readCmd();"
            + "if(c){var r=window.__gcEval(c);window.AndroidBridge.log('CMD',r);}}catch(e){}},700);"
            + "var _W=window.WebSocket;"
            + "if(_W){window.WebSocket=function(u,p){rep('WS',u);"
            + "var ws=new _W(u,p);"
            + "ws.addEventListener('open',function(){rep('WS-open',u)});"
            + "ws.addEventListener('close',function(ev){rep('WS-close',u+' code='+ev.code+' reason='+ev.reason)});"
            + "ws.addEventListener('error',function(){rep('WS-error',u)});"
            + "return ws};window.WebSocket.prototype=_W.prototype;"
            + "window.WebSocket.CONNECTING=0;window.WebSocket.OPEN=1;window.WebSocket.CLOSING=2;window.WebSocket.CLOSED=3;}"
            + "}catch(e){}})();";

    /**
     * 屏幕共享注入：App 启动页 (app.html) 里有一套「轮询 getLatestFrame 并发送」的逻辑，
     * 但应用随后会导航到电脑端前端页面 (http://IP:12393/)，app.html 的 script 随之销毁，
     * 导致截图永远发不出去（屏幕共享形同虚设）。
     * 因此把轮询逻辑放到原生注入里，每次 onPageFinished 都重新注入，跟随当前页面存活。
     */
    private static final String SCREEN_SHARE_INJECT_JS =
            "(function(){try{"
            + "if(window.__gcScreenInjected)return;window.__gcScreenInjected=1;"
            + "if(!window.AndroidBridge||!window.AndroidBridge.getLatestFrame)return;"
            + "window.__lastSentFrame='';"
            + "function rawWs(){try{var s=window.__gcWsService;if(!s)return null;"
            + "if(s.ws&&s.ws.readyState===1)return s.ws;"
            + "if(s.readyState===1&&typeof s.send==='function')return s;"
            + "return (s.ws||null);}catch(e){return null;}}"
            + "function sendIt(ws,b64){try{ws.send(JSON.stringify({"
            + "type:'text-input',text:'（我共享了手机屏幕，请看画面）',"
            // 后端 create_batch_input 要求 images 为对象数组：
            // {source:'screen'|'camera', data:<dataURL或base64>, mime_type:'image/jpeg'}
            + "images:[{source:'screen',data:'data:image/jpeg;base64,'+b64,mime_type:'image/jpeg'}]}));return true;}catch(e){return false;}}"
            + "window.__gcScreenPoller=setInterval(function(){try{"
            + "var f=window.AndroidBridge.getLatestFrame();"
            + "if(!f||f.length<=100||f===window.__lastSentFrame)return;"
            + "var s=window.__gcWsService;"
            + "var inner=(s&&s.ws)?s.ws:null;"
            + "if(inner&&inner.readyState!==1){return;}"  // 主 WS 未就绪先不发，避免浪费帧
            + "window.__lastSentFrame=f;"
            // 复用主会话 WS，让图片进入当前对话上下文（AI 才能看到画面）
            + "var w=rawWs();"
            + "if(w&&w.readyState===1){sendIt(w,f);"
            + "try{window.AndroidBridge.log('SCREEN','frame sent via main ws, len='+f.length);}catch(e){};return;}"
            // 主 WS 不可用时，退化为独立短连接
            + "var host=location.host;"
            + "var ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+host+'/client-ws'+location.search);"
            + "ws.onopen=function(){sendIt(ws,f);"
            + "try{window.AndroidBridge.log('SCREEN','frame sent via fallback ws, len='+f.length);}catch(e){};"
            + "setTimeout(function(){try{ws.close();}catch(e){}},800);};"
            + "ws.onerror=function(){try{window.AndroidBridge.log('SCREEN','fallback ws error');}catch(e){}};"
            + "}catch(e){}},4000);"
            + "}catch(e){}})();";

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 获取音频焦点（通话模式）：WebView 的 getUserMedia 需要，否则报
        // NotReadableError: Could not start audio source
        try {
            AudioManager audioManager = (AudioManager) getSystemService(AUDIO_SERVICE);
            audioManager.setMode(AudioManager.MODE_IN_COMMUNICATION);
        } catch (Exception e) {
            // 忽略
        }

        // 悬浮窗桌宠按钮（右下角悬浮按钮，点击开启/关闭悬浮窗）
        // 用独立 LinearLayout 横向容器持有三个按钮，再整体 addView 到 decorView 并强制
        // bringToFront，避免按钮被 WebView 覆盖导致点击无反应。
        try {
            // 关键：容器按内容 wrap_content（只包住按钮这一排），并设置 clickable=false，
            // 否则全屏透明层会拦截 WebView 的下层触摸事件。
            LinearLayout btnLayer = new LinearLayout(this);
            btnLayer.setOrientation(LinearLayout.HORIZONTAL);
            FrameLayout.LayoutParams layerLp = new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            layerLp.gravity = Gravity.BOTTOM | Gravity.RIGHT;
            layerLp.setMargins(0, 0, dp(14), dp(130));
            btnLayer.setClickable(false);
            btnLayer.setFocusable(false);
            btnLayer.setFocusableInTouchMode(false);
            btnLayer.setLayoutParams(layerLp);

            Button histBtn = new Button(this);
            histBtn.setText("🕘");
            histBtn.setTextSize(18f);
            histBtn.setAlpha(0.85f);
            histBtn.setOnClickListener(v -> openHistoryPanel());
            btnLayer.addView(histBtn, new LinearLayout.LayoutParams(dp(52), dp(52)));

            Button setBtn = new Button(this);
            setBtn.setText("⚙️");
            setBtn.setTextSize(18f);
            setBtn.setAlpha(0.85f);
            setBtn.setOnClickListener(v -> openSettingsPanel());
            btnLayer.addView(setBtn, new LinearLayout.LayoutParams(dp(52), dp(52)));

            Button floatBtn = new Button(this);
            floatBtn.setText("⛶");
            floatBtn.setTextSize(18f);
            floatBtn.setAlpha(0.85f);
            floatBtn.setOnClickListener(v -> launchFloatWindow());
            btnLayer.addView(floatBtn, new LinearLayout.LayoutParams(dp(52), dp(52)));

            ((ViewGroup) getWindow().getDecorView()).addView(btnLayer, layerLp);
            // 延迟 + 连续 bringToFront：确保 WebView 渲染完成后按钮仍盖在最上层
            btnLayer.post(() -> btnLayer.bringToFront());
            btnLayer.postDelayed(() -> btnLayer.bringToFront(), 500);
            btnLayer.postDelayed(() -> btnLayer.bringToFront(), 2000);
        } catch (Exception e) {
            // 忽略，不阻塞
        }

        if (getBridge() != null && getBridge().getWebView() != null) {
            WebView webView = getBridge().getWebView();
            mainWebView = webView;
            // 暴露原生 latestFrame 给网页（网页 JS 轮询读取发送）
            webView.addJavascriptInterface(new Object() {
                @android.webkit.JavascriptInterface
                public String getLatestFrame() {
                    return ScreenShareService.latestFrame;
                }

                // 网页调用：把一条对话记录到本地历史（与悬浮窗共用 ChatHistory）
                @android.webkit.JavascriptInterface
                public void saveMessage(String role, String text) {
                    try {
                        ChatHistory.append(MainActivity.this, role, text);
                    } catch (Exception ignored) {
                    }
                }

                // 声音同步：网页 JS 定时读取全局静音状态（主页面与悬浮窗双向同步）
                @android.webkit.JavascriptInterface
                public boolean isSoundMuted() {
                    return SoundSync.isMuted(MainActivity.this);
                }

                // 麦克风同步：网页轮询「一次性请求（带序号）」并执行后 ACK + 上报真实状态
                @android.webkit.JavascriptInterface
                public long getMicReqSeq() {
                    return MicSync.reqSeq();
                }

                @android.webkit.JavascriptInterface
                public boolean getMicReqValue() {
                    return MicSync.reqValue();
                }

                @android.webkit.JavascriptInterface
                public void ackMicReq(long seq) {
                    MicSync.ack(seq);
                }

                @android.webkit.JavascriptInterface
                public boolean getMicWant() {
                    return MicSync.reqValue();
                }

                @android.webkit.JavascriptInterface
                public void reportMic(boolean on) {
                    MicSync.report(on);
                }

                // 日志桥：网页 console.log / window.onerror 转发到 logcat（便于真机排查）
                @android.webkit.JavascriptInterface
                public void log(String tag, String msg) {
                    android.util.Log.i("GCWeb", "[" + tag + "] " + msg);
                }

                // 测试桥：读取一个串口文件里的 JS 并返回执行结果（自动化测试用）
                @android.webkit.JavascriptInterface
                public String readCmd() {
                    try {
                        java.io.File f = new java.io.File(getFilesDir(), "gc_cmd.js");
                        if (!f.exists()) return "";
                        byte[] b = new byte[(int) f.length()];
                        java.io.FileInputStream in = new java.io.FileInputStream(f);
                        int n = in.read(b);
                        in.close();
                        if (n > 0) f.delete();
                        return new String(b, 0, Math.max(n, 0), "UTF-8");
                    } catch (Exception e) {
                        return "";
                    }
                }
            }, "AndroidBridge");
            WebSettings settings = webView.getSettings();
            // 兼容 https://localhost 打包页加载 http 局域网资源；主方案已是同源 http://IP:port
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
            settings.setMediaPlaybackRequiresUserGesture(false);
            settings.setDomStorageEnabled(true);
            // 交给 HTTP 缓存头决定：后端对 html/js/css/json 发 no-store（改了立刻生效），
            // 对 /libs/ 下的 WASM + VAD 模型发长缓存（避免每次都重下 12MB 拖慢麦克风启动）。
            settings.setCacheMode(WebSettings.LOAD_DEFAULT);

            // 强制放行 WebView 的媒体权限请求（getUserMedia / 麦克风），
            // 否则页面启动 VAD 时会抛 NotAllowedError: Permission denied。
            webView.setWebChromeClient(new BridgeWebChromeClient(getBridge()) {
                @Override
                public void onPermissionRequest(final PermissionRequest request) {
                    runOnUiThread(() -> request.grant(request.getResources()));
                }
            });

            // 主方案：App 直接加载电脑页面 http://IP:port（同源，无混合内容问题）。
            // 必须继承 BridgeWebViewClient 以保留 Capacitor 本地文件服务器（否则 https://localhost
            // 配置页无法加载，报 ERR_CONNECTION_REFUSED）。
            webView.setWebViewClient(new BridgeWebViewClient(getBridge()) {
                // 关键：页面加载前清掉 localStorage 里残留的旧 wsUrl/baseUrl。
                // 前端 useWebSocket 会读 localStorage["wsUrl"] 覆盖同源默认值，若残留旧 IP/端口
                // （如之前测过的 192.168.x:12393），WS 连错端口 → set-model-and-conf 事件不来
                // → Live2D 人物迟迟不出现（"2分钟才显示"的根因）。让前端始终用同源默认
                // wsUrl（wss://IP:12394/client-ws）。
                @Override
                public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                    super.onPageStarted(view, url, favicon);
                    view.evaluateJavascript(
                            "try{localStorage.removeItem('wsUrl');localStorage.removeItem('baseUrl');"
                            + "localStorage.removeItem('modelInfo');}catch(e){}", null);
                }

                // 声音同步：页面渲染后注入静音补丁，让主页面与悬浮窗共用全局静音状态。
                // 悬浮窗点 🔊/🔇 时，主页面 WebView 的 Audio 也会跟着静音/出声。
                @Override
                public void onPageFinished(WebView view, String url) {
                    super.onPageFinished(view, url);
                    view.postDelayed(() -> view.evaluateJavascript(
                            SoundSync.injectPatchScript(MainActivity.this), null), 600);
                    view.postDelayed(() -> view.evaluateJavascript(
                            SoundSync.injectPatchScript(MainActivity.this), null), 3000);
                    // 注入日志转发 + 实际生效的 WS 地址上报（诊断用）
                    view.postDelayed(() -> view.evaluateJavascript(DEBUG_INJECT_JS, null), 400);
                    // 注入屏幕共享轮询（跟随当前页面存活，导航到电脑端页面后依然生效）
                    view.postDelayed(() -> view.evaluateJavascript(SCREEN_SHARE_INJECT_JS, null), 700);
                    view.postDelayed(() -> view.evaluateJavascript(SCREEN_SHARE_INJECT_JS, null), 2500);
                }

                // 不再注入 localStorage.wsUrl —— 前端已改为从 URL 读取 token 拼 WS 地址
                // （ws://IP:port/client-ws?token=xxx），注入反而会覆盖 URL token 导致连错。
                @Override
                public WebResourceResponse shouldInterceptRequest(
                        WebView view, WebResourceRequest request) {
                    return super.shouldInterceptRequest(view, request);
                }

                // 关键：局域网 http/https 页面必须在 WebView 内部打开，否则 Capacitor 会把
                // 导航交给外部浏览器（这正是之前"进入软件弹出手机自带浏览器"的原因）。
                @Override
                public boolean shouldOverrideUrlLoading(
                        WebView view, WebResourceRequest request) {
                    String url = request.getUrl().toString();
                    android.util.Log.i("GCWeb", "[nav] shouldOverrideUrlLoading " + url);
                    if (url.startsWith("http://") || url.startsWith("https://") ||
                            url.startsWith("ws://") || url.startsWith("wss://")) {
                        saveServerConfig(url); // 捕获电脑 IP/token 供悬浮窗使用
                        return false; // 留在 WebView 内加载
                    }
                    return super.shouldOverrideUrlLoading(view, request);
                }

                // Capacitor 的 launchIntent 会对"不在 allowNavigation 白名单内"的 host
                // 直接抛外部 ACTION_VIEW（弹手机浏览器）并返回 true。这里显式覆写，
                // 对 http(s) 后端地址一律留在 WebView 内加载，彻底避免跳外部浏览器。
                @Override
                public boolean shouldOverrideUrlLoading(WebView view, String url) {
                    android.util.Log.i("GCWeb", "[nav-legacy] " + url);
                    if (url != null && (url.startsWith("http://") || url.startsWith("https://"))) {
                        saveServerConfig(url);
                        return false;
                    }
                    return super.shouldOverrideUrlLoading(view, url);
                }

                // 信任电脑端自签名证书（局域网 https，手机语音需要安全上下文）
                @Override
                public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                    handler.proceed();
                }

                @Override
                public void onReceivedError(WebView view, WebResourceRequest request,
                        android.webkit.WebResourceError error) {
                    super.onReceivedError(view, request, error);
                    // 电脑页面主文档加载失败（后端停止 / 热点 IP 变化）→ 自动回配置页重填
                    if (request != null && request.isForMainFrame()) {
                        String url = request.getUrl().toString();
                        if (url.startsWith("http://") || url.startsWith("https://")) {
                            view.postDelayed(() -> view.loadUrl("https://localhost"), 1200);
                        }
                    }
                }
            });
        }

        // App 打开就主动请求麦克风权限，避免 WebView 页面启动 VAD/getUserMedia 时才申请、
        // 权限弹窗被忽略导致 NotAllowedError: Permission denied
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                    != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(this,
                        new String[]{Manifest.permission.RECORD_AUDIO}, RC_RECORD_AUDIO);
            }
        }

        // 保证露露的 TTS 从外放扬声器出声
        startAudioRouteGuard();

        // 从原生设置页跳转过来：直接打开网页端「设置中心」（人设/形象/声音/提醒）
        handlePanelIntent(getIntent());
    }

    /**
     * 音频路由守卫。
     *
     * 问题：WebView 用 getUserMedia 采集麦克风后，系统音频模式会被切成
     * MODE_IN_COMMUNICATION，此时播放的音频会被归到"通话"通道，
     * 默认走听筒 / 通话音量 → 用户感觉【露露说话完全没有声音】。
     *
     * 做法：只要处于通信模式就强制 setSpeakerphoneOn(true)（音路指向外放扬声器）。
     * AudioManager 是系统服务，进程内其它 WebView（悬浮窗）同样受益。
     */
    private void startAudioRouteGuard() {
        final android.media.AudioManager am =
                (android.media.AudioManager) getSystemService(android.content.Context.AUDIO_SERVICE);
        if (am == null) return;
        final android.os.Handler h = new android.os.Handler(getMainLooper());
        h.postDelayed(new Runnable() {
            @Override
            public void run() {
                try {
                    int mode = am.getMode();
                    if (mode == android.media.AudioManager.MODE_IN_COMMUNICATION
                            || mode == android.media.AudioManager.MODE_IN_CALL) {
                        if (!am.isSpeakerphoneOn()) {
                            am.setSpeakerphoneOn(true);
                            android.util.Log.i("GCWeb", "[audio] 通话音路 → 强制切换为外放扬声器");
                        }
                    }
                } catch (Exception e) {
                    // 忽略
                }
                h.postDelayed(this, 1500);
            }
        }, 1500);
    }

    /** 从电脑页面 URL 捕获 scheme/IP/token/端口，存到 SharedPreferences 供悬浮窗使用。 */
    private void saveServerConfig(String url) {
        try {
            Uri uri = Uri.parse(url);
            String host = uri.getHost();
            // 只排除 Capacitor 自己的本地页（https://localhost）；127.0.0.1 是合法后端地址
            if (host == null || host.equals("localhost")) return;
            String token = uri.getQueryParameter("token");
            String scheme = uri.getScheme();
            boolean https = "https".equalsIgnoreCase(scheme) || "wss".equalsIgnoreCase(scheme);
            int port = uri.getPort() != -1 ? uri.getPort() : (https ? 12394 : 12393);
            getSharedPreferences("gc_float_config", MODE_PRIVATE).edit()
                    .putString("server_ip", host)
                    .putString("token", token == null ? "" : token)
                    .putBoolean("https", https)
                    .putInt("port", port)
                    .apply();
        } catch (Exception e) {
            // 忽略
        }
    }

    /** 开启/关闭悬浮窗桌宠（首次需授权悬浮窗权限）。 */
    private void launchFloatWindow() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(this)) {
                // 引导到系统设置开启悬浮窗权限
                Intent intent = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:" + getPackageName()));
                startActivity(intent);
            } else {
                startService(new Intent(this, FloatWindowService.class));
            }
        } catch (Exception e) {
            // 忽略
        }
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }

    /** 启动原生历史聊天记录页（稳定，不受前端 Drawer 状态影响）。 */
    private void openHistoryPanel() {
        try {
            startActivity(new Intent(this, HistoryActivity.class));
        } catch (Exception e) {
            // 忽略
        }
    }

    /** 启动原生设置页（稳定，不受前端 Drawer 状态影响）。 */
    private void openSettingsPanel() {
        try {
            startActivity(new Intent(this, SettingsActivity.class));
        } catch (Exception e) {
            // 忽略
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        // 悬浮窗控制面板触发屏幕共享
        if (intent != null && "screen_share_toggle".equals(intent.getStringExtra("gc_action"))) {
            toggleScreenShare();
        }
        // 原生设置页跳转到网页端「设置中心」
        if (intent != null) handlePanelIntent(intent);
    }

    /** 处理「打开网页设置中心」的跳转指令（来自原生设置页/悬浮控制条）。 */
    private void handlePanelIntent(Intent intent) {
        if (intent == null || !"open_panel".equals(intent.getStringExtra("gc_action"))) return;
        String tab = intent.getStringExtra("gc_tab");
        openWebSettings(tab == null || tab.length() == 0 ? "persona" : tab);
        // 消费掉，避免之后 onResume 反复触发
        try {
            intent.removeExtra("gc_action");
        } catch (Exception ignored) {
        }
    }

    /**
     * 打开网页端「设置中心」（人设 / 形象 / 声音 / 提醒，与电脑端完全同一套）。
     * 页面从电脑端加载需要几秒，所以轮询等 window.gcOpenSettings 出现再调。
     */
    private void openWebSettings(String tab) {
        final String safe = tab == null ? "persona" : tab.replaceAll("[^a-z]", "");
        if (mainWebView == null) return;
        panelRetries = 12;   // 最多等约 10 秒
        tryOpenPanel(safe);
    }

    private void tryOpenPanel(final String safe) {
        if (mainWebView == null || panelRetries <= 0) return;
        panelRetries--;
        mainWebView.evaluateJavascript(
                "(function(){if(window.gcOpenSettings){window.gcOpenSettings('" + safe
                        + "');return '1';}return '0';})()",
                v -> {
                    if (v != null && v.contains("1")) return;
                    if (panelRetries > 0 && mainWebView != null) {
                        mainWebView.postDelayed(() -> tryOpenPanel(safe), 900);
                    }
                });
    }

    /** 开启/关闭屏幕共享（Android 14 前台服务 MediaProjection）。 */
    public void toggleScreenShare() {
        if (ScreenShareService.running) {
            stopService(new Intent(this, ScreenShareService.class));
            return;
        }
        // 弹系统屏幕捕获授权
        ScreenShareManager.requestPermission(this);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == 2001 && resultCode == RESULT_OK && data != null) {
            // 用前台服务启动屏幕捕获（Android 14 要求），截图存 latestFrame
            Intent si = ScreenShareService.buildStartIntent(data, resultCode);
            si.setClass(this, ScreenShareService.class);
            startForegroundService(si);
            // 注意：不再启动原生 WS 发送 poller —— 截图发送统一由前端页面 JS 完成
            // （index.html 轮询 latestFrame + window.__gcWsService 主 WS 发送），
            // 这样图片能进聊天主会话，AI 才能看到画面。原生通道曾因独立短连接
            // 连不上/图片不进上下文而失效。
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
    }
}
