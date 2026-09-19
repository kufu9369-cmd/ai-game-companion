package ai.game.companion.mobile;

import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

/**
 * 设置页面（完整版）：连接配置 + 声音 + 屏幕共享 + 悬浮窗。
 * 由 MainActivity 右下角 ⚙️ 按钮启动。
 */
public class SettingsActivity extends AppCompatActivity {

    private static final String PREFS = "gc_float_config";
    private EditText ipInput, portInput, tokenInput;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(buildUi());
    }

    private View buildUi() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(20), dp(20), dp(20), dp(20));
        root.setBackgroundColor(Color.parseColor("#1a1a2e"));

        // 标题
        TextView title = new TextView(this);
        title.setText("⚙️ 设置");
        title.setTextColor(Color.WHITE);
        title.setTextSize(24f);
        title.setTypeface(null, Typeface.BOLD);
        title.setGravity(Gravity.CENTER);
        root.addView(title, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(56)));

        // ---- 连接配置卡片 ----
        root.addView(sectionTitle("📡 连接配置"));

        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        String ip = prefs.getString("server_ip", "");
        String token = prefs.getString("token", "");
        int port = prefs.getInt("port", 12393);

        ipInput = labeledInput("服务器 IP", ip);
        root.addView(ipInput);

        portInput = labeledInput("端口", String.valueOf(port));
        ((EditText) portInput).setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        root.addView(portInput);

        tokenInput = labeledInput("口令", token);
        root.addView(tokenInput);

        Button saveBtn = new Button(this);
        saveBtn.setText("保存连接");
        saveBtn.setBackgroundColor(Color.parseColor("#2196F3"));
        saveBtn.setTextColor(Color.WHITE);
        saveBtn.setOnClickListener(v -> saveConnection());
        root.addView(saveBtn, matchWrap());

        // ---- 功能开关卡片 ----
        root.addView(sectionTitle("🎛️ 功能"));

        // 屏幕共享开关
        Switch shareSwitch = new Switch(this);
        shareSwitch.setText("📺 屏幕共享（AI 看画面）");
        shareSwitch.setTextColor(Color.WHITE);
        shareSwitch.setChecked(ScreenShareService.running);
        shareSwitch.setOnCheckedChangeListener((btn, checked) -> {
            Intent i = new Intent(this, MainActivity.class);
            i.putExtra("gc_action", "screen_share_toggle");
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            startActivity(i);
            btn.setChecked(ScreenShareService.running);
        });
        root.addView(card(shareSwitch));

        // 悬浮窗开关
        Switch floatSwitch = new Switch(this);
        floatSwitch.setText("⛶ 悬浮窗桌宠");
        floatSwitch.setTextColor(Color.WHITE);
        floatSwitch.setOnCheckedChangeListener((btn, checked) -> {
            launchFloatWindow();
            btn.setChecked(false);
        });
        root.addView(card(floatSwitch));

        // ---- 关于 ----
        root.addView(sectionTitle("ℹ️ 关于"));
        TextView about = new TextView(this);
        about.setText("AI游戏搭子 v1.0\n连接电脑后端后使用语音/文字对话\n屏幕共享需 Android 14+ 前台服务");
        about.setTextColor(Color.parseColor("#aaaaaa"));
        about.setTextSize(13f);
        root.addView(about, matchWrap());

        scroll.addView(root);
        return scroll;
    }

    private TextView sectionTitle(String s) {
        TextView tv = new TextView(this);
        tv.setText(s);
        tv.setTextColor(Color.parseColor("#64ffda"));
        tv.setTextSize(15f);
        tv.setTypeface(null, Typeface.BOLD);
        tv.setPadding(0, dp(20), 0, dp(8));
        return tv;
    }

    private EditText labeledInput(String label, String value) {
        EditText et = new EditText(this);
        et.setHint(label);
        et.setText(value);
        et.setTextColor(Color.WHITE);
        et.setHintTextColor(Color.parseColor("#888888"));
        et.setTextSize(15f);
        et.setSingleLine(true);
        et.setPadding(dp(8), dp(10), dp(8), dp(10));
        return et;
    }

    private View card(View inner) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackgroundColor(Color.parseColor("#232342"));
        card.setPadding(dp(16), dp(12), dp(16), dp(12));
        LinearLayout.LayoutParams lp = matchWrap();
        lp.topMargin = dp(8);
        card.setLayoutParams(lp);
        card.addView(inner);
        return card;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
    }

    private void saveConnection() {
        String ip = ipInput.getText().toString().trim();
        String token = tokenInput.getText().toString().trim();
        int port;
        try {
            port = Integer.parseInt(portInput.getText().toString().trim());
        } catch (Exception e) {
            port = 12393;
        }
        if (ip.isEmpty()) {
            Toast.makeText(this, "请输入服务器 IP", Toast.LENGTH_SHORT).show();
            return;
        }
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                .putString("server_ip", ip)
                .putString("token", token)
                .putInt("port", port)
                .apply();
        Toast.makeText(this, "已保存： " + ip + ":" + port, Toast.LENGTH_SHORT).show();
    }

    private void launchFloatWindow() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(this)) {
            Intent intent = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    android.net.Uri.parse("package:" + getPackageName()));
            startActivity(intent);
        } else {
            startService(new Intent(this, FloatWindowService.class));
            Toast.makeText(this, "悬浮窗已开启", Toast.LENGTH_SHORT).show();
        }
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }
}
