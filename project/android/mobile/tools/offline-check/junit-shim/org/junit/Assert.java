package org.junit;

import java.util.Arrays;
import java.util.Objects;

/** Подмножество org.junit.Assert, которым пользуются тесты. */
public final class Assert {
    private Assert() {}
    public static void fail(String msg) { throw new AssertionError(msg); }
    public static void assertTrue(boolean c) { if (!c) throw new AssertionError("ожидалось true"); }
    public static void assertTrue(String m, boolean c) { if (!c) throw new AssertionError(m); }
    public static void assertFalse(boolean c) { if (c) throw new AssertionError("ожидалось false"); }
    public static void assertFalse(String m, boolean c) { if (c) throw new AssertionError(m); }
    public static void assertNull(Object o) { if (o != null) throw new AssertionError("ожидался null, получено " + o); }
    public static void assertNull(String m, Object o) { if (o != null) throw new AssertionError(m + ": " + o); }
    public static void assertNotNull(Object o) { if (o == null) throw new AssertionError("ожидалось не null"); }
    public static void assertNotNull(String m, Object o) { if (o == null) throw new AssertionError(m); }
    public static void assertEquals(Object a, Object b) { if (!Objects.equals(a, b)) throw new AssertionError("ожидалось <" + a + ">, получено <" + b + ">"); }
    public static void assertEquals(String m, Object a, Object b) { if (!Objects.equals(a, b)) throw new AssertionError(m + ": ожидалось <" + a + ">, получено <" + b + ">"); }
    public static void assertEquals(long a, long b) { if (a != b) throw new AssertionError("ожидалось <" + a + ">, получено <" + b + ">"); }
    public static void assertEquals(String m, long a, long b) { if (a != b) throw new AssertionError(m + ": ожидалось <" + a + ">, получено <" + b + ">"); }
    public static void assertEquals(double a, double b, double eps) { if (Math.abs(a - b) > eps) throw new AssertionError("ожидалось <" + a + ">, получено <" + b + ">"); }
    public static void assertEquals(String m, double a, double b, double eps) { if (Math.abs(a - b) > eps) throw new AssertionError(m + ": ожидалось <" + a + ">, получено <" + b + ">"); }
    public static void assertArrayEquals(byte[] a, byte[] b) { if (!Arrays.equals(a, b)) throw new AssertionError("массивы различаются"); }
    public static void assertArrayEquals(String m, byte[] a, byte[] b) { if (!Arrays.equals(a, b)) throw new AssertionError(m); }
    public static void assertNotEquals(Object a, Object b) { if (Objects.equals(a, b)) throw new AssertionError("значения не должны совпадать: " + a); }
}
