# -*- coding: utf-8 -*-
"""Стили и скрипты чата для старого веб-движка.

Мессенджер ПК-версии — те же chat.js и chat.css, что у сайта. Сайт рассчитан
на свежий браузер; у PySide6 6.10 движок — Chromium 134, и страница идёт как
есть. Замены нужны только старому движку — Chromium 83 (QtWebEngine 5.15 прежней
сборки на PyQt5). Чего в нём нет и чем это заменяется перед загрузкой страницы:

  gap у flex-контейнера (84)   → отступы у соседних элементов
  :focus-visible (86)          → класс kov-fv, его ставит скрипт при работе с клавиатуры
  inset (87)                   → top / right / bottom / left
  aspect-ratio (88)            → высота по ширине, считает скрипт (ResizeObserver)
  :not(a, b), :is(…) (88)      → :not(a):not(b), список селекторов
  replaceChildren (86)         → короткая замена в скрипте

Версия движка определяется при запуске, правка делается только для того,
чего в нём действительно нет. В новом движке страница идёт как есть.
"""

import json
import re

FLEX = ("flex", "inline-flex")


# ── разбор CSS ───────────────────────────────────────────────────────────────

class Rule:
    """Обычное правило: селекторы и объявления [(свойство, значение, important)]."""

    def __init__(self, selectors, decls):
        self.selectors = selectors
        self.decls = decls

    def get(self, prop):
        for p, v, _imp in reversed(self.decls):
            if p == prop:
                return v
        return None


class At:
    """@media / @supports — с вложенными правилами; прочие (@keyframes, @property…) — как текст."""

    def __init__(self, prelude, children=None, raw=None):
        self.prelude = prelude
        self.children = children
        self.raw = raw


def _strip_comments(css):
    out, i, n = [], 0, len(css)
    while i < n:
        c = css[i]
        if c in "\"'":
            j = i + 1
            while j < n and css[j] != c:
                j += 2 if css[j] == "\\" else 1
            out.append(css[i:j + 1])
            i = j + 1
        elif css.startswith("/*", i):
            j = css.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _block_end(css, i):
    """Индекс закрывающей скобки блока, открытого перед i."""
    depth, n = 1, len(css)
    while i < n:
        c = css[i]
        if c in "\"'":
            j = i + 1
            while j < n and css[j] != c:
                j += 2 if css[j] == "\\" else 1
            i = j + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def split_top(text, sep=","):
    """Разбить по разделителю вне скобок и кавычек."""
    parts, depth, cur, quote = [], 0, [], None
    for c in text:
        if quote:
            cur.append(c)
            if c == quote:
                quote = None
            continue
        if c in "\"'":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        if c == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    return parts


def _decls(body):
    out = []
    for part in split_top(body, ";"):
        if ":" not in part:
            continue
        prop, value = part.split(":", 1)
        prop = prop.strip().lower()
        value = value.strip()
        if not prop:
            continue
        imp = False
        m = re.search(r"!\s*important\s*$", value, re.I)
        if m:
            imp = True
            value = value[:m.start()].strip()
        out.append((prop, value, imp))
    return out


def parse(css, _clean=True):
    if _clean:
        css = _strip_comments(css)
    nodes, i, n = [], 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j < 0:
            break
        head = css[i:j].strip()
        # «@charset …;» и прочие однострочные @-правила перед блоком
        while head.startswith("@") and ";" in head and not head.lower().startswith(("@media", "@supports")):
            head = head.split(";", 1)[1].strip()
        end = _block_end(css, j + 1)
        body = css[j + 1:end]
        if head.startswith("@"):
            low = head.lower()
            if low.startswith("@media") or low.startswith("@supports"):
                nodes.append(At(head, parse(body, False)))
            else:
                nodes.append(At(head, raw=body))
        elif head:
            sels = [s.strip() for s in split_top(head) if s.strip()]
            nodes.append(Rule(sels, _decls(body)))
        i = end + 1
    return nodes


def dump(nodes, indent=""):
    out = []
    for nd in nodes:
        if isinstance(nd, Rule):
            if not nd.selectors or not nd.decls:
                continue
            body = "; ".join("%s: %s%s" % (p, v, " !important" if imp else "") for p, v, imp in nd.decls)
            out.append("%s%s { %s; }" % (indent, ", ".join(nd.selectors), body))
        elif nd.children is not None:
            inner = dump(nd.children, indent + "  ")
            if inner.strip():
                out.append("%s%s {\n%s\n%s}" % (indent, nd.prelude, inner, indent))
        else:
            out.append("%s%s {%s}" % (indent, nd.prelude, nd.raw))
    return "\n".join(out)


def walk(nodes, media=""):
    """Все обычные правила с условием @media, внутри которого они лежат."""
    for nd in nodes:
        if isinstance(nd, Rule):
            yield nd, media
        elif nd.children is not None:
            yield from walk(nd.children, (media + " and " if media else "") + nd.prelude)


# ── правки ───────────────────────────────────────────────────────────────────

def _split_ws_top(value):
    out, depth, cur = [], 0, []
    for c in value:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c.isspace() and depth == 0:
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(c)
    if cur:
        out.append("".join(cur))
    return out


def _expand_inset(value):
    parts = _split_ws_top(value)
    if not parts or len(parts) > 4:
        return None
    t = parts[0]
    r = parts[1] if len(parts) > 1 else t
    b = parts[2] if len(parts) > 2 else t
    lf = parts[3] if len(parts) > 3 else r
    return [("top", t), ("right", r), ("bottom", b), ("left", lf)]


def _fix_inset(rule):
    out, changed = [], False
    for p, v, imp in rule.decls:
        if p == "inset":
            exp = _expand_inset(v)
            if exp:
                out.extend((k, x, imp) for k, x in exp)
                changed = True
                continue
        out.append((p, v, imp))
    rule.decls = out
    return changed


def _expand_is(sel):
    """«a :is(b, c) d» → [«a b d», «a c d»]; :where — так же."""
    m = re.search(r":(is|where)\(", sel)
    if not m:
        return [sel]
    start = m.end()
    depth, k = 1, start
    while k < len(sel) and depth:
        if sel[k] == "(":
            depth += 1
        elif sel[k] == ")":
            depth -= 1
        k += 1
    inner = sel[start:k - 1]
    out = []
    for alt in split_top(inner):
        out.extend(_expand_is(sel[:m.start()] + alt.strip() + sel[k:]))
    return out


def _split_not(sel):
    """«:not(a, b)» → «:not(a):not(b)»."""
    def repl(m):
        alts = [a.strip() for a in split_top(m.group(1))]
        return "".join(":not(%s)" % a for a in alts if a)
    prev = None
    while prev != sel:
        prev = sel
        sel = re.sub(r":not\(((?:[^()]|\([^()]*\))*,(?:[^()]|\([^()]*\))*)\)", repl, sel)
    return sel


def _ratio(value):
    v = value.strip().lower()
    if v == "auto":
        return 0.0
    m = re.match(r"^(?:auto\s+)?([\d.]+)\s*(?:/\s*([\d.]+))?$", v)
    if not m:
        return None
    a = float(m.group(1))
    b = float(m.group(2)) if m.group(2) else 1.0
    return a / b if b else None


def _gap_values(rule):
    gap = rule.get("gap")
    row = col = None
    if gap:
        parts = _split_ws_top(gap)
        row = parts[0]
        col = parts[1] if len(parts) > 1 else parts[0]
    row = rule.get("row-gap") or row
    col = rule.get("column-gap") or col
    return row, col


def _zero(v):
    return v is None or re.match(r"^0(px|rem|em|%)?$", v.strip()) is not None


_SIDES = ("top", "right", "bottom", "left")


def _margin_sides(rule):
    """Отступы правила по сторонам: {сторона: (значение, important)} — из margin и margin-*."""
    out = {}
    for p, v, imp in rule.decls:
        if p == "margin":
            parts = _split_ws_top(v)
            if not 1 <= len(parts) <= 4:
                continue
            t = parts[0]
            r = parts[1] if len(parts) > 1 else t
            b = parts[2] if len(parts) > 2 else t
            lf = parts[3] if len(parts) > 3 else r
            for side, x in zip(_SIDES, (t, r, b, lf)):
                out[side] = (x, imp)
        elif p.startswith("margin-") and p[7:] in _SIDES:
            out[p[7:]] = (v, imp)
    return out


def _last_compound(sel):
    """«#kovChat .list > .item:hover» → («#kovChat .list», «.item:hover»)."""
    s = sel.strip()
    depth, quote, cut = 0, None, 0
    for i, c in enumerate(s):
        if quote:
            if c == quote:
                quote = None
            continue
        if c in "\"'":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif depth == 0 and (c.isspace() or c in ">+~"):
            cut = i + 1
    return re.sub(r"[\s>+~]+$", "", s[:cut]).strip(), s[cut:].strip()


def _walk_chain(nodes, chain=()):
    for nd in nodes:
        if isinstance(nd, Rule):
            yield nd, list(chain)
        elif nd.children is not None:
            yield from _walk_chain(nd.children, tuple(chain) + (nd.prelude,))


def _media(chain):
    """Цепочка @media → одно условие «@media A and B» (пустая — без условия)."""
    conds = [re.sub(r"^@?media\s+", "", c.strip(), flags=re.I) for c in chain if c.strip()]
    return "@media " + " and ".join(conds) if conds else ""


def _own_plus_gap(nodes, containers):
    """Свой отступ ребёнка + gap. У браузера gap прибавляется к отступу элемента,
    а замена «> * + * { margin }» им перекрывается (у .date-separator margin: 10px 0 —
    и зазор пропадал). Для каждого правила с отступом на той же стороне — правило
    «контейнер > * + элемент» с суммой; оно точнее исходного и стоит в конце таблицы."""
    out = {}
    for rule, chain in _walk_chain(nodes):
        sides = _margin_sides(rule)
        if not sides:
            continue
        for s_sel, s_chain, side, gap in containers:
            if side not in sides:
                continue
            value, imp = sides[side]
            low = value.strip().lower()
            if "auto" in low or low in ("inherit", "initial", "unset", "revert"):
                continue
            total = gap if _zero(value) else "calc(%s + %s)" % (value, gap)
            for t in rule.selectors:
                if "::" in t:
                    continue
                pre, last = _last_compound(t)
                if not last or (pre and pre not in s_sel):
                    continue
                key = _media(list(s_chain) + chain)
                out.setdefault(key, []).append(Rule([s_sel + " > * + " + last], [("margin-" + side, total, imp)]))
    tail = []
    for key, rules in out.items():
        tail.append(At(key, rules) if key else None)
        if not key:
            tail.pop()
            tail.extend(rules)
    return tail


def transform(css, major, force=False):
    """CSS для Chromium major. Возвращает (css, сведения для скрипта страницы)."""
    def need(v):
        return force or major < v

    nodes = parse(css)
    info = {"aspect": [], "focus_visible": need(86), "flex_gap": [], "unknown_gap": []}

    # что известно о каждом селекторе: display / flex-direction / flex-wrap
    facts = {}
    for rule, media in walk(nodes):
        for s in rule.selectors:
            f = facts.setdefault((s, media), {})
            for prop in ("display", "flex-direction", "flex-wrap", "flex-flow"):
                v = rule.get(prop)
                if v:
                    f[prop] = v

    def fact(sel, media, prop):
        for key in ((sel, media), (sel, "")):
            v = facts.get(key, {}).get(prop)
            if v:
                return v
        return None

    hoisted = {}            # условие @media → правила-отступы (встают в начало таблицы)
    containers = []         # (селектор, цепочка @media, сторона, gap) — для суммы со своим отступом

    def fix_list(items, media, chain):
        out = []
        for nd in items:
            if isinstance(nd, At):
                if nd.children is not None:
                    inner = (media + " and " if media else "") + nd.prelude
                    nd.children = fix_list(nd.children, inner, chain + [nd.prelude])
                out.append(nd)
                continue
            out.extend(fix_rule(nd, media, chain))
        return out

    def fix_rule(rule, media, chain):
        if need(88):
            sels = []
            for s in rule.selectors:
                for e in _expand_is(s):
                    sels.append(_split_not(e))
            rule.selectors = sels
        if need(86):
            rule.selectors = [s.replace(":focus-visible", ".kov-fv") for s in rule.selectors]
        if need(87):
            _fix_inset(rule)
        if need(88) and rule.get("aspect-ratio") is not None:
            r = _ratio(rule.get("aspect-ratio"))
            if r is not None:
                for s in rule.selectors:
                    info["aspect"].append([s, round(r, 6)])
            rule.decls = [d for d in rule.decls if d[0] != "aspect-ratio"]
        if not (need(84) and any(d[0] in ("gap", "row-gap", "column-gap") for d in rule.decls)):
            return [rule]
        row, col = _gap_values(rule)
        flex_sels, other_sels = [], []
        for s in rule.selectors:
            disp = (rule.get("display") or fact(s, media, "display") or "").strip()
            if disp in FLEX:
                flex_sels.append(s)
            else:
                other_sels.append(s)
                if not disp:
                    info["unknown_gap"].append(s)
        if not flex_sels:
            return [rule]
        extra = []
        for s in flex_sels:
            flow = rule.get("flex-flow") or fact(s, media, "flex-flow") or ""
            direction = (rule.get("flex-direction") or fact(s, media, "flex-direction") or
                         next((w for w in flow.split() if w.startswith(("row", "column"))), "row")).strip()
            wrap = (rule.get("flex-wrap") or fact(s, media, "flex-wrap") or
                    next((w for w in flow.split() if "wrap" in w), "nowrap")).strip()
            info["flex_gap"].append([s, media, direction, wrap, row, col])
            vertical = direction.startswith("column")
            main = row if vertical else col
            cross = col if vertical else row
            side = {"row": "left", "row-reverse": "right", "column": "top",
                    "column-reverse": "bottom"}.get(direction, "left")
            if not _zero(main):
                extra.append(Rule([s + " > * + *"], [("margin-" + side, main, False)]))
                containers.append((s, list(chain), side, main))
            if wrap.startswith("wrap") and not _zero(cross):
                edge = "left" if vertical else "top"
                extra.append(Rule([s + " > *"], [("margin-" + edge, cross, False)]))
                extra.append(Rule([s], [("margin-" + edge, "calc(-1 * %s)" % cross, False)]))
        key = " and ".join(chain)
        hoisted.setdefault(key, []).extend(extra)
        flex_rule = Rule(flex_sels, [d for d in rule.decls if d[0] not in ("gap", "row-gap", "column-gap")])
        if other_sels:
            return [Rule(other_sels, list(rule.decls)), flex_rule]
        return [flex_rule]

    nodes = fix_list(nodes, "", [])
    head = []
    for key, rules in hoisted.items():
        if not rules:
            continue
        if key:
            # вложенные условия склеиваются: «@media A» + «@media B» → «@media A and B»
            conds = [re.sub(r"^@media\s+", "", c, flags=re.I) for c in key.split(" and @")]
            conds = [re.sub(r"^@?media\s+", "", c, flags=re.I) for c in conds]
            head.append(At("@media " + " and ".join(conds), rules))
        else:
            head.extend(rules)
    tail = _own_plus_gap(nodes, containers) if containers else []
    return dump(head + nodes + tail), info


# ── версия движка ────────────────────────────────────────────────────────────

def chromium_major(user_agent=None):
    if user_agent is None:
        try:
            from PySide6.QtWebEngineCore import QWebEngineProfile
            user_agent = QWebEngineProfile.defaultProfile().httpUserAgent()
        except Exception:
            user_agent = ""
    m = re.search(r"Chrome/(\d+)", user_agent or "")
    return int(m.group(1)) if m else 83


# ── скрипт страницы: замены того, чего нет в движке ──────────────────────────

POLYFILL_JS = r"""
(function () {
  function rc() { while (this.lastChild) this.removeChild(this.lastChild); this.append.apply(this, arguments); }
  [window.Element, window.DocumentFragment, window.Document].forEach(function (C) {
    if (C && C.prototype && !C.prototype.replaceChildren) C.prototype.replaceChildren = rc;
  });
  if (!String.prototype.replaceAll) String.prototype.replaceAll = function (a, b) {
    return a instanceof RegExp ? this.replace(a, b) : this.split(a).join(typeof b === 'function' ? b(a) : b);
  };
  if (!Array.prototype.at) Array.prototype.at = function (i) { i = Math.trunc(i) || 0; if (i < 0) i += this.length; return this[i]; };
  var cfg = window.__kovCompat || {};
  if (cfg.focus_visible) {
    var kbd = false;
    document.addEventListener('keydown', function (e) { if (!e.ctrlKey && !e.metaKey && !e.altKey) kbd = true; }, true);
    ['mousedown', 'pointerdown', 'touchstart'].forEach(function (t) { document.addEventListener(t, function () { kbd = false; }, true); });
    var TEXT = 'input:not([type=button]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=range]), textarea, [contenteditable=""], [contenteditable="true"]';
    document.addEventListener('focusin', function (e) {
      var t = e.target; if (!t || !t.classList) return;
      if (kbd || (t.matches && t.matches(TEXT))) t.classList.add('kov-fv');
    }, true);
    document.addEventListener('focusout', function (e) { var t = e.target; if (t && t.classList) t.classList.remove('kov-fv'); }, true);
  }
  if (cfg.aspect && cfg.aspect.length && window.ResizeObserver) {
    var ro = new ResizeObserver(function (entries) {
      entries.forEach(function (en) {
        var el = en.target, r = el.__kovAr;
        if (!r) return;
        var w = en.contentRect.width;
        if (w > 0) el.style.height = (w / r).toFixed(2) + 'px';
      });
    });
    var scan = function () {
      var root = document.getElementById('kovChat') || document;
      var seen = new Map();
      cfg.aspect.forEach(function (a) {
        var list; try { list = root.querySelectorAll(a[0]); } catch (e) { return; }
        list.forEach(function (el) { seen.set(el, a[1]); });
      });
      seen.forEach(function (r, el) {
        if (el.__kovAr === r) return;
        el.__kovAr = r;
        if (r) ro.observe(el); else { ro.unobserve(el); el.style.height = ''; }
      });
    };
    var queued = false;
    var later = function () { if (queued) return; queued = true; requestAnimationFrame(function () { queued = false; scan(); }); };
    document.addEventListener('DOMContentLoaded', function () {
      scan();
      new MutationObserver(later).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'data-count'] });
    });
  }
})();
"""


def page_config(info):
    return "window.__kovCompat = %s;" % json.dumps({"focus_visible": bool(info.get("focus_visible")),
                                                     "aspect": info.get("aspect") or []}, ensure_ascii=False)
