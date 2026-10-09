"""Итог прогона на устройстве: приложение не падало ни разу (журнал аварий Android за весь прогон)."""
from kovtest import case


@case("Приложение не падало за весь прогон", layer="device", order=1)
def no_crashes_whole_run(t):
    d = t.world.device
    if d is None:
        t.skip("только для тестов на устройстве (run.cmd device)")
    crashes = d.crashes()
    for crash in crashes:
        t.fail("приложение упало:\n" + crash[:1200])
    if not crashes:
        t.step("падений нет")
