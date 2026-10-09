package ru.kovcheg.kovapp;

import ru.kovcheg.kovapp.files.TransferService;

import android.os.Build;

import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import ru.kovcheg.kovapp.core.B64;
import ru.kovcheg.kovapp.core.Json;
import ru.kovcheg.kovapp.media.MediaJobs;
import ru.kovcheg.kovapp.notify.Notifier;
import ru.kovcheg.kovapp.push.PushManager;
import ru.kovcheg.kovapp.web.Bridge;
import ru.kovcheg.kovapp.web.BridgeProtocol;
import ru.kovcheg.kovapp.web.NavigationPolicy;

/**
 * Команды, которые страница может попросить у приложения. Каждая проверяет
 * аргументы; к файлам телефона — только через метки выбранных файлов.
 */
final class NativeCommands {
    /** Записи в «Загрузки» идут строго по очереди */
    private static final ExecutorService DOWNLOADS = Executors.newSingleThreadExecutor();
    private static final String JOB = "[A-Za-z0-9_-]{1,32}";
    private static final String HANDLE = "[0-9a-f]{32}";

    private final MainActivity activity;
    private final Bridge bridge;
    private final boolean mainWindow;

    NativeCommands(MainActivity activity, Bridge bridge, boolean mainWindow) {
        this.activity = activity;
        this.bridge = bridge;
        this.mainWindow = mainWindow;
    }

    /** Состояние для страницы при загрузке (native/kovapp-state.js) и в ответ на hello */
    static Map<String, Object> state() {
        App app = App.get();
        PushManager pm = app.push();
        return Json.map(
                "platform", "android",
                "app", BuildConfig.VERSION_NAME,
                "build", BuildConfig.VERSION_CODE,
                "flavor", "all",          // одна сборка: Google или собственный канал — решает телефон
                "sdk", Build.VERSION.SDK_INT,
                "permission", pm.permission(),
                "push", pm.state(),
                "features", Json.map("video", true, "hash", true, "image", true, "downloads", true));
    }

    static String stateScript() {
        return "window.__kovNativeInit=" + Json.stringify(state()) + ";\n";
    }

    private static void io(Runnable r) { App.get().io(r); }

    private static String route(String url) { return NavigationPolicy.isSafeRoute(url) ? url : "#/notifications"; }

    private static void checkMatches(String v, String re, String what) {
        if (v == null || !v.matches(re)) throw new BridgeProtocol.BadRequest(0, "неверный " + what);
    }

    void registerAll() {
        final App app = App.get();
        final MediaJobs media = app.media();
        final PushManager push = app.push();

        bridge.register("hello", (a, r) -> {
            r.ok(state());
            if (mainWindow) activity.onPageHello();
        });

        // ── сеанс ──
        bridge.register("session.set", (a, r) -> {
            String host = a.str("host", 300), token = a.str("token", 4096);
            long user = a.num("userId", 1, Long.MAX_VALUE);
            // токен отдаётся только своему серверу
            if (!AppConfig.ownServer(host)) { r.fail("чужой адрес сервера"); return; }
            push.setSession(host, token, user);
            r.ok(true);
        });
        bridge.register("session.clear", (a, r) -> {
            String host = a.optStr("host", 300, null), token = a.optStr("token", 4096, null);
            if (host != null && !AppConfig.ownServer(host)) host = null;
            push.logout(host, token);
            app.wipeSession();
            r.ok(true);
        });

        // ── системные уведомления ──
        bridge.register("notify.permission", (a, r) -> r.ok(push.permission()));
        bridge.register("notify.request", (a, r) -> activity.requestNotificationPermission(result -> r.ok(result)));
        bridge.register("notify.show", (a, r) -> {
            String kind = a.optStr("kind", 32, "");
            String channel = "chat".equals(kind) ? Notifier.CH_CHAT : "group".equals(kind) ? Notifier.CH_GROUP : Notifier.CH_NOTIFY;
            String tag = a.optStr("tag", 128, "kovcheg").replaceAll("[^A-Za-z0-9_:.\\-]", "");
            r.ok(Notifier.show(activity, tag.isEmpty() ? "kovcheg" : tag, a.optStr("title", 300, "Kovapp"), a.optStr("body", 2000, ""),
                    route(a.optStr("url", 300, "")), channel));
        });
        bridge.register("notify.close", (a, r) -> { Notifier.cancel(activity, a.str("tag", 128)); r.ok(true); });
        bridge.register("notify.settings", (a, r) -> { activity.openNotificationSettings(); r.ok(true); });
        // категория «Связь с сервером»: отключив её, человек убирает значок, канал продолжает работать
        bridge.register("notify.channel", (a, r) -> { activity.openChannelSettings(Notifier.CH_SERVICE); r.ok(true); });

        // ── уведомления при закрытом приложении ──
        bridge.register("push.state", (a, r) -> io(() -> r.ok(push.stateFresh())));
        bridge.register("push.enable", (a, r) -> io(() -> {
            Map<String, Object> res = push.enable();
            r.ok(res);
            // «работа в фоне» нужна только постоянному собственному каналу; если возможен Google —
            // собственный канал временный (ждём токен), и окно Android было бы лишним
            if ("direct".equals(res.get("mode")) && !push.batteryExempt() && !push.fcmExpected()) {
                activity.runOnUiThread(activity::requestBatteryExemption);
            }
        }));
        bridge.register("push.disable", (a, r) -> io(() -> r.ok(push.disable())));
        bridge.register("push.sync", (a, r) -> io(() -> r.ok(push.sync())));
        bridge.register("push.battery", (a, r) -> { activity.requestBatteryExemption(); r.ok(true); });

        // ── оформление и окна ──
        bridge.register("ui.theme", (a, r) -> { activity.applyTheme(a.str("color", 64), mainWindow); r.ok(true); });
        bridge.register("ui.exit", (a, r) -> { activity.exitFromPage(mainWindow); r.ok(true); });
        bridge.register("open.external", (a, r) -> {
            String url = a.str("url", 4096);
            NavigationPolicy.Kind k = AppConfig.NAV.classify(url);
            if (k != NavigationPolicy.Kind.EXTERNAL && k != NavigationPolicy.Kind.HANDOFF) { r.fail("адрес не внешний"); return; }
            activity.openExternal(android.net.Uri.parse(url));
            r.ok(true);
        });

        // ── файлы и медиа ──
        bridge.register("files.resolve", (a, r) -> r.ok(media.resolve(a.list("files", 200))));
        bridge.register("media.hashTree", (a, r) -> {
            final String handle = a.str("handle", 32), job = a.str("job", 32);
            checkMatches(handle, HANDLE, "файл");
            checkMatches(job, JOB, "номер задания");
            io(() -> {
                try { r.ok(media.hashTree(handle, job, bridge::emit)); } catch (Exception e) { r.fail(e.getMessage()); }
            });
        });
        // образец файла для быстрой проверки копии: ≈0,6 МБ чтения вместо всего файла
        bridge.register("media.hashSample", (a, r) -> {
            final String handle = a.str("handle", 32);
            checkMatches(handle, HANDLE, "файл");
            io(() -> {
                try { r.ok(media.hashSample(handle)); } catch (Exception e) { r.fail(e.getMessage()); }
            });
        });
        bridge.register("media.hashRange", (a, r) -> {
            final String handle = a.str("handle", 32);
            checkMatches(handle, HANDLE, "файл");
            final long off = a.num("offset", 0, Long.MAX_VALUE), len = a.num("length", 0, 64L * 1024 * 1024);
            io(() -> {
                try { r.ok(media.hashRange(handle, off, len)); } catch (Exception e) { r.fail(e.getMessage()); }
            });
        });
        bridge.register("media.videoPlan", (a, r) -> {
            final String handle = a.str("handle", 32);
            checkMatches(handle, HANDLE, "файл");
            final Map<String, Object> edits = a.optObj("edits");
            io(() -> r.ok(media.videoPlan(handle, edits)));
        });
        bridge.register("media.videoRun", (a, r) -> {
            final String job = a.str("job", 32);
            checkMatches(job, JOB, "номер задания");
            io(() -> {
                try { r.ok(media.videoRun(job, bridge::emit)); } catch (Exception e) { r.fail(e.getMessage()); }
            });
        });
        bridge.register("media.image", (a, r) -> {
            final String handle = a.str("handle", 32);
            checkMatches(handle, HANDLE, "файл");
            final int side = (int) a.optNum("maxSide", 64, 8192, 3200);
            io(() -> {
                try { r.ok(media.image(handle, side)); } catch (Exception e) { r.fail(e.getMessage()); }
            });
        });
        bridge.register("media.cancel", (a, r) -> { media.cancel(a.str("job", 32)); r.ok(true); });
        // шторка вложений: последние фото и видео, превью, выбор, камера
        final ru.kovcheg.kovapp.files.Gallery gallery = new ru.kovcheg.kovapp.files.Gallery(activity, app.temp(), app.picked());
        bridge.register("gallery.access", (a, r) -> r.ok(gallery.access()));
        bridge.register("gallery.request", (a, r) -> activity.requestGalleryPermission(ok -> r.ok(gallery.access())));
        bridge.register("gallery.recent", (a, r) -> io(() -> r.ok(gallery.recent(
                (int) a.optNum("offset", 0, 100000, 0), (int) a.optNum("limit", 1, 200, 60)))));
        bridge.register("gallery.thumbs", (a, r) -> io(() -> r.ok(gallery.thumbs(a.list("ids", 60)))));
        bridge.register("gallery.pick", (a, r) -> io(() -> r.ok(gallery.pick(a.list("ids", 100)))));
        bridge.register("gallery.preset", (a, r) -> io(() -> r.ok(gallery.preset(a.list("ids", 100)))));
        bridge.register("camera.request", (a, r) -> activity.requestCameraPermission(a.optBool("audio", false), ok -> r.ok(ok)));
        bridge.register("media.release", (a, r) -> r.ok(media.release(a.str("url", 200))));

        // ── скачивание ──
        // отправка файлов в фоне: ход — в шторку уведомлений, «Отменить» оттуда — странице
        if (mainWindow) {
            TransferService.onCancel = () -> bridge.emit("transfer.cancel", null);
            bridge.register("transfer.update", (a, r) -> {
                TransferService.update(activity, (int) a.optNum("count", 1, 100000, 1), (int) a.optNum("percent", 0, 100, 0),
                        a.optNum("quiet", 0, 1, 0) == 1);
                r.ok(true);
            });
            bridge.register("transfer.end", (a, r) -> { TransferService.end(activity); r.ok(true); });
        }
        bridge.register("download.url", (a, r) -> {
            final String url = a.str("url", 8192), name = a.optStr("name", 300, "file");
            if (!AppConfig.ownServer(url)) { r.fail("скачивать можно только со своего сервера"); return; }
            activity.withStoragePermission(granted -> {
                if (!granted) { r.fail("Нет разрешения на сохранение файлов"); return; }
                try {
                    app.downloads().enqueueUrl(url, name, a.optStr("mime", 200, null), activity.userAgent());
                    r.ok(true);
                } catch (RuntimeException e) {
                    r.fail(e.getMessage());
                }
            });
        });
        bridge.register("download.begin", (a, r) -> {
            final String name = a.str("name", 300), mime = a.optStr("mime", 200, null);
            final long size = a.num("size", 0, ru.kovcheg.kovapp.files.Downloads.MAX_BLOB);
            activity.withStoragePermission(granted -> {
                if (!granted) { r.fail("Нет разрешения на сохранение файлов"); return; }
                DOWNLOADS.execute(() -> {
                    try { r.ok(app.downloads().begin(name, mime, size)); } catch (Exception e) { r.fail(e.getMessage()); }
                });
            });
        });
        bridge.register("download.chunk", (a, r) -> {
            final int id = (int) a.num("id", 1, Integer.MAX_VALUE);
            final String data = a.str("data", BridgeProtocol.MAX_MESSAGE);
            DOWNLOADS.execute(() -> {
                try { app.downloads().chunk(id, B64.decode(data)); r.ok(true); } catch (Exception e) { r.fail(e.getMessage()); }
            });
        });
        bridge.register("download.end", (a, r) -> {
            final int id = (int) a.num("id", 1, Integer.MAX_VALUE);
            DOWNLOADS.execute(() -> {
                try { r.ok(app.downloads().end(id)); } catch (Exception e) { r.fail(e.getMessage()); }
            });
        });
        bridge.register("download.abort", (a, r) -> {
            final int id = (int) a.num("id", 1, Integer.MAX_VALUE);
            DOWNLOADS.execute(() -> { app.downloads().abort(id); r.ok(true); });
        });
    }
}
