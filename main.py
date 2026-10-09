import os
import argparse
import shutil
import sys
from pathlib import Path

from kovtest import parallel, runner
from kovtest.config import env, env_bool, load_yaml, settings as load_settings
from kovtest.log import percent, write_summary
from kovtest.world import World


# ===== запуск =====

def parse():
    parser = argparse.ArgumentParser(description="Автотесты Kovapp")
    parser.add_argument("--mode", choices=["full", "external"], default="")
    parser.add_argument("--target", choices=["web", "apk", "device"], default="",
                        help="web — сайт; apk — мобильная версия на ПК; device — настоящий Android (эмулятор или телефон)")
    parser.add_argument("--sections", default="", help="разделы через запятую; all — все")
    parser.add_argument("--layers", default="", help="слои через запятую: api, ui, visual, realtime")
    parser.add_argument("--list", action="store_true", help="показать разделы и выйти")
    parser.add_argument("--demo", action="store_true", help="заполнить сервер демонстрационными данными и выйти")
    parser.add_argument("--stand", action="store_true", help="стенд: нагрузка в фоне для ручной проверки")
    parser.add_argument("--worker", type=int, default=-1, help=argparse.SUPPRESS)
    parser.add_argument("--units", default="", help=argparse.SUPPRESS)
    parser.add_argument("--run-dir", default="", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse()
    catalog = load_yaml("sections.yaml")["sections"]
    if args.list:
        print("Разделы:")
        for item in catalog:
            only = item.get("targets")
            mark = "" if not only else {("apk",): "  [только мобильная]", ("device",): "  [только устройство]"}.get(
                tuple(only), "  [только сайт]")
            print("  %-15s %s%s" % (item["key"], item["title"], mark))
        return 0
    sections = [s.strip().lower() for s in args.sections.split(",") if s.strip()] or None
    layers = [x.strip().lower() for x in args.layers.split(",") if x.strip()] or None
    settings = load_settings(args.mode, sections, layers, args.target)
    if args.demo:                       # run.cmd demo: данные для ручной проверки, без тестов
        from kovtest.demo import seed
        return seed(settings)
    if args.stand:                      # run.cmd stand: люди в фоне, человек проверяет руками
        from kovtest import stand
        return stand.main(settings)
    if settings.target == "apk" and not settings.project_dir:
        print("Для мобильной версии нужна папка проекта (web_app и mobile): PROJECT_DIR в .env")
        return 2
    if settings.target == "apk" and not Path(settings.project_dir, "mobile", "tools", "devserver.py").is_file():
        print("В папке проекта %s нет mobile/tools/devserver.py — нужна версия проекта с мобильной версией" % settings.project_dir)
        return 2
    if settings.mode == "external" and not settings.base_url:
        print("Не задан EXTERNAL_BASE_URL в .env")
        return 2
    if settings.mode == "external" and not env_bool("EXTERNAL_CONFIRM_TEST_SERVER", False):
        print("Внешний режим создаёт и удаляет данные и рассылает уведомления.")
        print("Запускайте его только на тестовом сервере и подтвердите это в .env: EXTERNAL_CONFIRM_TEST_SERVER=1")
        return 2
    if settings.mode == "external" and not (settings.admin_login and settings.admin_password):
        print("Для внешнего режима заполните EXTERNAL_ADMIN_LOGIN и EXTERNAL_ADMIN_PASSWORD в .env")
        return 2
    if settings.target == "device":
        settings.parallel = 1            # устройство одно — разделы идут по очереди
    if "@load" in settings.sections:
        settings.sections = [x.strip().lower() for x in env("LOAD_SECTIONS", "files,device,messenger,realtime,load").split(",") if x.strip()]
        settings.layers = [x.strip().lower() for x in env("LOAD_LAYERS", "api").split(",") if x.strip()]
    fits = lambda item: settings.target in (item.get("targets") or ["web", "apk"])
    # «все» — без ручных разделов (нагрузка, сжатие, резервные копии запускаются своими командами)
    chosen = [i for i in catalog if fits(i) and not i.get("manual")] if "all" in settings.sections else [i for i in catalog if i["key"] in settings.sections]
    for item in [i for i in chosen if not fits(i)]:
        print("Раздел %s не относится к %s — пропущен" % (item["key"], {"apk": "мобильной версии", "device": "тестам на устройстве"}.get(settings.target, "сайту")))
    chosen = [i for i in chosen if fits(i)]
    unknown = [s for s in settings.sections if s not in ("all", "@load") and s not in {i["key"] for i in catalog}]
    if unknown:
        print("Неизвестные разделы: %s. Список: python main.py --list" % ", ".join(unknown))
        return 2

    worker = args.worker >= 0
    orchestrate = not worker and settings.parallel > 1
    if worker:
        chosen = parallel.decode(args.units, catalog)
        world = World(settings, folder=Path(args.run_dir) / "workers" / ("w%d" % (args.worker + 1)))
    else:
        world = World(settings)
    log = world.log
    print("Журналы прогона: %s" % log.dir, flush=True)
    if not worker:
        try:
            (settings.log_root / "last-run.txt").write_text(log.dir.name, encoding="utf-8")
        except OSError:
            pass
    log.banner("Прогон автотестов Kovapp: %s, режим %s, сервер %s%s" % (
        {"apk": "мобильная версия (APK на ПК)", "device": "настоящее приложение на Android"}.get(settings.target, "сайт"),
        settings.mode, settings.base_url, (", исполнитель w%d" % (args.worker + 1)) if worker else ""))
    results = []
    extra = []
    try:
        extra.append("Модуль тестов: версия %s" % (Path(__file__).resolve().parent / "VERSION").read_text(encoding="utf-8").strip())
    except OSError:
        extra.append("Модуль тестов: версия не указана")
    if os.environ.get("ONLY", "").strip():
        extra.append("Отбор тестов (ONLY): %s — прогон неполный" % os.environ["ONLY"].strip())
    from kovtest.config import project_web_dir
    for label, folder in (("Веб-версия (сервер и сайт)", project_web_dir()), ("Android", settings.project_dir)):
        version = Path(folder, "VERSION") if folder else None
        if version is not None and version.is_file():
            extra.append("%s: версия %s" % (label, version.read_text(encoding="utf-8").strip()))
        else:
            extra.append("%s: версия не указана" % label)
    code = 0
    abort = None            # (что сорвалось, почему) — попадает в отчёт ошибкой, а не «ошибок нет»
    try:
        if not world.wait_server():
            print("Сервер %s не отвечает" % settings.app_url)
            code = 3
            abort = ("Сервер не отвечает", "%s не ответил — тесты не запускались" % settings.app_url)
        elif orchestrate:
            if settings.mode == "external" and settings.create_accounts:
                world.accounts.remove_leftovers(world.api("admin"))
            bins = parallel.plan(chosen, settings.parallel)
            extra.append("Параллельно: %d исполнителя, разделы: %s" % (
                len(bins), " | ".join(parallel.encode(b["units"]) for b in bins)))
            parallel.launch(bins, log.dir, args, log)
            results = parallel.collect(log.dir, log)
        else:
            world.start_apk()
            try:
                world.start_device()
            except Exception as e:
                from kovtest.device import DeviceError
                if not isinstance(e, DeviceError):
                    raise
                log.error("Устройство не готово: %s" % e)
                print("\nУстройство не готово: %s\nПодробно — docs/DEVICE.md, раздел «Если что-то не так»." % e)
                raise RunAborted("Подготовка устройства", "тесты не запускались: %s" % e, 2)
            if world.device is not None:
                d = world.device
                extra.append("Устройство: %s, Android %s (API %d), сервисы Google: %s, приложение %s%s" % (
                    d.model, d.release, d.sdk, "есть" if d.gms else "нет", d.app_version,
                    ", веб-файлы из проекта" if settings.device_live_web else ""))
                extra.extend(getattr(d, "notes", []))
            if world.apk is not None:
                extra.append("Мобильная версия: интерфейс APK с надстройкой (%s), экраны: %s" % (
                    world.apk.version, ", ".join("%s — %s" % (d, settings.apk_devices.get(d, d)) for d in settings.devices)))
            world.accounts.prepare()
            mode = world.detect_file_mode()
            extra.append("Доступ к файлам: %s" % {"strict": "строгий (FILE_ACCESS_STRICT=1)",
                                                   "soft": "мягкий (FILE_ACCESS_STRICT=0)"}.get(mode, "не определён"))
            runner.load_sections(chosen)
            def checkpoint(done, every=[0]):
                # отчёт после каждой проверки: оборвался прогон — видно, что успело пройти
                every[0] += 1
                try:
                    write_summary(log, settings, done, ["ПРОГОН НЕ ЗАВЕРШЁН: отчёт промежуточный, записан после %d проверок" % len(done)])
                    server_logs = env("SERVER_LOG_DIR", "")
                    if every[0] % 10 == 0 and settings.mode == "full" and server_logs and Path(server_logs).is_dir():
                        shutil.copytree(server_logs, log.dir / "server-logs", dirs_exist_ok=True)
                except Exception as e:
                    log.warn("промежуточный отчёт не записан: %s" % e)
            results = runner.run(world, chosen, checkpoint)
    except RunAborted as a:
        code = a.code
        abort = (a.title, a.text)
    except KeyboardInterrupt:
        log.error("прогон прерван")
        code = 130
        abort = ("Прогон прерван", "остановлен вручную — результаты неполные")
    except Exception as e:
        log.exception("прогон остановлен ошибкой")
        print("Прогон остановлен ошибкой — подробности в full.log")
        code = 4
        abort = ("Прогон остановлен ошибкой", "%s: %s (подробно — full.log)" % (type(e).__name__, e))
    finally:
        if settings.cleanup and not orchestrate:
            try:
                world.data.cleanup()
            except Exception:
                log.exception("уборка тестовых данных не удалась")
        if not orchestrate:
            try:
                world.accounts.teardown()
            except Exception:
                log.exception("удаление учётных записей не удалось")
        if world._browsers is not None and world._browsers.used:
            extra.append("Запущенные браузеры: " + world._browsers.channel_used)
            if world._browsers.h264:
                extra.append("Видео H.264: " + ", ".join("%s — %s" % (k, "да" if v else "нет")
                                                          for k, v in world._browsers.h264.items()))
            if world._browsers.broken:
                extra.append("Не запустились: " + "; ".join(world._browsers.broken.values()))
        server_logs = env("SERVER_LOG_DIR", "")
        if settings.mode == "full" and server_logs and Path(server_logs).is_dir() and not worker:
            try:
                shutil.copytree(server_logs, log.dir / "server-logs", dirs_exist_ok=True)
                extra.append("Журналы сервера: server-logs/")
            except OSError as e:
                log.warn("журналы сервера не скопированы: %s" % e)
        if abort:
            from kovtest.runner import CaseResult
            results = list(results) + [CaseResult(section="подготовка", title=abort[0], layer="setup",
                                                  params={}, status="failed", messages=[abort[1]])]
        summary = write_summary(log, settings, results, extra)
        world.close()
        log.close()
    passed = sum(1 for r in results if r.status == "passed")
    failed = sum(1 for r in results if r.status == "failed")
    print("\nИтог: %s выполнено успешно, ошибок %d. Краткий отчёт: %s" % (
        percent(passed, passed + failed), failed, summary))
    if code:
        return code
    return 0 if failed == 0 else 1


class RunAborted(Exception):
    """Прогон не может продолжаться: причина пойдёт в отчёт ошибкой."""
    def __init__(self, title: str, text: str, code: int):
        super().__init__(text)
        self.title, self.text, self.code = title, text, code


if __name__ == "__main__":
    sys.exit(main())
