package ai.game.companion.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
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
import android.os.IBinder;
import android.os.Looper;
import android.util.Base64;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;

/**
 * 屏幕捕获前台服务（Android 14 要求 MediaProjection 必须在前台服务中运行）。
 * 周期性截图手机屏幕，转 base64，通过静态回调发给网页桥。
 */
public class ScreenShareService extends Service {

    public static final String ACTION_START = "ai.game.companion.mobile.SCREEN_START";
    public static final String ACTION_STOP = "ai.game.companion.mobile.SCREEN_STOP";
    private static final int NOTIFY_ID = 3001;

    /** 最新一帧 base64（供网页桥读取） */
    public static volatile String latestFrame = null;
    public static volatile boolean running = false;

    private MediaProjection projection;
    private VirtualDisplay virtualDisplay;
    private ImageReader imageReader;
    private Handler handler;

    public static Intent buildStartIntent(Intent data, int resultCode) {
        Intent i = new Intent();
        i.setAction(ACTION_START);
        i.putExtra("result_code", resultCode);
        i.putExtra("result_data", data);
        return i;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        handler = new Handler(Looper.getMainLooper());
        createNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) return START_NOT_STICKY;
        if (ACTION_STOP.equals(intent.getAction())) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (ACTION_START.equals(intent.getAction())) {
            int resultCode = intent.getIntExtra("result_code", 0);
            Intent data = intent.getParcelableExtra("result_data");
            startCapture(resultCode, data);
        }
        return START_STICKY;
    }

    private void startCapture(int resultCode, Intent data) {
        try {
            android.util.Log.i("GCScreen", "startCapture resultCode=" + resultCode
                    + " data=" + (data == null ? "null" : "ok"));
            // Android 14：MediaProjection 前台服务必须声明 mediaProjection 类型
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(NOTIFY_ID, buildNotification(),
                        android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
            } else {
                startForeground(NOTIFY_ID, buildNotification());
            }

            MediaProjectionManager mpm =
                    (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
            projection = mpm.getMediaProjection(resultCode, data);
            android.util.Log.i("GCScreen", "getMediaProjection -> "
                    + (projection == null ? "NULL (resultCode/data invalid)" : "ok"));
            if (projection == null) { stopSelf(); return; }

            // Android 14 (API 34) 起：createVirtualDisplay() 之前必须先注册
            // MediaProjection.Callback，否则抛
            // IllegalStateException: Must register a callback before starting capture。
            // 这也是之前"服务启动后立刻停止、截图发不出去"的根因。
            projection.registerCallback(new MediaProjection.Callback() {
                @Override
                public void onStop() {
                    android.util.Log.i("GCScreen", "MediaProjection.Callback.onStop -> stopping service");
                    running = false;
                    stopSelf();
                }
            }, handler);

            int width = getResources().getDisplayMetrics().widthPixels;
            int height = getResources().getDisplayMetrics().heightPixels;
            int density = getResources().getDisplayMetrics().densityDpi;

            imageReader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2);
            virtualDisplay = projection.createVirtualDisplay(
                    "gc-screen", width, height, density,
                    DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                    imageReader.getSurface(), null, handler);

            running = true;
            android.util.Log.i("GCScreen", "virtualDisplay created " + width + "x" + height
                    + " density=" + density + " -> scheduleCapture");
            scheduleCapture();
        } catch (Throwable t) {
            android.util.Log.e("GCScreen", "startCapture FAILED: " + t, t);
            running = false;
            stopSelf();
        }
    }

    private void scheduleCapture() {
        if (!running) return;
        try {
            captureFrame();
        } catch (Throwable t) {
            // 单帧失败忽略
        }
        handler.postDelayed(this::scheduleCapture, 4000);
    }

    private void captureFrame() {
        try {
            Image image = imageReader.acquireLatestImage();
            if (image == null) {
                android.util.Log.w("GCScreen", "captureFrame: acquireLatestImage NULL");
                return;
            }
            Bitmap bmp = null;
            try {
                Image.Plane plane = image.getPlanes()[0];
                ByteBuffer buffer = plane.getBuffer();
                int pixelStride = plane.getPixelStride();
                int rowStride = plane.getRowStride();
                int width = image.getWidth();
                int height = image.getHeight();
                int rowPadding = rowStride - pixelStride * width;

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
                latestFrame = Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP);
                android.util.Log.i("GCScreen", "frame captured len=" + latestFrame.length()
                        + " (" + targetW + "x" + targetH + ")");
            } finally {
                image.close();
                if (bmp != null) bmp.recycle();
            }
        } catch (Throwable t) {
            android.util.Log.e("GCScreen", "captureFrame FAILED: " + t, t);
        }
    }

    private Notification buildNotification() {
        Intent stop = new Intent(this, ScreenShareService.class);
        stop.setAction(ACTION_STOP);
        PendingIntent pi = PendingIntent.getService(this, 0, stop,
                PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, "gc_screen_ch")
                : new Notification.Builder(this);
        return b.setContentTitle("AI游戏搭子")
                .setContentText("正在共享屏幕给 AI 搭子")
                .setSmallIcon(android.R.drawable.ic_menu_camera)
                .setContentIntent(pi)
                .setOngoing(true)
                .build();
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(
                    "gc_screen_ch", "屏幕共享", NotificationManager.IMPORTANCE_LOW);
            getSystemService(NotificationManager.class).createNotificationChannel(ch);
        }
    }

    @Override
    public void onDestroy() {
        running = false;
        if (handler != null) handler.removeCallbacksAndMessages(null);
        if (virtualDisplay != null) { virtualDisplay.release(); virtualDisplay = null; }
        if (imageReader != null) { imageReader.close(); imageReader = null; }
        if (projection != null) { projection.stop(); projection = null; }
        latestFrame = null;
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
