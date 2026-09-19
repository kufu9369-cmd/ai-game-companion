package ai.game.companion.mobile;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.widget.Toast;

/**
 * 屏幕共享授权用的「透明壳」Activity。
 *
 * 为什么需要它：
 *   MediaProjection 的授权结果必须由 Activity 通过 onActivityResult 接收，
 *   但原先悬浮窗是直接去启动 MainActivity（主界面），打游戏时会把游戏顶掉。
 *   这个 Activity 是透明的、不进最近任务、拿到结果就销毁 ——
 *   屏幕上只会短暂出现系统「屏幕录制」授权弹窗，游戏画面不受影响。
 *
 * 用法：悬浮窗 / 外部用
 *   startActivity(new Intent(ctx, ScreenShareGrantActivity.class)
 *                 .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
 * 重复调用同一入口：已在共享 → 停止；未在共享 → 弹授权。
 */
public class ScreenShareGrantActivity extends Activity {

    private static final int RC_MEDIA = 2001;
    private boolean stopped = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // 已开启共享 → 这一下算「关闭」
        if (ScreenShareService.running) {
            stopped = true;
            try {
                stopService(new Intent(this, ScreenShareService.class));
                toast("已停止屏幕共享");
            } catch (Exception e) {
                // 忽略
            }
            finish();
            return;
        }
        try {
            android.media.projection.MediaProjectionManager mpm =
                    (android.media.projection.MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
            startActivityForResult(mpm.createScreenCaptureIntent(), RC_MEDIA);
        } catch (Exception e) {
            toast("屏幕共享启动失败：" + e.getMessage());
            finish();
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == RC_MEDIA && resultCode == RESULT_OK && data != null) {
            try {
                Intent si = ScreenShareService.buildStartIntent(data, resultCode);
                si.setClass(this, ScreenShareService.class);
                startForegroundService(si);
                toast("屏幕共享已开启（可以继续玩游戏）");
            } catch (Exception e) {
                toast("屏幕共享启动失败：" + e.getMessage());
            }
        } else {
            toast("已取消屏幕共享");
        }
        finish();
        super.onActivityResult(requestCode, resultCode, data);
    }

    /** 用完即走：不参与动画、不留任务栈 */
    @Override
    public void finish() {
        super.finish();
        overridePendingTransition(0, 0);
    }

    private void toast(String s) {
        try {
            Toast.makeText(this, s, Toast.LENGTH_SHORT).show();
        } catch (Exception e) {
            // 忽略
        }
    }
}
