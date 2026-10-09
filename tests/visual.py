from kovtest import case
from kovtest.config import env_list, load_yaml
from kovtest.flows import hash_of, segmented

UI = load_yaml("ui.yaml")
SECTIONS = UI["sections"]
ACCESS = UI["access"]
CRAWLER = UI["crawler"]
VISUAL_ROLES = env_list("VISUAL_ROLES") or UI["visual"]["roles"]
CRAWLER_ROLES = env_list("CRAWLER_ROLES") or CRAWLER["roles"]


# ===== куда попадает роль =====

def target_route(role, key):
    if key == "projects" and role in ACCESS["projects_hidden_for"]:
        return "#/tasks"
    if key == "csv" and role not in ACCESS["csv_only"]:
        return "#/tasks"
    return SECTIONS[key]["route"]


def combos(t):
    return [(theme, device) for theme in t.settings.themes for device in t.settings.devices]


def audit_section(t, key):
    role = t.params["role"]
    route = SECTIONS[key]["route"]
    for theme, device in combos(t):
        page = t.page(role, theme, device, path=route)
        expected = target_route(role, key)
        landed = hash_of(page)
        t.check(landed.startswith(expected), "%s [%s/%s]: ожидался адрес %s, открылся %s" % (
            SECTIONS[key]["title"], theme, device, expected, landed))
        if key == "tasks_archive" and expected == route:
            segmented(page, "archived")
            page.wait_ready()
        page.audit("%s_%s" % (key, role))
        page.assert_clean(SECTIONS[key]["title"])
        t.world.browsers.release(page)
        t.pages.remove(page)


def register_audit(key, order):
    @case("Вёрстка раздела «%s»%s" % (SECTIONS[key]["title"], " (архив)" if key == "tasks_archive" else ""),
          layer="visual", matrix={"engine": "engines", "role": VISUAL_ROLES}, order=order)
    def _audit(t, _key=key):
        audit_section(t, _key)
    return _audit


for _i, _key in enumerate(SECTIONS):
    register_audit(_key, 10 + _i)


# ===== обход кнопок =====

COLLECT = """(skip) => {
  const seen = {};
  const out = [];
  const bad = skip.selectors.join(',');
  const shown = el => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    let box = { l: r.left, t: r.top, r: r.right, b: r.bottom };
    let node = el.parentElement;
    while (node && node !== document.body) {
      const st = getComputedStyle(node);
      if (/(hidden|clip)/.test(st.overflowX + st.overflowY) || getComputedStyle(node).visibility === 'hidden') {
        const p = node.getBoundingClientRect();
        box = { l: Math.max(box.l, p.left), t: Math.max(box.t, p.top), r: Math.min(box.r, p.right), b: Math.min(box.b, p.bottom) };
      }
      node = node.parentElement;
    }
    box = { l: Math.max(box.l, 0), t: box.t, r: Math.min(box.r, innerWidth), b: box.b };
    const cx = (r.left + r.right) / 2;
    if (cx < 0 || cx > innerWidth) return false;
    return Math.max(0, box.r - box.l) * Math.max(0, box.b - box.t) >= 0.9 * r.width * r.height;
  };
  for (const el of document.querySelectorAll('.content button, .content [role=button], .top-bar button')) {
    if (el.offsetParent === null || el.disabled || !shown(el)) continue;
    if (bad && el.closest(bad)) continue;
    if (el.closest('.modal-root')) continue;
    const text = (el.textContent || '').replace(/\\s+/g, ' ').trim();
    const title = el.getAttribute('title') || el.getAttribute('aria-label') || '';
    if (skip.texts.some(s => text.includes(s) || title.includes(s))) continue;
    const key = text + '|' + title;
    seen[key] = (seen[key] || 0) + 1;
    out.push({ text, title, n: seen[key] - 1 });
  }
  return out;
}"""

CLICK = """(d) => {
  let n = 0;
  for (const el of document.querySelectorAll('.content button, .content [role=button], .top-bar button')) {
    if (el.offsetParent === null || el.disabled || el.closest('.modal-root')) continue;
    const text = (el.textContent || '').replace(/\\s+/g, ' ').trim();
    const title = el.getAttribute('title') || el.getAttribute('aria-label') || '';
    if (text !== d.text || title !== d.title) continue;
    if (n === d.n) {
      el.scrollIntoView({ block: 'center', inline: 'center' });
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      window.__kovMut = 0;
      if (!window.__kovObs) {
        window.__kovObs = new MutationObserver(list => { window.__kovMut += list.length; });
        window.__kovObs.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
      }
      return { found: true, hittable: !!hit && (hit === el || el.contains(hit)), x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }
    n += 1;
  }
  return { found: false };
}"""


def crawl_combos(t):
    wanted = CRAWLER.get("combos") or []
    pairs = [tuple(x.split("-", 1)) for x in wanted if "-" in x]
    return [p for p in pairs if p[0] in t.settings.themes and p[1] in t.settings.devices] or combos(t)


def crawl(t, key):
    role = t.params["role"]
    route = SECTIONS[key]["route"]
    skip = {"texts": CRAWLER["skip_text"], "selectors": CRAWLER["skip_selector"]}
    for theme, device in crawl_combos(t):
        page = t.page(role, theme, device, path=route)
        if not hash_of(page).startswith(target_route(role, key)):
            t.world.browsers.release(page)
            t.pages.remove(page)
            continue
        buttons = page.raw.evaluate(COLLECT, skip)[:CRAWLER["max_buttons"]]
        t.log.info("обход %s [%s/%s/%s]: кнопок %d" % (key, role, theme, device, len(buttons)))
        for item in buttons:
            name = item["text"] or item["title"] or "без подписи"
            state = page.raw.evaluate(CLICK, item)
            if not state.get("found"):
                continue
            if not state.get("hittable"):
                t.fail("%s [%s/%s]: кнопку «%s» нельзя нажать — её перекрывает другой элемент" % (
                    SECTIONS[key]["title"], theme, device, name))
                continue
            before_pages = len(page.context.pages)
            before_hash = hash_of(page)
            try:
                page.raw.mouse.click(state["x"], state["y"])
            except Exception as e:
                t.fail("%s [%s/%s]: нажатие «%s» не прошло: %s" % (SECTIONS[key]["title"], theme, device, name, e))
                continue
            page.raw.wait_for_timeout(450)
            changed = page.raw.evaluate("() => window.__kovMut || 0")
            popups = page.context.pages[before_pages:]
            if not changed and hash_of(page) == before_hash and not popups:
                t.warn("%s [%s/%s]: кнопка «%s» ничего не изменила на экране" % (SECTIONS[key]["title"], theme, device, name))
            for extra in popups:
                extra.close()
            if page.raw.locator(".modal-root, .kov-sheet-root").count():
                page.audit("%s_dialog_%s" % (key, role), shot=False)
            page.close_overlays()      # окна — Escape, шторки снизу на телефоне — «×»
            if page.raw.locator(".side-panel.open, .side-menu.open").count():
                page.raw.keyboard.press("Escape")
                page.raw.wait_for_timeout(200)
            if hash_of(page) != before_hash:
                page.goto(route)
        page.assert_clean("обход кнопок: " + SECTIONS[key]["title"])
        t.world.browsers.release(page)
        t.pages.remove(page)


def register_crawl(key, order):
    @case("Кнопки раздела «%s» отвечают на нажатие" % SECTIONS[key]["title"], layer="visual",
          matrix={"engine": "engines", "role": CRAWLER_ROLES}, order=order)
    def _crawl(t, _key=key):
        crawl(t, _key)
    return _crawl


for _i, _key in enumerate(k for k in SECTIONS if k != "tasks_archive"):
    register_crawl(_key, 100 + _i)
