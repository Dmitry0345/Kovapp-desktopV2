# -*- coding: utf-8 -*-
"""Векторные иконки интерфейса.

Каждая иконка описана как набор векторных контуров в системе координат
24 x 24 и отрисовывается через QPainter прямо в QPixmap нужного размера.

За счёт этого иконки:
  * не требуют внешних файлов-ресурсов (ничего не нужно класть в images/);
  * остаются идеально чёткими при любом коэффициенте масштабирования —
    размер передаётся уже отмасштабированным через scaler.scale(),
    а контур каждый раз пересчитывается заново, а не растягивается.

Использование::

    from general_functions.vector_icons import icon_pixmap, make_icon

    button.setIcon(make_icon("edit", scaler.scale(22), "#FFFFFF"))
    label.setPixmap(icon_pixmap("phone", scaler.scale(18), "#546E7A"))
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

# Все контуры описаны в квадрате 24 x 24 (как viewBox у SVG).
VIEWBOX = 24.0

__all__ = ["VIEWBOX", "available_icons", "icon_pixmap", "make_icon"]


# ══════════════════════════════════════════════════════════════════════════════
# Вспомогательные функции рисования
# ══════════════════════════════════════════════════════════════════════════════

def _stroke(painter, path, color, width):
    """Обводит контур пером со скруглёнными концами."""
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.strokePath(path, pen)


def _fill(painter, path, color):
    painter.fillPath(path, QColor(color))


def _line(x1, y1, x2, y2):
    path = QPainterPath()
    path.moveTo(x1, y1)
    path.lineTo(x2, y2)
    return path


def _rounded(x, y, w, h, r):
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), r, r)
    return path


# ══════════════════════════════════════════════════════════════════════════════
# Сами иконки
# ══════════════════════════════════════════════════════════════════════════════

def _draw_edit(painter, color, width):
    """Карандаш — «изменить данные»."""
    body = QPainterPath()
    body.moveTo(4.3, 19.4)
    body.lineTo(5.2, 15.6)
    body.lineTo(15.4, 5.4)
    body.lineTo(18.4, 8.4)
    body.lineTo(8.2, 18.6)
    body.closeSubpath()
    _stroke(painter, body, color, width)

    _stroke(painter, _line(12.9, 7.9, 15.9, 10.9), color, width)
    _stroke(painter, _line(3.8, 21.4, 20.2, 21.4), color, width)


def _draw_trash(painter, color, width):
    """Корзина — «удалить сотрудника»."""
    _stroke(painter, _line(3.6, 6.4, 20.4, 6.4), color, width)

    handle = QPainterPath()
    handle.moveTo(9.3, 6.4)
    handle.lineTo(9.3, 4.7)
    handle.quadTo(9.3, 3.9, 10.1, 3.9)
    handle.lineTo(13.9, 3.9)
    handle.quadTo(14.7, 3.9, 14.7, 4.7)
    handle.lineTo(14.7, 6.4)
    _stroke(painter, handle, color, width)

    body = QPainterPath()
    body.moveTo(5.9, 6.4)
    body.lineTo(6.8, 19.3)
    body.quadTo(6.9, 20.6, 8.2, 20.6)
    body.lineTo(15.8, 20.6)
    body.quadTo(17.1, 20.6, 17.2, 19.3)
    body.lineTo(18.1, 6.4)
    _stroke(painter, body, color, width)

    _stroke(painter, _line(10.3, 10.0, 10.6, 17.2), color, width)
    _stroke(painter, _line(13.7, 10.0, 13.4, 17.2), color, width)


def _draw_logout(painter, color, width):
    """Дверь со стрелкой — «выход»."""
    door = QPainterPath()
    door.moveTo(13.2, 4.0)
    door.lineTo(7.3, 4.0)
    door.quadTo(5.0, 4.0, 5.0, 6.3)
    door.lineTo(5.0, 17.7)
    door.quadTo(5.0, 20.0, 7.3, 20.0)
    door.lineTo(13.2, 20.0)
    _stroke(painter, door, color, width)

    _stroke(painter, _line(10.4, 12.0, 20.2, 12.0), color, width)

    head = QPainterPath()
    head.moveTo(16.9, 8.7)
    head.lineTo(20.2, 12.0)
    head.lineTo(16.9, 15.3)
    _stroke(painter, head, color, width)


def _draw_phone(painter, color, width):
    """Телефон."""
    _stroke(painter, _rounded(7.0, 2.3, 10.0, 19.4, 2.4), color, width)
    _stroke(painter, _line(10.5, 5.5, 13.5, 5.5), color, width)

    dot = QPainterPath()
    dot.addEllipse(QPointF(12.0, 18.3), 0.95, 0.95)
    _fill(painter, dot, color)


def _draw_mail(painter, color, width):
    """Конверт."""
    _stroke(painter, _rounded(2.7, 5.0, 18.6, 14.0, 2.2), color, width)

    flap = QPainterPath()
    flap.moveTo(3.7, 6.6)
    flap.lineTo(12.0, 13.0)
    flap.lineTo(20.3, 6.6)
    _stroke(painter, flap, color, width)


def _draw_briefcase(painter, color, width):
    """Портфель — должность."""
    _stroke(painter, _rounded(2.7, 7.0, 18.6, 13.0, 2.2), color, width)

    handle = QPainterPath()
    handle.moveTo(8.6, 7.0)
    handle.lineTo(8.6, 5.5)
    handle.quadTo(8.6, 4.3, 9.8, 4.3)
    handle.lineTo(14.2, 4.3)
    handle.quadTo(15.4, 4.3, 15.4, 5.5)
    handle.lineTo(15.4, 7.0)
    _stroke(painter, handle, color, width)

    _stroke(painter, _line(2.7, 12.6, 21.3, 12.6), color, width)


def _draw_building(painter, color, width):
    """Здание — отдел."""
    _stroke(painter, _rounded(3.5, 3.3, 10.0, 17.4, 1.6), color, width)
    _stroke(painter, _rounded(13.5, 9.5, 7.0, 11.2, 1.6), color, width)
    _stroke(painter, _line(2.2, 20.7, 21.8, 20.7), color, width)

    windows = QPainterPath()
    for x, y in ((5.8, 6.3), (9.4, 6.3), (5.8, 10.1), (9.4, 10.1), (15.8, 12.5)):
        windows.addRoundedRect(QRectF(x, y, 2.2, 2.2), 0.5, 0.5)
    _stroke(painter, windows, color, width * 0.8)

    _stroke(painter, _rounded(7.4, 15.6, 2.6, 5.1, 0.6), color, width * 0.8)


def _draw_user(painter, color, width):
    """Силуэт человека — запасной вариант аватара."""
    head = QPainterPath()
    head.addEllipse(QPointF(12.0, 8.3), 3.7, 3.7)
    _stroke(painter, head, color, width)

    shoulders = QPainterPath()
    shoulders.moveTo(4.9, 20.5)
    shoulders.cubicTo(4.9, 16.3, 8.1, 14.1, 12.0, 14.1)
    shoulders.cubicTo(15.9, 14.1, 19.1, 16.3, 19.1, 20.5)
    _stroke(painter, shoulders, color, width)


def _draw_note(painter, color, width):
    """Лист с текстом — описание профиля."""
    _stroke(painter, _rounded(4.4, 3.3, 15.2, 17.4, 2.2), color, width)
    _stroke(painter, _line(8.0, 8.4, 16.0, 8.4), color, width)
    _stroke(painter, _line(8.0, 12.0, 16.0, 12.0), color, width)
    _stroke(painter, _line(8.0, 15.6, 13.0, 15.6), color, width)


def _draw_id_card(painter, color, width):
    """Бейдж — личные данные."""
    _stroke(painter, _rounded(2.7, 4.7, 18.6, 14.6, 2.2), color, width)

    head = QPainterPath()
    head.addEllipse(QPointF(8.4, 10.5), 2.1, 2.1)
    _stroke(painter, head, color, width)

    shoulders = QPainterPath()
    shoulders.moveTo(5.3, 16.2)
    shoulders.cubicTo(5.3, 13.5, 11.5, 13.5, 11.5, 16.2)
    _stroke(painter, shoulders, color, width)

    _stroke(painter, _line(14.4, 9.8, 18.6, 9.8), color, width * 0.85)
    _stroke(painter, _line(14.4, 13.4, 18.6, 13.4), color, width * 0.85)


def _draw_close(painter, color, width):
    """Крестик — закрыть окно."""
    _stroke(painter, _line(6.6, 6.6, 17.4, 17.4), color, width)
    _stroke(painter, _line(17.4, 6.6, 6.6, 17.4), color, width)


def _draw_camera(painter, color, width):
    """Фотоаппарат — сменить фотографию."""
    body = QPainterPath()
    body.addRoundedRect(QRectF(2.6, 6.4, 18.8, 13.8), 2.6, 2.6)
    _stroke(painter, body, color, width)

    bump = QPainterPath()
    bump.moveTo(8.2, 6.4)
    bump.lineTo(9.5, 3.9)
    bump.lineTo(14.5, 3.9)
    bump.lineTo(15.8, 6.4)
    _stroke(painter, bump, color, width)

    lens = QPainterPath()
    lens.addEllipse(QPointF(12.0, 13.3), 3.7, 3.7)
    _stroke(painter, lens, color, width)


def _draw_info(painter, color, width):
    """Буква i в круге — информационное сообщение."""
    circle = QPainterPath()
    circle.addEllipse(QPointF(12.0, 12.0), 8.9, 8.9)
    _stroke(painter, circle, color, width)

    dot = QPainterPath()
    dot.addEllipse(QPointF(12.0, 7.6), 1.05, 1.05)
    _fill(painter, dot, color)

    _stroke(painter, _line(12.0, 11.0, 12.0, 16.6), color, width)


def _draw_warning(painter, color, width):
    """Треугольник с восклицательным знаком — предупреждение."""
    tri = QPainterPath()
    tri.moveTo(12.0, 3.4)
    tri.lineTo(21.6, 19.6)
    tri.quadTo(22.2, 20.7, 20.9, 20.7)
    tri.lineTo(3.1, 20.7)
    tri.quadTo(1.8, 20.7, 2.4, 19.6)
    tri.closeSubpath()
    _stroke(painter, tri, color, width)

    _stroke(painter, _line(12.0, 9.2, 12.0, 14.6), color, width)

    dot = QPainterPath()
    dot.addEllipse(QPointF(12.0, 17.6), 1.05, 1.05)
    _fill(painter, dot, color)


def _draw_error(painter, color, width):
    """Крестик в круге — ошибка."""
    circle = QPainterPath()
    circle.addEllipse(QPointF(12.0, 12.0), 8.9, 8.9)
    _stroke(painter, circle, color, width)

    _stroke(painter, _line(8.7, 8.7, 15.3, 15.3), color, width)
    _stroke(painter, _line(15.3, 8.7, 8.7, 15.3), color, width)


def _draw_question(painter, color, width):
    """Знак вопроса в круге — подтверждение действия."""
    circle = QPainterPath()
    circle.addEllipse(QPointF(12.0, 12.0), 8.9, 8.9)
    _stroke(painter, circle, color, width)

    hook = QPainterPath()
    hook.moveTo(9.3, 9.4)
    hook.quadTo(9.3, 6.9, 12.0, 6.9)
    hook.quadTo(14.8, 6.9, 14.8, 9.4)
    hook.quadTo(14.8, 11.4, 12.0, 12.4)
    hook.lineTo(12.0, 14.5)
    _stroke(painter, hook, color, width)

    dot = QPainterPath()
    dot.addEllipse(QPointF(12.0, 17.3), 1.05, 1.05)
    _fill(painter, dot, color)


def _draw_success(painter, color, width):
    """Галочка в круге — успешное действие."""
    circle = QPainterPath()
    circle.addEllipse(QPointF(12.0, 12.0), 8.9, 8.9)
    _stroke(painter, circle, color, width)

    tick = QPainterPath()
    tick.moveTo(7.8, 12.2)
    tick.lineTo(10.8, 15.2)
    tick.lineTo(16.4, 9.0)
    _stroke(painter, tick, color, width)


def _draw_check(painter, color, width):
    """Просто галочка."""
    tick = QPainterPath()
    tick.moveTo(5.0, 12.6)
    tick.lineTo(9.7, 17.3)
    tick.lineTo(19.0, 6.8)
    _stroke(painter, tick, color, width * 1.15)


def _draw_download(painter, color, width):
    """Стрелка вниз на подставке — скачать."""
    _stroke(painter, _line(12.0, 3.6, 12.0, 15.0), color, width)

    head = QPainterPath()
    head.moveTo(7.6, 10.8)
    head.lineTo(12.0, 15.2)
    head.lineTo(16.4, 10.8)
    _stroke(painter, head, color, width)

    tray = QPainterPath()
    tray.moveTo(4.2, 16.4)
    tray.lineTo(4.2, 19.0)
    tray.quadTo(4.2, 20.4, 5.6, 20.4)
    tray.lineTo(18.4, 20.4)
    tray.quadTo(19.8, 20.4, 19.8, 19.0)
    tray.lineTo(19.8, 16.4)
    _stroke(painter, tray, color, width)


def _draw_upload(painter, color, width):
    """Стрелка вверх на подставке — выгрузить."""
    _stroke(painter, _line(12.0, 15.2, 12.0, 3.8), color, width)

    head = QPainterPath()
    head.moveTo(7.6, 8.0)
    head.lineTo(12.0, 3.6)
    head.lineTo(16.4, 8.0)
    _stroke(painter, head, color, width)

    tray = QPainterPath()
    tray.moveTo(4.2, 16.4)
    tray.lineTo(4.2, 19.0)
    tray.quadTo(4.2, 20.4, 5.6, 20.4)
    tray.lineTo(18.4, 20.4)
    tray.quadTo(19.8, 20.4, 19.8, 19.0)
    tray.lineTo(19.8, 16.4)
    _stroke(painter, tray, color, width)


def _draw_eye(painter, color, width):
    """Глаз — просмотр."""
    outline = QPainterPath()
    outline.moveTo(2.2, 12.0)
    outline.cubicTo(5.4, 6.4, 18.6, 6.4, 21.8, 12.0)
    outline.cubicTo(18.6, 17.6, 5.4, 17.6, 2.2, 12.0)
    outline.closeSubpath()
    _stroke(painter, outline, color, width)

    pupil = QPainterPath()
    pupil.addEllipse(QPointF(12.0, 12.0), 2.9, 2.9)
    _stroke(painter, pupil, color, width)


def _draw_play(painter, color, width):
    """Треугольник воспроизведения."""
    tri = QPainterPath()
    tri.moveTo(8.6, 5.8)
    tri.lineTo(18.4, 12.0)
    tri.lineTo(8.6, 18.2)
    tri.closeSubpath()
    _fill(painter, tri, color)


def _draw_image(painter, color, width):
    """Картинка — фотография."""
    _stroke(painter, _rounded(3.0, 4.6, 18.0, 14.8, 2.4), color, width)

    sun = QPainterPath()
    sun.addEllipse(QPointF(8.6, 9.6), 1.7, 1.7)
    _stroke(painter, sun, color, width * 0.9)

    hills = QPainterPath()
    hills.moveTo(3.6, 17.2)
    hills.lineTo(9.4, 12.4)
    hills.lineTo(13.4, 15.8)
    hills.lineTo(16.2, 13.2)
    hills.lineTo(20.4, 17.2)
    _stroke(painter, hills, color, width)


def _draw_video(painter, color, width):
    """Видеокамера."""
    _stroke(painter, _rounded(2.6, 6.6, 12.8, 10.8, 2.2), color, width)

    lens = QPainterPath()
    lens.moveTo(15.4, 12.0)
    lens.lineTo(21.4, 8.4)
    lens.lineTo(21.4, 15.6)
    lens.closeSubpath()
    _stroke(painter, lens, color, width)


def _draw_file(painter, color, width):
    """Лист с загнутым уголком — файл."""
    body = QPainterPath()
    body.moveTo(6.0, 2.9)
    body.lineTo(13.6, 2.9)
    body.lineTo(19.0, 8.3)
    body.lineTo(19.0, 20.0)
    body.quadTo(19.0, 21.1, 17.9, 21.1)
    body.lineTo(6.0, 21.1)
    body.quadTo(4.9, 21.1, 4.9, 20.0)
    body.lineTo(4.9, 4.0)
    body.quadTo(4.9, 2.9, 6.0, 2.9)
    _stroke(painter, body, color, width)

    fold = QPainterPath()
    fold.moveTo(13.4, 3.1)
    fold.lineTo(13.4, 8.5)
    fold.lineTo(18.8, 8.5)
    _stroke(painter, fold, color, width)


def _draw_plus(painter, color, width):
    """Плюс — добавить."""
    _stroke(painter, _line(12.0, 5.0, 12.0, 19.0), color, width * 1.1)
    _stroke(painter, _line(5.0, 12.0, 19.0, 12.0), color, width * 1.1)


def _draw_search(painter, color, width):
    """Лупа."""
    lens = QPainterPath()
    lens.addEllipse(QPointF(10.6, 10.6), 6.3, 6.3)
    _stroke(painter, lens, color, width)
    _stroke(painter, _line(15.2, 15.2, 20.3, 20.3), color, width * 1.1)


def _draw_menu(painter, color, width):
    """Три полосы — главное меню."""
    for y in (7.0, 12.0, 17.0):
        _stroke(painter, _line(4.2, y, 19.8, y), color, width * 1.15)


def _draw_arrow_back(painter, color, width):
    """Стрелка влево — возврат."""
    _stroke(painter, _line(20.0, 12.0, 5.2, 12.0), color, width * 1.1)
    head = QPainterPath()
    head.moveTo(11.0, 5.8)
    head.lineTo(4.6, 12.0)
    head.lineTo(11.0, 18.2)
    _stroke(painter, head, color, width * 1.1)


def _draw_arrow_circle(painter, color, width):
    """Круговая стрелка — сброс/вернуть в работу.

    Остриё — сплошной треугольник: обводка ломаной со скруглением давала
    на кончике утолщение и выглядела смазанной.
    """
    arc = QPainterPath()
    arc.moveTo(19.6, 12.0)
    arc.arcTo(QRectF(3.6, 3.6, 16.8, 16.8), 0.0, 292.0)
    _stroke(painter, arc, color, width)

    head = QPainterPath()
    head.moveTo(19.6, 3.1)
    head.lineTo(23.0, 9.0)
    head.lineTo(16.2, 9.0)
    head.closeSubpath()
    _fill(painter, head, color)


def _draw_copy(painter, color, width):
    """Два листа — копировать."""
    _stroke(painter, _rounded(8.4, 8.4, 11.8, 11.8, 2.2), color, width)
    back = QPainterPath()
    back.moveTo(15.6, 5.6)
    back.lineTo(15.6, 4.0)
    back.quadTo(15.6, 2.8, 14.4, 2.8)
    back.lineTo(4.0, 2.8)
    back.quadTo(2.8, 2.8, 2.8, 4.0)
    back.lineTo(2.8, 14.4)
    back.quadTo(2.8, 15.6, 4.0, 15.6)
    back.lineTo(5.6, 15.6)
    _stroke(painter, back, color, width)


def _draw_folder(painter, color, width):
    """Папка — файлы задачи."""
    body = QPainterPath()
    body.moveTo(2.8, 7.4)
    body.lineTo(2.8, 18.4)
    body.quadTo(2.8, 19.8, 4.2, 19.8)
    body.lineTo(19.8, 19.8)
    body.quadTo(21.2, 19.8, 21.2, 18.4)
    body.lineTo(21.2, 9.4)
    body.quadTo(21.2, 8.0, 19.8, 8.0)
    body.lineTo(12.2, 8.0)
    body.lineTo(10.0, 5.2)
    body.lineTo(4.2, 5.2)
    body.quadTo(2.8, 5.2, 2.8, 6.6)
    body.closeSubpath()
    _stroke(painter, body, color, width)


def _draw_save(painter, color, width):
    """Дискета — сохранить на диск."""
    body = QPainterPath()
    body.moveTo(4.0, 3.4)
    body.lineTo(16.4, 3.4)
    body.lineTo(20.6, 7.6)
    body.lineTo(20.6, 19.4)
    body.quadTo(20.6, 20.6, 19.4, 20.6)
    body.lineTo(4.0, 20.6)
    body.quadTo(2.8, 20.6, 2.8, 19.4)
    body.lineTo(2.8, 4.6)
    body.quadTo(2.8, 3.4, 4.0, 3.4)
    _stroke(painter, body, color, width)

    _stroke(painter, _rounded(7.0, 3.6, 9.0, 5.4, 0.8), color, width * 0.9)
    _stroke(painter, _rounded(6.4, 12.6, 11.2, 8.0, 1.0), color, width * 0.9)


def _draw_mail_open(painter, color, width):
    """Раскрытый конверт — прочитанное уведомление."""
    body = QPainterPath()
    body.moveTo(2.8, 10.6)
    body.lineTo(12.0, 4.2)
    body.lineTo(21.2, 10.6)
    body.lineTo(21.2, 18.6)
    body.quadTo(21.2, 20.0, 19.8, 20.0)
    body.lineTo(4.2, 20.0)
    body.quadTo(2.8, 20.0, 2.8, 18.6)
    body.closeSubpath()
    _stroke(painter, body, color, width)

    flap = QPainterPath()
    flap.moveTo(2.8, 10.6)
    flap.lineTo(12.0, 16.2)
    flap.lineTo(21.2, 10.6)
    _stroke(painter, flap, color, width)


def _draw_mail_filled(painter, color, width):
    """Запечатанный конверт с заливкой — есть непрочитанное."""
    body = QPainterPath()
    body.addRoundedRect(QRectF(2.7, 5.0, 18.6, 14.0), 2.2, 2.2)
    _fill(painter, body, color)

    flap = QPainterPath()
    flap.moveTo(4.4, 7.2)
    flap.lineTo(12.0, 13.0)
    flap.lineTo(19.6, 7.2)
    pen = QPen(QColor("#FFFFFF"), width * 1.05)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.strokePath(flap, pen)


def _draw_bell(painter, color, width):
    """Колокольчик — раздел уведомлений."""
    body = QPainterPath()
    body.moveTo(5.4, 17.0)
    body.lineTo(5.4, 10.8)
    body.quadTo(5.4, 4.6, 12.0, 4.6)
    body.quadTo(18.6, 4.6, 18.6, 10.8)
    body.lineTo(18.6, 17.0)
    _stroke(painter, body, color, width)

    _stroke(painter, _line(3.4, 17.2, 20.6, 17.2), color, width)
    _stroke(painter, _line(12.0, 2.6, 12.0, 4.6), color, width)

    clapper = QPainterPath()
    clapper.moveTo(10.1, 19.2)
    clapper.quadTo(10.6, 21.2, 12.0, 21.2)
    clapper.quadTo(13.4, 21.2, 13.9, 19.2)
    _stroke(painter, clapper, color, width)


def _draw_filter(painter, color, width):
    """Воронка — фильтр."""
    body = QPainterPath()
    body.moveTo(3.2, 5.0)
    body.lineTo(20.8, 5.0)
    body.lineTo(14.0, 12.6)
    body.lineTo(14.0, 19.4)
    body.lineTo(10.0, 17.0)
    body.lineTo(10.0, 12.6)
    body.closeSubpath()
    _stroke(painter, body, color, width)


def _draw_users(painter, color, width):
    """Две фигуры — сотрудники."""
    head = QPainterPath()
    head.addEllipse(QPointF(9.7, 8.6), 3.3, 3.3)
    _stroke(painter, head, color, width)

    shoulders = QPainterPath()
    shoulders.moveTo(3.6, 19.6)
    shoulders.cubicTo(3.6, 15.6, 6.4, 13.6, 9.7, 13.6)
    shoulders.cubicTo(13.0, 13.6, 15.8, 15.6, 15.8, 19.6)
    _stroke(painter, shoulders, color, width)

    second = QPainterPath()
    second.moveTo(16.2, 5.9)
    second.quadTo(19.6, 6.6, 19.6, 9.4)
    second.quadTo(19.6, 12.0, 16.6, 12.8)
    _stroke(painter, second, color, width)

    tail = QPainterPath()
    tail.moveTo(17.4, 14.2)
    tail.quadTo(21.0, 15.4, 21.0, 19.6)
    _stroke(painter, tail, color, width)


def _draw_settings(painter, color, width):
    """Шестерёнка — настройки."""
    import math
    gear = QPainterPath()
    outer, inner = 9.3, 6.9
    for i in range(24):
        angle = math.radians(i * 15.0)
        radius = outer if (i // 2) % 2 == 0 else inner
        x = 12.0 + radius * math.cos(angle)
        y = 12.0 + radius * math.sin(angle)
        if i == 0:
            gear.moveTo(x, y)
        else:
            gear.lineTo(x, y)
    gear.closeSubpath()
    _stroke(painter, gear, color, width * 0.85)

    hole = QPainterPath()
    hole.addEllipse(QPointF(12.0, 12.0), 3.2, 3.2)
    _stroke(painter, hole, color, width)


def _draw_archive(painter, color, width):
    """Коробка архива."""
    _stroke(painter, _rounded(2.9, 3.6, 18.2, 4.6, 1.2), color, width)
    body = QPainterPath()
    body.moveTo(4.6, 8.6)
    body.lineTo(4.6, 19.0)
    body.quadTo(4.6, 20.4, 6.0, 20.4)
    body.lineTo(18.0, 20.4)
    body.quadTo(19.4, 20.4, 19.4, 19.0)
    body.lineTo(19.4, 8.6)
    _stroke(painter, body, color, width)
    _stroke(painter, _line(9.6, 12.6, 14.4, 12.6), color, width)


def _draw_calendar(painter, color, width):
    """Календарь — дедлайн."""
    _stroke(painter, _rounded(3.2, 5.0, 17.6, 15.4, 2.2), color, width)
    _stroke(painter, _line(3.2, 9.6, 20.8, 9.6), color, width)
    _stroke(painter, _line(8.2, 2.9, 8.2, 6.4), color, width)
    _stroke(painter, _line(15.8, 2.9, 15.8, 6.4), color, width)

    dots = QPainterPath()
    for x, y in ((8.0, 13.2), (12.0, 13.2), (16.0, 13.2), (8.0, 16.8), (12.0, 16.8)):
        dots.addEllipse(QPointF(x, y), 0.85, 0.85)
    _fill(painter, dots, color)


def _draw_clock(painter, color, width):
    """Часы — срок."""
    circle = QPainterPath()
    circle.addEllipse(QPointF(12.0, 12.0), 8.9, 8.9)
    _stroke(painter, circle, color, width)
    _stroke(painter, _line(12.0, 6.6, 12.0, 12.2), color, width)
    _stroke(painter, _line(12.0, 12.2, 16.0, 14.5), color, width)


def _draw_table(painter, color, width):
    """Таблица — файлы CSV."""
    _stroke(painter, _rounded(3.0, 4.4, 18.0, 15.2, 2.2), color, width)
    _stroke(painter, _line(3.0, 9.4, 21.0, 9.4), color, width)
    _stroke(painter, _line(3.0, 14.6, 21.0, 14.6), color, width)
    _stroke(painter, _line(9.6, 9.4, 9.6, 19.6), color, width)


def _draw_flag(painter, color, width):
    """Флажок — приоритетная задача."""
    _stroke(painter, _line(5.6, 3.0, 5.6, 21.0), color, width)
    cloth = QPainterPath()
    cloth.moveTo(5.6, 4.2)
    cloth.lineTo(18.8, 4.2)
    cloth.lineTo(15.6, 8.6)
    cloth.lineTo(18.8, 13.0)
    cloth.lineTo(5.6, 13.0)
    cloth.closeSubpath()
    _fill(painter, cloth, color)


def _draw_list(painter, color, width):
    """Список — подробности задачи."""
    dots = QPainterPath()
    for y in (6.6, 12.0, 17.4):
        dots.addEllipse(QPointF(4.9, y), 1.15, 1.15)
    _fill(painter, dots, color)
    for y in (6.6, 12.0, 17.4):
        _stroke(painter, _line(9.0, y, 20.0, y), color, width)


def _draw_chevron_down(painter, color, width):
    """Шеврон вниз."""
    path = QPainterPath()
    path.moveTo(6.0, 9.4)
    path.lineTo(12.0, 15.4)
    path.lineTo(18.0, 9.4)
    _stroke(painter, path, color, width * 1.1)


_ICONS = {
    "menu": _draw_menu,
    "arrow_back": _draw_arrow_back,
    "arrow_circle": _draw_arrow_circle,
    "copy": _draw_copy,
    "folder": _draw_folder,
    "save": _draw_save,
    "mail_open": _draw_mail_open,
    "mail_filled": _draw_mail_filled,
    "bell": _draw_bell,
    "filter": _draw_filter,
    "users": _draw_users,
    "settings": _draw_settings,
    "archive": _draw_archive,
    "calendar": _draw_calendar,
    "clock": _draw_clock,
    "table": _draw_table,
    "flag": _draw_flag,
    "list": _draw_list,
    "chevron_down": _draw_chevron_down,
    "info": _draw_info,
    "warning": _draw_warning,
    "error": _draw_error,
    "question": _draw_question,
    "success": _draw_success,
    "check": _draw_check,
    "download": _draw_download,
    "upload": _draw_upload,
    "eye": _draw_eye,
    "play": _draw_play,
    "image": _draw_image,
    "video": _draw_video,
    "file": _draw_file,
    "plus": _draw_plus,
    "search": _draw_search,
    "edit": _draw_edit,
    "trash": _draw_trash,
    "logout": _draw_logout,
    "phone": _draw_phone,
    "mail": _draw_mail,
    "briefcase": _draw_briefcase,
    "building": _draw_building,
    "user": _draw_user,
    "note": _draw_note,
    "id_card": _draw_id_card,
    "close": _draw_close,
    "camera": _draw_camera,
}


# ══════════════════════════════════════════════════════════════════════════════
# Публичный API
# ══════════════════════════════════════════════════════════════════════════════

def available_icons():
    """Список доступных имён иконок."""
    return sorted(_ICONS.keys())


def icon_pixmap(name, size, color="#FFFFFF", stroke=1.9):
    """Отрисовывает иконку `name` в QPixmap размером size x size.

    :param name:   имя иконки (см. available_icons())
    :param size:   сторона квадрата в пикселях (уже с учётом scaler.scale)
    :param color:  цвет контура
    :param stroke: толщина контура в координатах 24 x 24
    """
    size = max(int(size), 1)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    drawer = _ICONS.get(name)
    if drawer is None:
        return pixmap

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.scale(size / VIEWBOX, size / VIEWBOX)
        drawer(painter, color, float(stroke))
    finally:
        painter.end()

    return pixmap


def make_icon(name, size, color="#FFFFFF", hover_color=None, disabled_color=None, stroke=1.9):
    """Собирает QIcon с отдельными состояниями Normal / Active / Disabled."""
    icon = QIcon()
    normal = icon_pixmap(name, size, color, stroke)
    icon.addPixmap(normal, QIcon.Normal, QIcon.Off)
    icon.addPixmap(normal, QIcon.Selected, QIcon.Off)

    active = icon_pixmap(name, size, hover_color, stroke) if hover_color else normal
    icon.addPixmap(active, QIcon.Active, QIcon.Off)

    if disabled_color:
        icon.addPixmap(icon_pixmap(name, size, disabled_color, stroke), QIcon.Disabled, QIcon.Off)

    return icon
