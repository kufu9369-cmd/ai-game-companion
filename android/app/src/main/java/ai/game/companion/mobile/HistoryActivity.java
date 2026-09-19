package ai.game.companion.mobile;

import android.app.AlertDialog;
import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/**
 * 历史聊天记录页面：可滚动的消息列表，按时间显示，用户/搭子分左右气泡。
 * 由 MainActivity 右下角 🕘 按钮启动。
 */
public class HistoryActivity extends AppCompatActivity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(buildUi());
    }

    private View buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.parseColor("#1a1a2e"));

        // 顶部栏
        LinearLayout topBar = new LinearLayout(this);
        topBar.setOrientation(LinearLayout.HORIZONTAL);
        topBar.setGravity(Gravity.CENTER_VERTICAL);
        topBar.setPadding(dp(16), dp(16), dp(16), dp(16));
        topBar.setBackgroundColor(Color.parseColor("#232342"));

        TextView title = new TextView(this);
        title.setText("🕘 历史聊天记录");
        title.setTextColor(Color.WHITE);
        title.setTextSize(18f);
        title.setTypeface(null, Typeface.BOLD);
        topBar.addView(title, new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        Button clearBtn = new Button(this);
        clearBtn.setText("清空");
        clearBtn.setTextSize(13f);
        clearBtn.setTextColor(Color.WHITE);
        clearBtn.setBackgroundColor(Color.parseColor("#e74c3c"));
        clearBtn.setPadding(dp(12), dp(4), dp(12), dp(4));
        clearBtn.setOnClickListener(v -> confirmClear());
        topBar.addView(clearBtn, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, dp(38)));
        root.addView(topBar, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        // 消息列表（可滚动）
        ScrollView scroll = new ScrollView(this);
        LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        list.setPadding(dp(12), dp(12), dp(12), dp(12));

        List<ChatHistory.Entry> entries = ChatHistory.load(this);
        if (entries.isEmpty()) {
            TextView empty = new TextView(this);
            empty.setText("（暂无聊天记录，先和搭子聊几句吧）");
            empty.setTextColor(Color.parseColor("#888888"));
            empty.setGravity(Gravity.CENTER);
            empty.setPadding(0, dp(60), 0, dp(60));
            list.addView(empty, matchWrap());
        } else {
            SimpleDateFormat fmt = new SimpleDateFormat("MM-dd HH:mm", Locale.CHINA);
            String lastDay = "";
            for (int i = 0; i < entries.size(); i++) {
                ChatHistory.Entry e = entries.get(i);
                String day = new SimpleDateFormat("yyyy-MM-dd", Locale.CHINA)
                        .format(new Date(e.time));
                if (!day.equals(lastDay)) {
                    lastDay = day;
                    TextView sep = new TextView(this);
                    sep.setText("—— " + day + " ——");
                    sep.setTextColor(Color.parseColor("#888888"));
                    sep.setTextSize(12f);
                    sep.setGravity(Gravity.CENTER);
                    sep.setPadding(0, dp(16), 0, dp(8));
                    list.addView(sep, matchWrap());
                }
                list.addView(messageBubble(e, fmt));
            }
        }

        scroll.addView(list);
        root.addView(scroll, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        return root;
    }

    /** 单条消息气泡（用户右蓝、搭子左灰）。 */
    private View messageBubble(ChatHistory.Entry e, SimpleDateFormat fmt) {
        boolean isUser = "user".equals(e.role);
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(isUser ? Gravity.END : Gravity.START);
        row.setPadding(dp(4), dp(4), dp(4), dp(4));

        LinearLayout bubble = new LinearLayout(this);
        bubble.setOrientation(LinearLayout.VERTICAL);
        bubble.setPadding(dp(14), dp(10), dp(14), dp(10));
        bubble.setBackgroundColor(isUser ? Color.parseColor("#2196F3")
                : Color.parseColor("#3a3a5c"));
        android.graphics.drawable.GradientDrawable gd = new android.graphics.drawable.GradientDrawable();
        gd.setColor(isUser ? Color.parseColor("#2196F3") : Color.parseColor("#3a3a5c"));
        gd.setCornerRadius(dp(12));
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            bubble.setBackground(gd);
        }

        TextView who = new TextView(this);
        who.setText((isUser ? "👤 我" : "🤖 搭子") + "  " + fmt.format(new Date(e.time)));
        who.setTextColor(isUser ? Color.parseColor("#BBDEFB") : Color.parseColor("#64ffda"));
        who.setTextSize(11f);
        bubble.addView(who, matchWrap());

        TextView text = new TextView(this);
        text.setText(e.text);
        text.setTextColor(Color.WHITE);
        text.setTextSize(15f);
        text.setLineSpacing(dp(2), 1.1f);
        bubble.addView(text, matchWrap());

        LinearLayout.LayoutParams lp = matchWrap();
        lp.width = LinearLayout.LayoutParams.WRAP_CONTENT;
        row.addView(bubble, lp);
        return row;
    }

    private void confirmClear() {
        new AlertDialog.Builder(this)
                .setTitle("确认清空")
                .setMessage("确定要清空所有聊天记录吗？此操作不可恢复。")
                .setPositiveButton("清空", (d, w) -> {
                    ChatHistory.clear(this);
                    setContentView(buildUi());
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }
}
