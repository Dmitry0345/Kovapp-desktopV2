import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;

/** Запускает методы @Test во всех переданных классах. Код выхода 1 — есть провалы. */
public final class TestRunner {
    public static void main(String[] args) throws Exception {
        int passed = 0, failed = 0;
        StringBuilder report = new StringBuilder();
        for (String name : args) {
            Class<?> c = Class.forName(name);
            for (Method m : c.getDeclaredMethods()) {
                if (!m.isAnnotationPresent(org.junit.Test.class)) continue;
                Object inst = Modifier.isStatic(m.getModifiers()) ? null : c.getDeclaredConstructor().newInstance();
                long t0 = System.nanoTime();
                try {
                    m.invoke(inst);
                    passed++;
                    report.append(String.format("  ok   %-58s %6.0f ms%n", c.getSimpleName() + "." + m.getName(), (System.nanoTime() - t0) / 1e6));
                } catch (InvocationTargetException e) {
                    failed++;
                    Throwable t = e.getCause();
                    report.append(String.format("  FAIL %-58s %s%n", c.getSimpleName() + "." + m.getName(), t));
                    for (StackTraceElement el : t.getStackTrace()) {
                        if (el.getClassName().startsWith("ru.kovcheg")) { report.append("         at ").append(el).append('\n'); }
                    }
                }
            }
        }
        System.out.print(report);
        System.out.println("Итого: " + passed + " пройдено, " + failed + " провалено");
        System.exit(failed == 0 ? 0 : 1);
    }
}
