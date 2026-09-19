package ai.game.companion.mobile;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import android.util.DisplayMetrics;
import android.view.WindowManager;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;

/**
 * 原生屏幕捕获（MediaProjection）：周期性截图手机屏幕，转 base64，
 * 通过 JS 桥注入到网页（前端发 text-input 带 images 给电脑 AI 识别）。
 *
 * 用法：请求授权后调 start(projectionCode, projectionData, callback) 开始周期截图。
 */
public class ScreenShareManager {
    private MediaProjection projection;
    private VirtualDisplay virtualDisplay;
    private ImageReader imageReader;
    private Handler handler;
    private boolean running = false;
    private ScreenCallback callback;

    public interface ScreenCallback {
        void onFrame(String base64Jpeg);
    }

    /** 弹出系统屏幕捕获授权（需在 Activity 中调用）。返回授权 code/data。 */
    public static void requestPermission(Activity activity) {
        MediaProjectionManager mpm =
                (MediaProjectionManager) activity.getSystemService(Context.MEDIA_PROJECTION_SERVICE);
        activity.startActivityForResult(mpm.createScreenCaptureIntent(), 2001);
    }

    /** 从 onActivityResult 恢复授权，并开始周期截图。 */
    public void start(Context context, int resultCode, Intent data, ScreenCallback cb) {
        if (running) return;
        callback = cb;
        handler = new Handler(Looper.getMainLooper());
        MediaProjectionManager mpm =
                (MediaProjectionManager) context.getSystemService(Context.MEDIA_PROJECTION_SERVICE);
        projection = mpm.getMediaProjection(resultCode, data);
        if (projection == null) return;

        DisplayMetrics metrics = context.getResources().getDisplayMetrics();
        int width = metrics.widthPixels;
        int height = metrics.heightPixels;
        int density = metrics.densityDpi;

        imageReader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2);
        virtualDisplay = projection.createVirtualDisplay(
                "gc-screen", width, height, density,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, imageReader.getSurface(), null, handler);

        running = true;
        scheduleCapture();
    }

    private void scheduleCapture() {
        if (!running) return;
        // 每 4 秒截一帧（降低频率，减少内存/性能压力，避免崩溃）
        try {
            captureFrame();
        } catch (Throwable t) {
            // 任何异常都不能崩溃
        }
        handler.postDelayed(this::scheduleCapture, 4000);
    }

    private void captureFrame() {
        try {
            Image image = imageReader.acquireLatestImage();
            if (image == null) return;
            Bitmap bmp = null;
            try {
                Image.Plane plane = image.getPlanes()[0];
                ByteBuffer buffer = plane.getBuffer();
                int pixelStride = plane.getPixelStride();
                int rowStride = plane.getRowStride();
                int width = image.getWidth();
                int height = image.getHeight();
                int rowPadding = rowStride - pixelStride * width;

                // 更小的目标分辨率，降低内存（640 宽）
                int targetW = Math.min(width, 640);
                int targetH = height * targetW / width;

                Bitmap full = Bitmap.createBitmap(
                        width + rowPadding / pixelStride, height, Bitmap.Config.ARGB_8888);
                full.copyPixelsFromBuffer(buffer);
                bmp = Bitmap.createBitmap(full, 0, 0, width, height);
                full.recycle();

                if (width != targetW) {
                    Bitmap scaled = Bitmap.createScaledBitmap(bmp, targetW, targetH, true);
                    bmp.recycle();
                    bmp = scaled;
                }

                ByteArrayOutputStream out = new ByteArrayOutputStream();
                bmp.compress(Bitmap.CompressFormat.JPEG, 55, out);
                byte[] bytes = out.toByteArray();
                String b64 = Base64.encodeToString(bytes, Base64.NO_WRAP);

                if (callback != null) callback.onFrame(b64);
            } finally {
                image.close();
                if (bmp != null) bmp.recycle();
            }
        } catch (Throwable t) {
            // 单帧失败忽略，绝不崩溃
        }
    }

    public void stop() {
        running = false;
        if (handler != null) handler.removeCallbacksAndMessages(null);
        if (virtualDisplay != null) { virtualDisplay.release(); virtualDisplay = null; }
        if (imageReader != null) { imageReader.close(); imageReader = null; }
        if (projection != null) { projection.stop(); projection = null; }
    }

    public boolean isRunning() {
        return running;
    }
}
