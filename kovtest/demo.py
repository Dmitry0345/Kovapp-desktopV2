"""Демонстрационные данные для ручной проверки (run.cmd demo): живые названия, без меток автотестов.

Каждый блок создаётся отдельно: если один не удался, остальные всё равно будут; в конце — что не получилось.
"""
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from .data import Data
from .ws import Socket

MONT, TO, BUH, CONTRACT, STOCK = "Монтажный отдел", "Отдел ТО", "Бухгалтерия", "Договорной отдел", "Склад"


class DemoData(Data):
    def name(self, text: str) -> str:
        return text                                  # без метки «[АТ…]» — как у настоящих пользователей


def day(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


class Demo:
    def __init__(self, world):
        self.w = world
        self.d = DemoData(world)
        self.failed = []
        self.projects = {}
        self.tasks = {}

    # ----- служебное -----

    def api(self, role):
        return self.w.api(role)

    def uid(self, role) -> int:
        return self.w.accounts.get(role).id

    def file(self, kind: str, name: str):
        _, content, ctype = self.w.media.get(kind)
        return ("files", (name, content, ctype))

    def step(self, title: str, fn) -> None:
        try:
            fn()
            print("  ✓ " + title, flush=True)
        except Exception as e:
            text = (str(e).splitlines() or [type(e).__name__])[0][:200]
            self.failed.append("%s: %s" % (title, text))
            print("  ✗ %s — %s" % (title, text), flush=True)

    def say(self, role: str, chat_id: int, lines) -> None:
        socket_ = Socket(self.w.settings.base_url, self.api(role).token, self.w.log)
        try:
            for text in lines:
                socket_.send({"type": "message", "chat_id": chat_id, "text": text, "client_id": "c" + uuid.uuid4().hex[:10]})
                time.sleep(0.6)
        finally:
            socket_.close()

    def task(self, key, role, title, stages, description, days=14, responsible=(), project=None) -> dict:
        payload = {"name": title, "description": description, "deadline": day(days), "customer_organization": None,
                   "responsible_persons_ids": [self.uid(r) for r in responsible], "assigned_employees_ids": [],
                   "stages": [{"department_name": dep, "text_description": text} for dep, text in stages]}
        if project:
            payload["project_id"] = self.projects[project]["id"]
        created = self.api(role).call("POST", "tasks", json=payload, expect=201)
        for stage in created.get("stage_details") or []:
            staff = self.d.staff_ids(stage.get("department_name") or "")
            if staff:
                self.api(role).call("PUT", "stages/%d/participants" % stage["id"], json=staff)
        self.tasks[key] = self.d.task_by_id(created["id"])
        return self.tasks[key]

    # ----- блоки данных -----

    def organizations(self):
        for title, phone, email, desc in (
                ("АО «Альфа-Строй»", "+7 (812) 555-10-20", "office@alfa-stroy.ru", "Генподрядчик, офис на Невском"),
                ("ООО «Северный квартал»", "+7 (812) 555-33-44", "info@sev-kvartal.ru", "Застройщик ЖК «Северный»"),
                ("ГБОУ «Школа № 123»", "+7 (812) 555-77-01", "school123@spb.edu.ru", "Заказчик обслуживания сигнализации")):
            self.api("director").call("POST", "organizations", json={
                "name": title, "description": desc,
                "phones": [{"phone_number": phone, "description": "Приёмная"}],
                "emails": [{"email": email, "description": "Общая"}]}, expect=201)

    def create_projects(self):
        for key, role, title, org, resp, days, desc in (
                ("north", "director", "Видеонаблюдение ЖК «Северный»", "ООО «Северный квартал»", "head_mont", 45,
                 "42 камеры, 3 корпуса, серверная в корпусе 1"),
                ("alfa", "contract1", "СКУД офиса «Альфа»", "АО «Альфа-Строй»", "head_mont", 30,
                 "Турникеты на проходной, 12 дверей с картами доступа"),
                ("school", "director", "Пожарная сигнализация школы № 123", "ГБОУ «Школа № 123»", "head_to", 60,
                 "Плановое обслуживание и замена датчиков")):
            self.projects[key] = self.api(role).call("POST", "projects", json={
                "name": title, "organization": org, "deadline": day(days), "description": desc,
                "responsible_id": self.uid(resp)}, expect=201)

    def project_files(self):
        pid = self.projects["north"]["id"]
        for section, files in (
                ("contracts", [self.file("pdf", "Договор 17-СК.pdf")]),
                ("project", [self.file("docx", "Пояснительная записка.docx"), self.file("xlsx", "Спецификация оборудования.xlsx")]),
                ("delivery", [self.file("jpg", "Серверная, корпус 1.jpg")]),
                ("extra", [self.file("pptx", "Презентация для заказчика.pptx")])):
            self.api("director").call("POST", "projects/%d/files" % pid, data={"section": section}, files=files, timeout=600)

    def warehouse(self):
        pid = self.projects["north"]["id"]
        for row, (name, qty, unit) in enumerate((("Камера IP 4 Мп купольная", 42, "шт"), ("Кабель UTP cat.5e", 1500, "м"),
                                                 ("Коммутатор PoE на 16 портов", 4, "шт")), start=1):
            self.api("director").call("PATCH", "projects/%d/warehouse/%d" % (pid, row),
                                      json={"fields": ["name", "quantity", "unit"], "name": name, "quantity": qty, "unit": unit})
        # выдача монтажнику — ждёт его подтверждения
        self.api("stock").call("POST", "projects/%d/warehouse/1/issue" % pid,
                               json={"quantity": 6, "receiver_id": self.uid("worker_mont")})

    def create_tasks(self):
        big = self.task("cameras", "director", "Монтаж видеонаблюдения, корпус 1",
                        [(CONTRACT, "Подписать договор и приложение со спецификацией"),
                         (MONT, "Смонтировать 14 камер и серверную стойку"),
                         (BUH, "Закрыть акты, выставить счёт заказчику")],
                        "Корпус 1, подъезды 1–3. Доступ на объект с 8:00, пропуска у охраны.", 20,
                        responsible=("head_mont",), project="north")
        self.d.complete_stage("director", big, 1)     # договор подписан — работа у монтажников
        self.task("skud", "director", "Пусконаладка СКУД на проходной",
                  [(MONT, "Подключить турникеты и контроллеры"), (TO, "Проверить пропуска, обучить охрану")],
                  "Работы вечером, после 19:00 — днём проходная занята.", 7, responsible=("head_mont",), project="alfa")
        self.task("fire", "head_to", "Плановое ТО пожарной сигнализации",
                  [(TO, "Проверить 86 датчиков, заменить неисправные")],
                  "Школа: работы только в субботу.", 3, responsible=("head_to",), project="school")
        self.task("cable", "director", "Закупка кабеля и крепежа",
                  [(BUH, "Оплатить счёт поставщика"), (STOCK, "Принять и разместить на складе")],
                  "Счёт от 12.09, поставщик «ЭлектроСнаб».", 10, responsible=("head_buh",))
        self.task("urgent", "head_mont", "Замена камеры на въезде",
                  [(MONT, "Камера на въезде не показывает с утра — заменить")],
                  "Срочно: жалоба охраны.", 1, responsible=("head_mont",))

    def task_files(self):
        big = self.tasks["cameras"]
        stage = [s for s in big["stage_details"] if s["department_name"] == MONT][0]
        self.api("director").call("POST", "upload", data={"task_id": str(big["id"])},
                                  files=[self.file("pdf", "Схема размещения камер.pdf")], timeout=600)
        self.api("worker_mont").call("POST", "upload", data={"task_id": str(big["id"]), "stage_detail_id": str(stage["id"])},
                                     files=[self.file("jpg", "Подъезд 2, до монтажа.jpg"), self.file("docx", "Акт скрытых работ.docx")],
                                     timeout=600)

    def archived_task(self):
        done = self.task("done", "director", "Обследование объекта «Северный»",
                         [(MONT, "Осмотреть корпуса и составить перечень работ")],
                         "Выполнено, отчёт в файлах проекта.", 5, responsible=("head_mont",), project="north")
        self.d.complete_stage("director", done, 1)
        self.api("director").call("POST", "tasks/%d/archive" % done["id"])

    def template(self):
        self.api("director").call("POST", "templates", json={
            "template_name": "Видеонаблюдение под ключ", "num_stages": 3,
            "departments": ", ".join([CONTRACT, MONT, BUH])}, expect=201)

    def chats(self):
        group = self.d.group_chat("head_mont", ["worker_mont", "worker_mont2", "stock"], "Монтаж «Северный»")
        self.say("head_mont", group["id"], ["Коллеги, завтра начинаем корпус 1. Сбор в 8:30 у КПП."])
        self.say("worker_mont", group["id"], ["Понял. Перфоратор и стремянку возьму со склада."])
        self.say("stock", group["id"], ["Камеры и кабель на складе, выдам по заявке в приложении."])
        self.say("worker_mont2", group["id"], ["Я на подъезде 2, фото серверной скину вечером."])
        self.d.send_chat_files("worker_mont2", group["id"], ("jpg",), "Серверная, корпус 1")
        private = self.d.private_chat("worker_mont", "head_mont")
        self.say("worker_mont", private["id"], ["Добрый день! На въезде камера не показывает."])
        self.say("head_mont", private["id"], ["Видел, завёл задачу. Сможешь сегодня заменить?"])
        self.say("worker_mont", private["id"], ["Да, после обеда."])
        contract = self.d.private_chat("director", "contract1")
        self.say("director", contract["id"], ["Договор с «Альфа-Строй» согласовали?"])
        self.say("contract1", contract["id"], ["Да, подписанный скан загрузила в проект."])

    def equipment(self):
        items = [self.d.equipment_item(title, qty) for title, qty in (
            ("Перфоратор Bosch GBH 2-26", 5), ("Стремянка на 8 ступеней", 3),
            ("Тестер витой пары", 2), ("Шуруповёрт Makita", 6))]
        self.api("stock").call("POST", "equipment/%d/issue" % items[0]["id"],
                               json={"receiver_id": self.uid("worker_mont"), "quantity": 1}, expect=201)
        issued = self.api("stock").call("POST", "equipment/%d/issue" % items[2]["id"],
                                        json={"receiver_id": self.uid("worker_mont2"), "quantity": 1}, expect=201)
        self.api("worker_mont2").call("POST", "equipment/issues/%d/confirm" % issued["id"])

    def notifications(self):
        self.api("director").call("POST", "notifications", json={
            "user_ids": [self.uid(r) for r in ("head_mont", "worker_mont", "worker_mont2")],
            "message": "Планёрка по объекту «Северный» — в пятницу в 10:00", "target_section": "notifications"})

    # ----- всё вместе -----

    def run(self) -> None:
        print("Заполняю сервер демонстрационными данными:", flush=True)
        self.step("организации", self.organizations)
        self.step("проекты", self.create_projects)
        if self.projects:
            self.step("файлы проекта во всех разделах", self.project_files)
            self.step("склад проекта и выдача монтажнику", self.warehouse)
        self.step("задачи на разных этапах", self.create_tasks)
        if "cameras" in self.tasks:
            self.step("файлы задачи и этапа", self.task_files)
        self.step("задача в архиве", self.archived_task)
        self.step("шаблон задачи", self.template)
        self.step("чаты с перепиской и фото", self.chats)
        self.step("оборудование и выдачи", self.equipment)
        self.step("уведомление от директора", self.notifications)

    def accounts_text(self) -> str:
        lines = ["Учётные записи (пароль у всех ролей одинаковый, у администратора — свой):", ""]
        rows = []
        for key in self.w.accounts.keys():
            acc = self.w.accounts.get(key)
            rows.append((acc.title, acc.department, acc.login, acc.password))
        width = max(len(r[0]) for r in rows) + 2
        for title, dep, login, password in rows:
            lines.append("  %-*s %-18s логин: %-18s пароль: %s" % (width, title, dep, login, password))
        return "\n".join(lines)


def seed(settings) -> int:
    from .world import World
    world = World(settings)
    try:
        if not world.wait_server():
            print("Сервер %s не отвечает" % settings.base_url)
            return 3
        world.accounts.prepare()
        demo = Demo(world)
        demo.run()
        text = demo.accounts_text()
        print("\n" + text)
        target = Path(__file__).resolve().parent.parent / "logs" / "demo-accounts.txt"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text + "\n", encoding="utf-8")
        except OSError:
            pass
        if demo.failed:
            print("\nНе создалось (остальное на месте):\n  " + "\n  ".join(demo.failed))
        return 0
    finally:
        world.close()
