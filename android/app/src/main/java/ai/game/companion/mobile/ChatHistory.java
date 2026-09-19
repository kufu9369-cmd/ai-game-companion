package ai.game.companion.mobile;

import android.content.Context;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * 本地聊天历史存储（JSON 文件）。
 * 悬浮窗与全屏 App 共用，每次对话写入，可查看历史。
 */
public class ChatHistory {
    private static final String FILE = "chat_history.json";
    private static final int MAX_ENTRIES = 500;

    public static class Entry {
        public String role;      // "user" / "ai"
        public String text;
        public long time;

        public Entry(String role, String text, long time) {
            this.role = role;
            this.text = text;
            this.time = time;
        }
    }

    /** 追加一条对话记录。 */
    public static void append(Context ctx, String role, String text) {
        try {
            List<Entry> entries = load(ctx);
            entries.add(new Entry(role, text, System.currentTimeMillis()));
            // 裁剪超长
            while (entries.size() > MAX_ENTRIES) entries.remove(0);

            JSONArray arr = new JSONArray();
            for (Entry e : entries) {
                JSONObject o = new JSONObject();
                o.put("role", e.role);
                o.put("text", e.text);
                o.put("time", e.time);
                arr.put(o);
            }
            write(ctx, arr.toString());
        } catch (Exception e) {
            Log.e("ChatHistory", "append error", e);
        }
    }

    /** 读取全部历史。 */
    public static List<Entry> load(Context ctx) {
        List<Entry> out = new ArrayList<>();
        try {
            File f = new File(ctx.getFilesDir(), FILE);
            if (!f.exists()) return out;
            FileInputStream fis = new FileInputStream(f);
            byte[] bytes = new byte[(int) f.length()];
            int read = fis.read(bytes);
            fis.close();
            if (read <= 0) return out;
            JSONArray arr = new JSONArray(new String(bytes, StandardCharsets.UTF_8));
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.getJSONObject(i);
                out.add(new Entry(o.optString("role"), o.optString("text"), o.optLong("time")));
            }
        } catch (Exception e) {
            Log.e("ChatHistory", "load error", e);
        }
        return out;
    }

    /** 清空历史。 */
    public static void clear(Context ctx) {
        write(ctx, "[]");
    }

    private static void write(Context ctx, String json) {
        try {
            File f = new File(ctx.getFilesDir(), FILE);
            FileOutputStream fos = new FileOutputStream(f);
            fos.write(json.getBytes(StandardCharsets.UTF_8));
            fos.close();
        } catch (Exception e) {
            Log.e("ChatHistory", "write error", e);
        }
    }
}
