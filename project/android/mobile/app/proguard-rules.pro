# Классы, которые система создаёт по имени из манифеста, R8 сохраняет сам.
# Мост на WebMessageListener не использует отражение — отдельных правил не нужно.

# Убираем отладочный вывод из рабочей сборки
-assumenosideeffects class android.util.Log {
    public static int v(...);
    public static int d(...);
    public static int i(...);
}
