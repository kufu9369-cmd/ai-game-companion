package ai.game.companion.mobile;

import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.PixelFormat;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.IBinder;
import android.provider.Settings;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.PermissionRequest;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/**
 * 手机悬浮窗桌宠（v3 优化）：
 * - 只显示人物上半身（隐藏聊天框/输入栏/侧栏/背景，canvas 放大截取上半身）
 * - 可拖动（底部拖动把手）、可自由缩放（右下角把手）
 * - 点击人物弹出控制面板：声音开关 / 屏幕共享 / 关闭
 */
public class FloatWindowService extends Service {

    private static final String PREFS = "gc_float_config";
    public static final String ACTION_STOP = "ai.game.companion.mobile.FLOAT_STOP";

    private WindowManager windowManager;
    private FrameLayout container;
    private WebView floatWebView;
    private LinearLayout controlPanel;
    private WindowManager.LayoutParams params;

    private int startX, startY, startTouchX, startTouchY;
    private int startW, startH;
    private boolean panelVisible = false;

    @Override
    public void onCreate() {
        super.onCreate();
        windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (container == null) {
            buildFloatWindow();
        } else {
            floatWebView.loadUrl(serverUrl());
        }
        return START_STICKY;
    }

    private String serverUrl() {
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        String ip = prefs.getString("server_ip", "");
        String token = prefs.getString("token", "");
        if (ip == null || ip.isEmpty()) {
            return "about:blank";
        }
        // 与主界面保持同一协议：必须走 HTTPS，悬浮窗内的语音(麦克风)和屏幕共享
        // 才有 secure context；HTTP 下 WebView 不提供 navigator.mediaDevices。
        boolean https = prefs.getBoolean("https", true);
        int port = prefs.getInt("port", https ? 12394 : 12393);
        String base = (https ? "https://" : "http://") + ip + ":" + port + "/";
        StringBuilder q = new StringBuilder();
        if (token != null && !token.isEmpty()) {
            q.append("token=").append(token);
        }
        // pet=1 告诉前端「这是悬浮窗」：只渲染画面，不再开第二路麦克风。
        // 否则悬浮窗会独立收音，导致主界面关麦后仍被识别（见 frontend/index.html 补丁 3）。
        if (q.length() > 0) q.append('&');
        q.append("pet=1");
        return base + "?" + q;
    }

    private void buildFloatWindow() {
        container = new FrameLayout(this);
        container.setBackgroundColor(0x00000000);

        // ---- WebView ----
        floatWebView = new WebView(this);
        WebSettings s = floatWebView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setMediaPlaybackRequiresUserGesture(false);
        floatWebView.setBackgroundColor(0x00000000);

        floatWebView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                floatWebView.post(() -> request.grant(request.getResources()));
            }
        });

        // 屏幕共享桥：悬浮窗 WebView 读取原生最新截图（与全屏 App 一致）
        floatWebView.addJavascriptInterface(new Object() {
            @android.webkit.JavascriptInterface
            public String getLatestFrame() {
                return ScreenShareService.latestFrame;
            }

            // 网页调用：记录一条对话到本地历史
            @android.webkit.JavascriptInterface
            public void saveMessage(String role, String text) {
                ChatHistory.append(FloatWindowService.this, role, text);
            }

            // 声音同步：网页 JS 定时读取全局静音状态
            @android.webkit.JavascriptInterface
            public boolean isSoundMuted() {
                return SoundSync.isMuted(FloatWindowService.this);
            }

            // 麦克风同步：网页轮询「一次性请求（带序号）」并在执行后 ACK；
            // 同时把自身真实状态上报回来，供本按钮图标显示。
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
        }, "AndroidBridge");

        floatWebView.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.proceed();
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                if (url.startsWith("http://") || url.startsWith("https://")) {
                    return false;
                }
                return super.shouldOverrideUrlLoading(view, request);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                injectPetMode(view); // 隐藏 UI + 上半身截取（重复注入防 React 重渲染）
            }
        });

        // 点击人物区域 → 弹出/收起控制面板（不跳回软件）
        floatWebView.setOnTouchListener((v, event) -> {
            if (event.getAction() == MotionEvent.ACTION_UP) {
                floatWebView.postDelayed(() -> togglePanel(), 150);
            }
            return false;
        });

        // ---- 控制面板（声音/屏幕共享/关闭）----
        controlPanel = new LinearLayout(this);
        controlPanel.setOrientation(LinearLayout.HORIZONTAL);
        controlPanel.setGravity(Gravity.CENTER_VERTICAL);
        controlPanel.setBackgroundColor(0xcc000000);
        controlPanel.setPadding(dp(8), dp(4), dp(8), dp(4));
        controlPanel.setVisibility(View.GONE);

        // 原「声音开关」按钮 → 改成「麦克风开关」，与主界面麦克风按钮双向同步（见 MicSync）
        Button soundBtn = new Button(this);
        soundBtn.setText("🎤");
        soundBtn.setTextSize(14f);
        soundBtn.setOnClickListener(v -> toggleMic(soundBtn));

        Button shareBtn = new Button(this);
        shareBtn.setText("📺");
        shareBtn.setTextSize(14f);
        shareBtn.setOnClickListener(v -> toggleScreenShare());

        Button closeBtn = new Button(this);
        closeBtn.setText("✕");
        closeBtn.setTextSize(14f);
        closeBtn.setOnClickListener(v -> stopSelf());

        controlPanel.addView(soundBtn, new LinearLayout.LayoutParams(dp(40), dp(36)));
        controlPanel.addView(shareBtn, new LinearLayout.LayoutParams(dp(40), dp(36)));
        controlPanel.addView(closeBtn, new LinearLayout.LayoutParams(dp(40), dp(36)));

        // 麦克风按钮图标跟随 MicSync.reported —— 它是主界面网页上报的**真实状态**，
        // 与 App 内那个麦克风按钮同源，所以两边显示永远一致。
        // （旧版跟随 reqValue/旧 want：网页上报会覆盖请求，导致点了不同步）
        final Button micBtnRef = soundBtn;
        final android.os.Handler micSyncHandler = new android.os.Handler(getMainLooper());
        micSyncHandler.post(new Runnable() {
            @Override
            public void run() {
                try {
                    boolean on = MicSync.hasReported() ? MicSync.reported() : MicSync.reqValue();
                    micBtnRef.setText(on ? "🎤" : "🔇");
                    micBtnRef.setAlpha(on ? 1.0f : 0.35f);
                } catch (Exception e) {
                    // 忽略
                }
                micSyncHandler.postDelayed(this, 800);
            }
        });

        // ---- 顶部小手柄（点击弹控制面板，拖动移动）----
        View topBar = new View(this);
        topBar.setBackgroundColor(0x44000000);
        topBar.setOnTouchListener(dragTouchListener);

        // ---- 底部拖动把手 ----
        View dragHandle = new View(this);
        dragHandle.setBackgroundColor(0x44000000);
        dragHandle.setOnTouchListener(dragTouchListener);

        // ---- 右下角缩放把手 ----
        View resizeHandle = new View(this);
        resizeHandle.setBackgroundColor(0x88ffffff);
        resizeHandle.setOnTouchListener((v, event) -> {
            switch (event.getAction()) {
                case MotionEvent.ACTION_DOWN:
                    startW = params.width;
                    startH = params.height;
                    startTouchX = (int) event.getRawX();
                    startTouchY = (int) event.getRawY();
                    return true;
                case MotionEvent.ACTION_MOVE:
                    params.width = Math.max(dp(90), startW + (int) event.getRawX() - startTouchX);
                    params.height = Math.max(dp(160), startH + (int) event.getRawY() - startTouchY);
                    windowManager.updateViewLayout(container, params);
                    return true;
                case MotionEvent.ACTION_UP:
                    return true;
            }
            return false;
        });

        FrameLayout.LayoutParams webLp = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT);
        FrameLayout.LayoutParams panelLp = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP | Gravity.CENTER_HORIZONTAL);
        FrameLayout.LayoutParams topLp = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, dp(14), Gravity.TOP);
        FrameLayout.LayoutParams dragLp = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, dp(18), Gravity.BOTTOM);
        FrameLayout.LayoutParams resizeLp = new FrameLayout.LayoutParams(
                dp(26), dp(26), Gravity.BOTTOM | Gravity.RIGHT);

        container.addView(floatWebView, webLp);
        container.addView(controlPanel, panelLp);
        container.addView(topBar, topLp);
        container.addView(dragHandle, dragLp);
        container.addView(resizeHandle, resizeLp);

        // 悬浮窗参数（初始小尺寸）
        params = new WindowManager.LayoutParams(
                dp(180), dp(300),
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT);
        params.gravity = Gravity.TOP | Gravity.START;
        params.x = dp(20);
        params.y = dp(80);

        windowManager.addView(container, params);
        floatWebView.loadUrl(serverUrl());
    }

    /** 拖动悬浮窗（顶部/底部把手共用）。 */
    private final View.OnTouchListener dragTouchListener = (v, event) -> {
        switch (event.getAction()) {
            case MotionEvent.ACTION_DOWN:
                startX = params.x;
                startY = params.y;
                startTouchX = (int) event.getRawX();
                startTouchY = (int) event.getRawY();
                return true;
            case MotionEvent.ACTION_MOVE:
                params.x = startX + (int) event.getRawX() - startTouchX;
                params.y = startY + (int) event.getRawY() - startTouchY;
                windowManager.updateViewLayout(container, params);
                return true;
            case MotionEvent.ACTION_UP:
                return true;
        }
        return false;
    };

    /** 隐藏聊天框/输入栏/侧栏/背景，只留人物，并放大截取上半身。 */
    private void injectPetMode(WebView view) {
        String js = "try{"
                + "(function(){"
                + "var root=document.getElementById('root');if(!root)return;"
                + "var cv=document.getElementById('canvas');"
                // 隐藏背景图
                + "document.querySelectorAll('img,video').forEach(function(m){"
                + "if(m.alt==='background')m.style.display='none'});"
                // 隐藏输入框所在容器（placeholder 含"消息"）
                + "document.querySelectorAll('input').forEach(function(i){"
                + "if(i.placeholder&&i.placeholder.indexOf('消息')>=0){"
                + "var n=i;for(var k=0;k<10;k++){n=n.parentElement;if(!n)break;"
                + "if(n.parentElement===root){n.style.display='none';break}}}});"
                // 隐藏 root 下非舞台的兄弟（侧栏/聊天/footer）
                + "var holder=cv;for(var h=0;h<8;h++){if(holder&&holder.parentElement!==root)holder=holder.parentElement;else break}"
                + "var kids=Array.prototype.slice.call(root.children);"
                + "kids.forEach(function(ch){if(holder&&ch!==holder&&ch.style)ch.style.display='none'});"
                // 上半身截取：canvas 放大，transformOrigin top，腿部溢出底部被容器裁掉
                + "if(cv){cv.style.transform='scale(1.35)';cv.style.transformOrigin='top center'}"
                + "})();"
                // 静音钩子 —— 必须幂等！
                // 本方法每 3 秒重注入一次，若每次都 var _p=play; play=包装(_p) 会层层叠加成
                // 极深调用链，最终 play() 抛 "Maximum call stack size exceeded"（曾导致露露没声音）。
                // 正确做法：只在首次捕获原生 play，未静音时还原成原生（零包装）。
                + "window.__gcMuted=" + SoundSync.isMuted(this) + ";"
                + "if(!window.__gcFloatOrigPlay){window.__gcFloatOrigPlay=HTMLMediaElement.prototype.play;}"
                + "window.__gcFloatApply=function(){"
                + "var _n=window.__gcFloatOrigPlay;"
                + "if(window.__gcMuted){HTMLMediaElement.prototype.play=function(){this.muted=true;return _n.apply(this,arguments)};}"
                + "else{HTMLMediaElement.prototype.play=_n;}"
                + "};"
                + "window.__gcFloatApply();"
                + "}catch(e){}";
        view.postDelayed(() -> view.evaluateJavascript(js, null), 800);
        // 每 3s 重注入一次，防 React 重渲染恢复隐藏
        view.postDelayed(() -> {
            if (floatWebView != null && container != null) {
                view.evaluateJavascript(js, null);
            }
        }, 3000);
    }

    private void togglePanel() {
        panelVisible = !panelVisible;
        controlPanel.setVisibility(panelVisible ? View.VISIBLE : View.GONE);
    }

    /** 切换麦克风：下发一次性请求（带序号），网页轮询到后真正开/关麦克风。 */
    private void toggleMic(Button btn) {
        boolean cur = MicSync.hasReported() ? MicSync.reported() : MicSync.reqValue();
        boolean on = !cur;
        MicSync.request(on);
        // 乐观更新图标，等网页上报真实状态后会被纠正
        btn.setText(on ? "🎤" : "🔇");
        btn.setAlpha(on ? 1.0f : 0.35f);
        Toast.makeText(this, on ? "麦克风已开启" : "麦克风已关闭", Toast.LENGTH_SHORT).show();
    }

    private void toggleScreenShare() {
        // 原生屏幕共享：用 Android MediaProjection 截图，发给电脑 AI。
        //
        // 已在共享 → 直接停服务，完全不弹任何界面（打游戏时零打扰）。
        if (ScreenShareService.running) {
            try {
                stopService(new Intent(this, ScreenShareService.class));
                Toast.makeText(this, "已停止屏幕共享", Toast.LENGTH_SHORT).show();
            } catch (Exception e) {
                // 忽略
            }
            return;
        }
        // 未共享 → 走透明壳 Activity 弹系统「屏幕录制」授权。
        // 关键：不要启动 MainActivity！否则会把游戏搭子主界面拉到前台，
        // 打游戏时会被顶掉。透明壳只会短暂出现系统授权弹窗，游戏画面不受影响。
        try {
            Intent i = new Intent(this, ScreenShareGrantActivity.class);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                    | Intent.FLAG_ACTIVITY_NO_ANIMATION
                    | Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS);
            startActivity(i);
        } catch (Exception e) {
            // 兜底：透明壳起不来时退回旧路径（会短暂把主界面拉到前台）
            try {
                Intent i = new Intent(this, MainActivity.class);
                i.putExtra("gc_action", "screen_share_toggle");
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
                startActivity(i);
            } catch (Exception e2) {
                Toast.makeText(this, "屏幕共享启动失败", Toast.LENGTH_SHORT).show();
            }
        }
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }

    /** 查看本地聊天历史弹窗。 */
    private void showHistory() {
        try {
            java.util.List<ChatHistory.Entry> entries = ChatHistory.load(this);
            StringBuilder sb = new StringBuilder();
            if (entries.isEmpty()) {
                sb.append("（暂无历史记录）");
            } else {
                // 显示最近 50 条，倒序（最新在上）
                int start = Math.max(0, entries.size() - 50);
                for (int i = entries.size() - 1; i >= start; i--) {
                    ChatHistory.Entry e = entries.get(i);
                    String who = "user".equals(e.role) ? "👤 我" : "🤖 搭子";
                    sb.append(who).append(": ").append(e.text).append("\n\n");
                }
            }
            new android.app.AlertDialog.Builder(this)
                    .setTitle("🕘 聊天历史")
                    .setMessage(sb.toString().trim())
                    .setPositiveButton("关闭", null)
                    .setNegativeButton("清空", (d, w) -> ChatHistory.clear(this))
                    .setCancelable(true)
                    .show();
        } catch (Exception e) {
            // 忽略
        }
    }

    @Override
    public void onDestroy() {
        if (container != null) {
            try {
                windowManager.removeView(container);
            } catch (Exception e) {
                // 已移除
            }
            if (floatWebView != null) floatWebView.destroy();
            container = null;
            floatWebView = null;
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
