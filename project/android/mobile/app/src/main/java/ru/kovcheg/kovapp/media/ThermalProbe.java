package ru.kovcheg.kovapp.media;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

/**
 * Снимок нагрева и частот для журнала сжатия: температура батареи, самые горячие датчики,
 * частоты групп ядер (текущая / разрешённая / наибольшая) и графики. Что телефон не отдаёт — пропускается.
 */
public final class ThermalProbe {
    private final File sys;   // корень /sys (в тестах — временная папка)

    public ThermalProbe(File sysRoot) { this.sys = sysRoot; }

    public static ThermalProbe device() { return new ThermalProbe(new File("/sys")); }

    /** batteryTenths — температура батареи в десятых °C из системы (null — взять из /sys) */
    public String snapshot(Integer batteryTenths) {
        List<String> parts = new ArrayList<>();
        Integer bt = batteryTenths != null ? batteryTenths : readInt(new File(sys, "class/power_supply/battery/temp"));
        if (bt != null && bt > 0 && bt < 1000) parts.add(String.format(Locale.ROOT, "батарея %.1f°C", bt / 10.0));
        String zones = zones();
        if (!zones.isEmpty()) parts.add("датчики: " + zones);
        String cpu = cpu();
        if (!cpu.isEmpty()) parts.add("процессор: " + cpu);
        String gpu = gpu();
        if (!gpu.isEmpty()) parts.add("графика " + gpu);
        return parts.isEmpty() ? "нет доступа к датчикам" : String.join(" · ", parts);
    }

    /** Три самых горячих датчика; значения в тысячных или десятых градуса приводятся к °C */
    String zones() {
        File[] list = new File(sys, "class/thermal").listFiles((d, n) -> n.startsWith("thermal_zone"));
        if (list == null) return "";
        List<double[]> temps = new ArrayList<>();
        List<String> names = new ArrayList<>();
        for (File z : list) {
            Integer raw = readInt(new File(z, "temp"));
            if (raw == null) continue;
            double c = raw > 1000 ? raw / 1000.0 : raw > 200 ? raw / 10.0 : raw;
            if (c <= 0 || c > 150) continue;
            String type = readText(new File(z, "type"));
            names.add(type == null || type.isEmpty() ? z.getName() : type);
            temps.add(new double[]{c, names.size() - 1});
        }
        temps.sort((a, b) -> Double.compare(b[0], a[0]));
        List<String> out = new ArrayList<>();
        for (int i = 0; i < Math.min(3, temps.size()); i++)
            out.add(String.format(Locale.ROOT, "%s %.0f°C", names.get((int) temps.get(i)[1]), temps.get(i)[0]));
        return String.join(", ", out);
    }

    /** Группы ядер: «ядра 4-7 1844/2362 МГц (ограничено до 1844)» — частоты в кГц из cpufreq */
    String cpu() {
        File[] pol = new File(sys, "devices/system/cpu/cpufreq").listFiles((d, n) -> n.startsWith("policy"));
        if (pol == null || pol.length == 0) return "";
        Arrays.sort(pol, (a, b) -> a.getName().compareTo(b.getName()));
        List<String> out = new ArrayList<>();
        for (File p : pol) {
            Integer cur = readInt(new File(p, "scaling_cur_freq")), allowed = readInt(new File(p, "scaling_max_freq")),
                    top = readInt(new File(p, "cpuinfo_max_freq"));
            if (cur == null) continue;
            String cores = readText(new File(p, "related_cpus"));
            String label = cores == null || cores.isEmpty() ? p.getName() : "ядра " + span(cores);
            StringBuilder s = new StringBuilder(label).append(' ').append(cur / 1000);
            if (top != null) s.append('/').append(top / 1000);
            s.append(" МГц");
            if (allowed != null && top != null && allowed < top) s.append(" (ограничено до ").append(allowed / 1000).append(')');
            out.add(s.toString());
        }
        return String.join(", ", out);
    }

    /** Графика: devfreq-устройство с gpu/mali в имени, частоты в Гц */
    String gpu() {
        File[] dev = new File(sys, "class/devfreq").listFiles((d, n) -> n.toLowerCase(Locale.ROOT).contains("gpu") || n.toLowerCase(Locale.ROOT).contains("mali"));
        if (dev == null) return "";
        for (File g : dev) {
            Integer cur = readInt(new File(g, "cur_freq")), max = readInt(new File(g, "max_freq"));
            if (cur == null) continue;
            return (cur / 1_000_000) + (max != null ? "/" + (max / 1_000_000) : "") + " МГц";
        }
        return "";
    }

    /** «4 5 6 7» → «4-7» */
    static String span(String cores) {
        String[] p = cores.trim().split("\\s+");
        return p.length > 1 ? p[0] + "-" + p[p.length - 1] : p[0];
    }

    static Integer readInt(File f) {
        String t = readText(f);
        if (t == null || t.isEmpty()) return null;
        try { return Integer.valueOf(t.split("\\s+")[0]); } catch (NumberFormatException e) { return null; }
    }

    static String readText(File f) {
        try { return new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8).trim(); }
        catch (Exception e) { return null; }   // нет файла или нет доступа — пропускаем
    }
}
