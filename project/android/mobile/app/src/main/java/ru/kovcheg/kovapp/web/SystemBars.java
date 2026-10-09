package ru.kovcheg.kovapp.web;

import android.app.Activity;
import android.graphics.Color;
import android.os.Build;
import android.view.View;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;

/**
 * Цвет строки состояния и панели навигации — по meta theme-color страницы,
 * как у Chrome на телефоне. На Android 11+ окно рисуется под системными
 * панелями, отступы задаются вручную (включая клавиатуру).
 */
public final class SystemBars {
    private final Activity activity;
    private final View root;
    private int color = Color.WHITE;

    public SystemBars(Activity activity, View root) {
        this.activity = activity;
        this.root = root;
        Window w = activity.getWindow();
        if (Build.VERSION.SDK_INT >= 30) {
            w.setDecorFitsSystemWindows(false);
            root.setOnApplyWindowInsetsListener((v, insets) -> {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                android.graphics.Insets ime = insets.getInsets(WindowInsets.Type.ime());
                v.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, ime.bottom));
                return WindowInsets.CONSUMED;
            });
        } else {
            root.setFitsSystemWindows(true);
        }
    }

    /** Цвет из страницы: #RRGGBB или rgb(r, g, b) */
    public static int parse(String css, int fallback) {
        if (css == null) return fallback;
        String c = css.trim();
        try {
            if (c.matches("#[0-9a-fA-F]{6}")) return 0xFF000000 | Integer.parseInt(c.substring(1), 16);
            if (c.matches("#[0-9a-fA-F]{3}")) {
                int r = Integer.parseInt(c.substring(1, 2), 16), g = Integer.parseInt(c.substring(2, 3), 16), b = Integer.parseInt(c.substring(3, 4), 16);
                return Color.rgb(r * 17, g * 17, b * 17);
            }
            java.util.regex.Matcher m = java.util.regex.Pattern.compile("rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)").matcher(c);
            if (m.find()) return Color.rgb(Integer.parseInt(m.group(1)), Integer.parseInt(m.group(2)), Integer.parseInt(m.group(3)));
        } catch (NumberFormatException ignore) { /* */ }
        return fallback;
    }

    static boolean light(int c) {
        double l = (0.299 * Color.red(c) + 0.587 * Color.green(c) + 0.114 * Color.blue(c)) / 255.0;
        return l > 0.6;
    }

    @SuppressWarnings("deprecation")
    public void apply(int top) {
        color = top;
        root.setBackgroundColor(top);
        Window w = activity.getWindow();
        boolean lightIcons = light(top);
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController c = w.getInsetsController();
            if (c != null) {
                int mask = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
                c.setSystemBarsAppearance(lightIcons ? mask : 0, mask);
            }
            if (Build.VERSION.SDK_INT < 35) {
                w.setStatusBarColor(Color.TRANSPARENT);
                w.setNavigationBarColor(Color.TRANSPARENT);
            }
        } else {
            w.setStatusBarColor(top);
            int flags = w.getDecorView().getSystemUiVisibility();
            flags = lightIcons ? flags | View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR : flags & ~View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
            if (Build.VERSION.SDK_INT >= 26) {
                w.setNavigationBarColor(top);
                flags = lightIcons ? flags | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR : flags & ~View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
            } else {
                w.setNavigationBarColor(Color.BLACK);
            }
            w.getDecorView().setSystemUiVisibility(flags);
        }
    }

    public int color() { return color; }
}
