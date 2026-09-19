package ai.game.companion.mobile;

import android.content.Context;

/**
 * 全局静音状态：主页面与悬浮窗共用，保证点一边另一边同步。
 *
 * 用进程内静态变量而非 SharedPreferences 持久化 —— 关键设计决策：
 * - 静态变量：MainActivity 和 FloatWindowService 同进程，天然共享，重启 App 自动恢复有声。
 * - 持久化 SharedPreferences：会把静音状态写死，用户误点 🔊 后永久静音，
 *   且重启不恢复，极易造成"突然没声音"的困惑（本 bug 曾导致此问题）。
 *
 * === 2026-09-11 重要修复 ===
 * 旧实现每次注入都把 HTMLMediaElement.prototype.play 再包一层：
 *     var _p = HTMLMediaElement.prototype.play;
 *     HTMLMediaElement.prototype.play = function(){ ... return _p.apply(...) };
 * 由于 onPageFinished 会多次触发、加上其它注入，包装会层层叠加成一条很深的调用链，
 * 最终 play() 抛 "Maximum call stack size exceeded"（RangeError），
 * 表现就是【露露的语音完全没有声音】。
 *
 * 新实现原则：**没静音时 prototype.play 必须还原成原生函数**，
 * 即正常使用路径上零包装、零开销、零风险；只有真的静音时才临时套一层。
 */
public class SoundSync {
    /** 进程内全局静音状态（不持久化，重启自动恢复有声）。 */
    private static volatile boolean muted = false;

    /** 当前是否全局静音。 */
    public static boolean isMuted(Context ctx) {
        return muted;
    }

    /** 设置全局静音状态。返回新值。 */
    public static boolean setMuted(Context ctx, boolean m) {
        muted = m;
        return muted;
    }

    /**
     * 给 WebView 注入"静音补丁"。
     *
     * 幂等且无副作用：
     * 1) 首次注入时用 window.__gcOrigPlay 记住原生 play（此后永不改变）；
     * 2) 未静音 → 把 prototype.play 还原成原生引用；
     * 3) 静音   → 临时替换成"设 muted=true 再调原生"的单层包装；
     * 4) 400ms 轮询原生桥 AndroidBridge.isSoundMuted()，实现主页面 ↔ 悬浮窗双向同步。
     */
    public static String injectPatchScript(Context ctx) {
        boolean m = muted;
        return "try{"
                + "window.__gcMuted=" + m + ";"

                // ---- 1) 只捕获一次原生 play ----
                + "if(!window.__gcOrigPlay){window.__gcOrigPlay=HTMLMediaElement.prototype.play;}"
                + "var _n=window.__gcOrigPlay;"

                // ---- 2) 按静音状态安装/还原 ----
                + "window.__gcApplySound=function(){"
                +   "var mu=!!window.__gcMuted;"
                +   "if(mu){"
                +     "HTMLMediaElement.prototype.play=function(){this.muted=true;return _n.apply(this,arguments)};"
                +   "}else{"
                +     "HTMLMediaElement.prototype.play=_n;"
                +   "}"
                +   "var as=document.querySelectorAll('audio');"
                +   "for(var i=0;i<as.length;i++){try{as[i].muted=mu}catch(e){}}"
                + "};"
                + "window.__gcApplySound();"

                // ---- 3) 双向同步轮询（不重复创建）----
                + "if(!window.__gcMuteSync){window.__gcMuteSync=setInterval(function(){"
                +   "try{"
                +     "if(window.AndroidBridge&&window.AndroidBridge.isSoundMuted){"
                +       "var v=window.AndroidBridge.isSoundMuted();"
                +       "if(v!==window.__gcMuted){window.__gcMuted=v;window.__gcApplySound();}"
                +     "}"
                +   "}catch(e){}"
                + "},400);}"
                + "}catch(e){}";
    }
}
