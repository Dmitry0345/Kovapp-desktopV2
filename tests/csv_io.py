from kovtest import case
from kovtest.flows import download_via


# ===== выгрузка =====

@case("Выгрузка сотрудников и организаций в CSV", layer="api", order=1)
def export_api(t):
    api = t.api("director")
    for kind in ("users", "organizations"):
        response = api.get("csv/%s/export" % kind)
        t.expect_status(response, 200, "выгрузка %s" % kind)
        if response.status_code == 200:
            t.check(len(response.content) > 0, "файл выгрузки %s пустой" % kind)


@case("Скачивание CSV через раздел", layer="ui", matrix={"device": "devices"}, order=2)
def export_ui(t):
    page = t.page("director", "light", t.params["device"], path="#/csv")
    page.raw.click(".content [data-key='export']")
    page.raw.click(".content [data-key='users']")
    name, size = download_via(page, lambda: page.raw.locator(".content button", has_text="Скачать CSV").click())
    t.check(name.endswith(".csv") and size > 0, "скачан не тот файл: %s, %d байт" % (name, size))
    page.assert_clean("CSV")
