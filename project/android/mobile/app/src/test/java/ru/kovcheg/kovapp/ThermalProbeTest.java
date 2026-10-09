package ru.kovcheg.kovapp;

import static org.junit.Assert.*;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

import org.junit.Test;

import ru.kovcheg.kovapp.media.ThermalProbe;

public class ThermalProbeTest {
    private static void put(File root, String path, String text) throws Exception {
        File f = new File(root, path);
        f.getParentFile().mkdirs();
        Files.write(f.toPath(), text.getBytes(StandardCharsets.UTF_8));
    }

    @Test public void readsTemperaturesAndFrequencies() throws Exception {
        File sys = Files.createTempDirectory("sys").toFile();
        put(sys, "class/power_supply/battery/temp", "305\n");
        put(sys, "class/thermal/thermal_zone0/type", "soc_thermal\n");
        put(sys, "class/thermal/thermal_zone0/temp", "41000\n");      // тысячные доли
        put(sys, "class/thermal/thermal_zone1/type", "gpu\n");
        put(sys, "class/thermal/thermal_zone1/temp", "395\n");        // десятые доли
        put(sys, "class/thermal/thermal_zone2/type", "broken\n");
        put(sys, "class/thermal/thermal_zone2/temp", "-1\n");         // неверное — пропускается
        put(sys, "devices/system/cpu/cpufreq/policy0/related_cpus", "0 1 2 3\n");
        put(sys, "devices/system/cpu/cpufreq/policy0/scaling_cur_freq", "1402000\n");
        put(sys, "devices/system/cpu/cpufreq/policy0/scaling_max_freq", "1844000\n");
        put(sys, "devices/system/cpu/cpufreq/policy0/cpuinfo_max_freq", "1844000\n");
        put(sys, "devices/system/cpu/cpufreq/policy4/related_cpus", "4 5 6 7\n");
        put(sys, "devices/system/cpu/cpufreq/policy4/scaling_cur_freq", "1018000\n");
        put(sys, "devices/system/cpu/cpufreq/policy4/scaling_max_freq", "1421000\n");
        put(sys, "devices/system/cpu/cpufreq/policy4/cpuinfo_max_freq", "2362000\n");
        put(sys, "class/devfreq/gpufreq/cur_freq", "533000000\n");
        put(sys, "class/devfreq/gpufreq/max_freq", "1037000000\n");
        String s = new ThermalProbe(sys).snapshot(null);
        assertTrue(s, s.startsWith("батарея 30.5°C"));
        assertTrue(s, s.contains("датчики: soc_thermal 41°C, gpu 40°C"));
        assertFalse(s, s.contains("broken"));
        assertTrue(s, s.contains("ядра 0-3 1402/1844 МГц"));
        assertTrue(s, s.contains("ядра 4-7 1018/2362 МГц (ограничено до 1421)"));
        assertTrue(s, s.endsWith("графика 533/1037 МГц"));
    }

    @Test public void batteryFromSystemWinsAndMissingDataIsSkipped() throws Exception {
        File sys = Files.createTempDirectory("sys").toFile();
        assertEquals("нет доступа к датчикам", new ThermalProbe(sys).snapshot(null));
        assertEquals("батарея 33.0°C", new ThermalProbe(sys).snapshot(330));
    }
}
