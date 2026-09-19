package ai.game.companion.mobile;

import java.util.concurrent.atomic.AtomicLong;

/**
 * 麦克风开关的跨组件共享状态（悬浮窗 ↔ 主界面）。
 *
 * 背景：悬浮窗（FloatWindowService）和主界面（MainActivity）是两个独立 WebView。
 * 用户在悬浮窗上按「麦克风」按钮，要能真正切换主界面里的麦克风；反过来，主界面
 * 手动开关时，悬浮窗按钮图标也要跟着变。
 *
 * 旧实现（已废弃）的问题：只用一个布尔 want，网页上报真实状态时也会写 want，
 * 于是「悬浮窗的请求」会被网页的下一次上报覆盖掉 → 按钮点了不同步。
 *
 * 现实现拆成两个互不干扰的概念：
 *   request  —— 一次性的「请把麦克风切到 X」指令，单调递增序号，网页执行后 ACK。
 *   reported —— 网页上报的真实状态（= App 内麦克风按钮的渲染依据），只读不改请求。
 */
public class MicSync {

    private static final AtomicLong SEQ = new AtomicLong(0);

    /** 请求值：true = 开麦。 */
    private static volatile boolean reqValue = true;
    /** 已被网页确认执行的序号。 */
    private static volatile long ackSeq = 0;
    /** 网页上报的真实状态（与 App 内按钮同源）。 */
    private static volatile boolean reported = false;
    private static volatile long reportedAt = 0L;

    /** 下发一次切换请求，返回新序号。 */
    public static long request(boolean desired) {
        reqValue = desired;
        return SEQ.incrementAndGet();
    }

    public static long reqSeq() {
        return SEQ.get();
    }

    public static boolean reqValue() {
        return reqValue;
    }

    public static void ack(long seq) {
        if (seq > ackSeq) ackSeq = seq;
    }

    public static long ackSeq() {
        return ackSeq;
    }

    /** 网页上报真实状态。**不会**覆盖尚未执行的请求。 */
    public static void report(boolean on) {
        reported = on;
        reportedAt = System.currentTimeMillis();
    }

    public static boolean reported() {
        return reported;
    }

    public static long reportedAt() {
        return reportedAt;
    }

    /** 是否已经收到过网页上报。 */
    public static boolean hasReported() {
        return reportedAt > 0L;
    }

    // ---- 兼容旧接口 ----
    public static boolean getWant() {
        return reqValue;
    }

    public static void setWant(boolean v) {
        request(v);
    }
}
