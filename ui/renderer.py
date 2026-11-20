# -*- coding: utf-8 -*-
"""同心双环图标、鼠标电量胶囊图标与任务栏嵌入式信息条绘制模块。"""

from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from config import (
    ICON_SIZE,
    LOW_COLOR_THRESHOLD,
    MID_COLOR_THRESHOLD,
    SUPERSAMPLE_FACTOR,
)
from core.models import WidgetStyle
from .theme import foreground_color


COLOR_LOW: Tuple[int, int, int, int] = (255, 90, 90, 255)             # <= 15% 红色
COLOR_MID: Tuple[int, int, int, int] = (255, 196, 66, 255)            # <= 30% 黄色
COLOR_CHARGING: Tuple[int, int, int, int] = (74, 222, 128, 255)       # 深色任务栏充电绿色
COLOR_CHARGING_LIGHT: Tuple[int, int, int, int] = (22, 163, 74, 255)  # 浅色任务栏充电绿色
COLOR_DOT_ONLINE: Tuple[int, int, int, int] = (74, 222, 128, 255)
COLOR_DOT_CHARGING: Tuple[int, int, int, int] = COLOR_CHARGING
COLOR_DOT_SLEEP: Tuple[int, int, int, int] = (148, 163, 184, 210)
COLOR_DOT_OFFLINE: Tuple[int, int, int, int] = (239, 68, 68, 220)

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
)

_DIGIT_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\msyhbd.ttc",
    *_FONT_CANDIDATES,
)


def _resolve_charging_green(light: bool) -> Tuple[int, int, int, int]:
    """根据深浅任务栏主题返回高对比度的充电指示绿色。"""
    return COLOR_CHARGING_LIGHT if light else COLOR_CHARGING


def resolve_arc_color(
    percent: Optional[int],
    fg: Tuple[int, int, int, int],
    charging: bool = False,
) -> Tuple[int, int, int, int]:
    """根据电量百分比、充电状态与当前前景色计算填充颜色。"""
    if charging:
        return _resolve_charging_green(fg == (32, 32, 32, 255))
    if percent is None:
        return fg
    if percent <= LOW_COLOR_THRESHOLD:
        return COLOR_LOW
    if percent <= MID_COLOR_THRESHOLD:
        return COLOR_MID
    return fg


def _load_font_from_candidates(candidates: Tuple[str, ...], font_px: int) -> ImageFont.ImageFont:
    """按候选列表顺序加载系统 TrueType 字体并回退到默认位图字体。"""
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, font_px)
            except Exception:
                continue
    return ImageFont.load_default()


@lru_cache(maxsize=16)
def load_ui_font(font_px: int) -> ImageFont.ImageFont:
    """加载系统微软雅黑或回退字体并按字号缓存。"""
    return _load_font_from_candidates(_FONT_CANDIDATES, font_px)


@lru_cache(maxsize=24)
def load_digit_font(font_px: int) -> ImageFont.ImageFont:
    """加载托盘小尺寸高清晰粗体数字字体并缓存。"""
    return _load_font_from_candidates(_DIGIT_FONT_CANDIDATES, font_px)


def _resolve_status_dot_color(
    connected: bool,
    online: bool,
    charging: bool,
    light: bool = False,
) -> Tuple[int, int, int, int]:
    """根据设备连接、休眠与充电状态返回指示圆点颜色。"""
    if not connected:
        return COLOR_DOT_OFFLINE
    if charging:
        return _resolve_charging_green(light)
    if online:
        return COLOR_DOT_ONLINE
    return COLOR_DOT_SLEEP


@lru_cache(maxsize=4)
def _alpha_gamma_lut(gamma: float) -> Tuple[int, ...]:
    """生成用于补偿 FreeType 小字号抗锯齿偏细偏暗的 Alpha 提亮查找表。"""
    return tuple(min(255, int(round(((i / 255.0) ** gamma) * 255))) for i in range(256))


def _make_widget_canvas(
    width: int,
    height: int,
    light: bool,
    hovered: bool,
    opaque_bg: Optional[Tuple[int, int, int]],
) -> Image.Image:
    """创建带防点击穿透底板 (Alpha=1) 或悬停高亮的任务栏位图画布。"""
    if opaque_bg is not None:
        bg_rgba = (opaque_bg[0], opaque_bg[1], opaque_bg[2], 255)
    elif hovered:
        bg_rgba = (0, 0, 0, 18) if light else (255, 255, 255, 18)
    else:
        bg_rgba = (255, 255, 255, 1) if light else (0, 0, 0, 1)
    return Image.new("RGBA", (width, height), bg_rgba)


def _composite_gamma_text(
    canvas: Image.Image,
    items: Iterable[Tuple[Tuple[int, int], str]],
    font: ImageFont.ImageFont,
    fg: Tuple[int, int, int, int],
    light: bool,
) -> None:
    """在独立图层绘制文字并施加 Gamma 补偿后合成到目标画布。"""
    text_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    for pos, text in items:
        text_draw.text(pos, text, font=font, fill=fg)

    r, g, b, a = text_layer.split()
    gamma = 0.85 if light else 0.72
    text_layer = Image.merge("RGBA", (r, g, b, a.point(_alpha_gamma_lut(gamma))))
    canvas.alpha_composite(text_layer)


def render_tray_icon(
    mouse_percent: Optional[int],
    keyboard_percent: Optional[int],
    light_theme: Optional[bool] = None,
    size: int = ICON_SIZE,
    supersample: int = SUPERSAMPLE_FACTOR,
    mouse_charging: bool = False,
    mouse_connected: bool = True,
    keyboard_connected: bool = True,
    keyboard_charging: bool = False,
) -> Image.Image:
    """绘制同心双环细线电量图标，并按基准画布等比缩放至目标尺寸。"""
    ss = max(1, int(supersample))
    base_size = ICON_SIZE
    canvas_size = base_size * ss
    big = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)

    fg = foreground_color(light_theme)
    track = (fg[0], fg[1], fg[2], 54)
    mouse_val = mouse_percent if mouse_connected else None
    kb_val = keyboard_percent if keyboard_connected else None

    def draw_ring(
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        width: float,
        val: Optional[int],
        charging: bool = False,
    ) -> None:
        box = [
            int(round(x0 * ss)),
            int(round(y0 * ss)),
            int(round(x1 * ss)),
            int(round(y1 * ss)),
        ]
        stroke_w = max(1, int(round(width * ss)))
        draw.ellipse(box, outline=track, width=stroke_w)
        if val is not None and val > 0:
            clamped = min(100, max(0, int(val)))
            color = resolve_arc_color(clamped, fg, charging=charging)
            if clamped >= 100:
                draw.ellipse(box, outline=color, width=stroke_w)
            else:
                draw.arc(
                    box,
                    start=-90,
                    end=-90 + 360 * clamped / 100.0,
                    fill=color,
                    width=stroke_w,
                )
        elif charging:
            color = resolve_arc_color(None, fg, charging=True)
            draw.ellipse(box, outline=color, width=stroke_w)

    outer_w = 5.8 if size <= 24 else 5.2
    inner_w = 4.0 if size <= 24 else 3.8
    draw_ring(
        0.5,
        0.5,
        base_size - 1.5,
        base_size - 1.5,
        outer_w,
        mouse_val,
        charging=mouse_charging and mouse_connected,
    )  # 外圈：鼠标
    draw_ring(
        20.0,
        20.0,
        base_size - 21.0,
        base_size - 21.0,
        inner_w,
        kb_val,
        charging=keyboard_charging and keyboard_connected,
    )  # 内圈：键盘

    if canvas_size == size:
        return big
    return big.resize((size, size), Image.LANCZOS)


def render_numeric_tray_icon(
    mouse_percent: Optional[int],
    connected: bool = True,
    online: bool = True,
    charging: bool = False,
    light_theme: Optional[bool] = None,
    size: int = 16,
    supersample: int = SUPERSAMPLE_FACTOR,
) -> Image.Image:
    """绘制系统托盘满幅数字电量图标（大号电量数字 + 底部进度条 + 右下角连接状态点）。"""
    sq = max(16, int(size))
    ss = max(1, int(supersample))
    fg = foreground_color(light_theme)
    light = fg == (32, 32, 32, 255)
    has_val = connected and mouse_percent is not None
    clamped = min(100, max(0, int(mouse_percent))) if has_val else None

    accent = resolve_arc_color(clamped, fg, charging=charging) if (clamped is not None or charging) else fg
    text = str(clamped) if clamped is not None else "--"

    scale = sq / 16.0
    bar_h = max(2, round(2.0 * scale))
    dot_d = max(3, round(3.2 * scale))
    bottom_reserve = max(bar_h, dot_d) + 1
    top_area_h = max(8, sq - bottom_reserve)

    # 自适应选择在 16px/20px/24px 内完全容纳且无裁切的最大字号
    chosen_font: Optional[ImageFont.ImageFont] = None
    chosen_bbox = (0, 0, sq, top_area_h)
    start_px = max(8, round(top_area_h * (0.95 if len(text) >= 3 else 1.15)))
    for font_px in range(start_px, 6, -1):
        candidate = load_digit_font(font_px)
        bb = candidate.getbbox(text)
        if (bb[2] - bb[0]) <= sq and (bb[3] - bb[1]) <= top_area_h:
            chosen_font = candidate
            chosen_bbox = bb
            break
    if chosen_font is None:
        chosen_font = load_digit_font(7)
        chosen_bbox = chosen_font.getbbox(text)

    tw = chosen_bbox[2] - chosen_bbox[0]
    th = chosen_bbox[3] - chosen_bbox[1]
    tx = (sq - tw) // 2 - chosen_bbox[0]
    ty = (top_area_h - th) // 2 - chosen_bbox[1]
    text_color = accent if (has_val or charging) else (fg[0], fg[1], fg[2], 170)

    canvas = Image.new("RGBA", (sq, sq), (0, 0, 0, 0))
    _composite_gamma_text(canvas, [((tx, ty), text)], chosen_font, text_color, light)

    draw = ImageDraw.Draw(canvas)
    dx0 = sq - 1 - dot_d
    bar_x0 = 1
    bar_gap = max(2, round(1.5 * scale))
    bar_x1 = max(bar_x0 + 2, dx0 - bar_gap)
    bar_cy = sq - 1 - dot_d // 2
    by0 = bar_cy - bar_h // 2
    by1 = by0 + bar_h - 1
    track_alpha = 80 if light else 75
    draw.rectangle([bar_x0, by0, bar_x1, by1], fill=(fg[0], fg[1], fg[2], track_alpha))

    if clamped is not None and clamped > 0:
        fill_w = max(1, round((bar_x1 - bar_x0 + 1) * clamped / 100.0))
        draw.rectangle([bar_x0, by0, bar_x0 + fill_w - 1, by1], fill=accent)

    # 通过超采样绘制右下角连接指示圆点，防止小尺寸下退化成方点
    dot_color = _resolve_status_dot_color(
        connected=connected, online=online, charging=charging, light=light
    )
    dot_canvas = Image.new("RGBA", (sq * ss, sq * ss), (0, 0, 0, 0))
    dc_x = (sq - 0.5 - dot_d / 2.0) * ss
    dc_y = (sq - 0.5 - dot_d / 2.0) * ss
    dr = (dot_d / 2.0) * ss
    ImageDraw.Draw(dot_canvas).ellipse([dc_x - dr, dc_y - dr, dc_x + dr, dc_y + dr], fill=dot_color)
    canvas.alpha_composite(dot_canvas.resize((sq, sq), Image.LANCZOS))
    return canvas


def render_mouse_capsule_icon(
    mouse_percent: Optional[int],
    connected: bool = True,
    online: bool = True,
    charging: bool = False,
    light_theme: Optional[bool] = None,
    height: int = 28,
    square_size: Optional[int] = None,
    supersample: int = SUPERSAMPLE_FACTOR,
) -> Image.Image:
    """绘制竖向圆角鼠标电量填充图标 + 底部连接指示圆点。"""
    ss = max(1, int(supersample))
    w_base, h_base = 24, 28
    cw, ch = w_base * ss, h_base * ss

    fg = foreground_color(light_theme)
    light = fg == (32, 32, 32, 255)
    if not connected:
        outline_color = (fg[0], fg[1], fg[2], 125)
    elif charging:
        outline_color = _resolve_charging_green(light)
    else:
        outline_color = fg
    fill_color = resolve_arc_color(mouse_percent, fg, charging=charging) if mouse_percent is not None else None

    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    mx0, my0, mx1, my1 = 5 * ss, 1 * ss, 19 * ss, 21 * ss
    radius = (mx1 - mx0) // 2
    stroke = max(1, round(1.35 * ss))

    # 1. 按电量自底向上填充内部区域（通过胶囊蒙版严格裁剪边界）
    if fill_color is not None and mouse_percent is not None and mouse_percent > 0:
        clamped = min(100, max(0, int(mouse_percent)))
        pad = round(2.0 * ss)
        ix0, iy0, ix1, iy1 = mx0 + pad, my0 + pad, mx1 - pad, my1 - pad
        inner_r = max(1, (ix1 - ix0) // 2)
        mask = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(mask).rounded_rectangle([ix0, iy0, ix1, iy1], radius=inner_r, fill=255)
        fill_layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        fill_h = round((iy1 - iy0) * clamped / 100.0)
        ImageDraw.Draw(fill_layer).rectangle([ix0, iy1 - fill_h, ix1, iy1], fill=fill_color)
        img.paste(fill_layer, (0, 0), mask)

    draw = ImageDraw.Draw(img)
    # 2. 外轮廓与顶部滚轮槽位
    draw.rounded_rectangle([mx0, my0, mx1, my1], radius=radius, outline=outline_color, width=stroke)
    wx0, wy0, wx1, wy1 = 11 * ss, 4 * ss, 13 * ss, 8 * ss
    high_fill_covers_wheel = (
        mouse_percent is not None
        and mouse_percent >= 75
        and (charging or (mouse_percent > LOW_COLOR_THRESHOLD and mouse_percent > MID_COLOR_THRESHOLD))
    )
    if high_fill_covers_wheel:
        wheel_color = (240, 240, 240, 235) if light else (28, 28, 28, 235)
    else:
        wheel_color = outline_color
    draw.rounded_rectangle([wx0, wy0, wx1, wy1], radius=ss, fill=wheel_color)

    # 3. 底部连接指示圆点
    dot_color = _resolve_status_dot_color(
        connected=connected, online=online, charging=charging, light=light
    )
    dx, dy, dr = 12 * ss, 25 * ss, round(1.85 * ss)
    draw.ellipse([dx - dr, dy - dr, dx + dr, dy + dr], fill=dot_color)

    if square_size is not None:
        sq = max(16, int(square_size))
        scaled_w = max(1, round(sq * w_base / h_base))
        resized = img.resize((scaled_w, sq), Image.LANCZOS)
        square = Image.new("RGBA", (sq, sq), (0, 0, 0, 0))
        square.alpha_composite(resized, ((sq - scaled_w) // 2, 0))
        return square

    target_h = max(16, int(height))
    target_w = max(12, round(target_h * w_base / h_base))
    return img.resize((target_w, target_h), Image.LANCZOS)


def _compute_dual_ring_metrics(dpi: int, bar_height: int) -> Tuple[int, int, int, int, int, ImageFont.ImageFont]:
    """计算双环双行模式的固定宽高与排版指标。"""
    scale = max(0.75, dpi / 96.0)
    bar_h = max(24, int(bar_height))
    font_px = max(10, min(round(12 * scale), max(10, round(bar_h * 0.34))))
    font = load_ui_font(font_px)

    pad_x = max(4, round(6 * scale))
    gap = max(4, round(6 * scale))
    icon_size = max(16, min(round(24 * scale), bar_h - max(6, round(10 * scale))))

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    max_text_w = max(
        probe.textlength("鼠标: 100%", font=font),
        probe.textlength("键盘: 100%", font=font),
    )
    bar_w = int(round(pad_x + icon_size + gap + max_text_w + pad_x))
    return bar_w, bar_h, pad_x, icon_size, gap, font


def _compute_mouse_capsule_metrics(dpi: int, bar_height: int) -> Tuple[int, int, int, int, int, ImageFont.ImageFont]:
    """计算单鼠标胶囊样式的固定宽高与排版指标。"""
    scale = max(0.75, dpi / 96.0)
    bar_h = max(24, int(bar_height))
    font_px = max(11, min(round(14 * scale), max(11, round(bar_h * 0.42))))
    font = load_ui_font(font_px)

    pad_x = max(5, round(7 * scale))
    gap = max(3, round(4 * scale))
    icon_h = max(20, min(round(28 * scale), bar_h - max(4, round(8 * scale))))
    icon_w = max(16, round(icon_h * 24 / 28))

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    max_text_w = probe.textlength("100%", font=font)
    bar_w = int(round(pad_x + icon_w + gap + max_text_w + pad_x))
    return bar_w, bar_h, pad_x, icon_h, gap, font


def measure_taskbar_widget(dpi: int = 96, bar_height: int = 40) -> Tuple[int, int]:
    """返回默认双环双行任务栏嵌入条的固定推荐宽高 (width, height)。"""
    bar_w, bar_h, *_ = _compute_dual_ring_metrics(dpi, bar_height)
    return bar_w, bar_h


def measure_widget_by_style(
    style: WidgetStyle = WidgetStyle.DUAL_RING,
    dpi: int = 96,
    bar_height: int = 40,
) -> Tuple[int, int]:
    """按指定样式返回任务栏嵌入条推荐宽高。"""
    if style == WidgetStyle.MOUSE_CAPSULE:
        bar_w, bar_h, *_ = _compute_mouse_capsule_metrics(dpi, bar_height)
        return bar_w, bar_h
    return measure_taskbar_widget(dpi=dpi, bar_height=bar_height)


def format_device_bar_text(label: str, percent: Optional[int]) -> str:
    """格式化任务栏右侧单行文本。"""
    val = f"{percent}%" if percent is not None else "--"
    return f"{label}: {val}"


def render_taskbar_widget(
    mouse_percent: Optional[int],
    keyboard_percent: Optional[int],
    light_theme: Optional[bool] = None,
    dpi: int = 96,
    bar_height: int = 40,
    hovered: bool = False,
    opaque_bg: Optional[Tuple[int, int, int]] = None,
    mouse_charging: bool = False,
    mouse_connected: bool = True,
    keyboard_connected: bool = True,
    keyboard_charging: bool = False,
) -> Image.Image:
    """绘制「左边放大双环图标 + 右边两行文字」的任务栏嵌入式位图。"""
    bar_w, bar_h, pad_x, icon_size, gap, font = _compute_dual_ring_metrics(dpi, bar_height)
    fg = foreground_color(light_theme)
    light = fg == (32, 32, 32, 255)
    mouse_val = mouse_percent if mouse_connected else None
    kb_val = keyboard_percent if keyboard_connected else None

    canvas = _make_widget_canvas(bar_w, bar_h, light, hovered, opaque_bg)
    icon_img = render_tray_icon(
        mouse_val,
        kb_val,
        light_theme=light,
        size=icon_size,
        supersample=SUPERSAMPLE_FACTOR,
        mouse_charging=mouse_charging and mouse_connected,
        mouse_connected=mouse_connected,
        keyboard_connected=keyboard_connected,
        keyboard_charging=keyboard_charging and keyboard_connected,
    )
    canvas.alpha_composite(icon_img, (pad_x, (bar_h - icon_size) // 2))

    line1 = format_device_bar_text("鼠标", mouse_val)
    line2 = format_device_bar_text("键盘", kb_val)
    b1 = font.getbbox("鼠标: 100%")
    b2 = font.getbbox("键盘: 100%")
    lh1 = max(1, b1[3] - b1[1])
    lh2 = max(1, b2[3] - b2[1])
    line_gap = max(2, round(4 * max(0.75, dpi / 96.0)))
    top_y = (bar_h - (lh1 + line_gap + lh2)) // 2

    text_x = pad_x + icon_size + gap
    _composite_gamma_text(
        canvas,
        [
            ((text_x, top_y - b1[1]), line1),
            ((text_x, top_y + lh1 + line_gap - b2[1]), line2),
        ],
        font,
        fg,
        light,
    )
    return canvas


def render_mouse_capsule_widget(
    mouse_percent: Optional[int],
    connected: bool = True,
    online: bool = True,
    charging: bool = False,
    light_theme: Optional[bool] = None,
    dpi: int = 96,
    bar_height: int = 40,
    hovered: bool = False,
    opaque_bg: Optional[Tuple[int, int, int]] = None,
) -> Image.Image:
    """绘制「左边鼠标电量胶囊 + 右边单行百分比」的任务栏嵌入式位图（仅显示鼠标电量）。"""
    bar_w, bar_h, pad_x, icon_h, gap, font = _compute_mouse_capsule_metrics(dpi, bar_height)
    fg = foreground_color(light_theme)
    light = fg == (32, 32, 32, 255)

    canvas = _make_widget_canvas(bar_w, bar_h, light, hovered, opaque_bg)
    icon_img = render_mouse_capsule_icon(
        mouse_percent=mouse_percent,
        connected=connected,
        online=online,
        charging=charging,
        light_theme=light,
        height=icon_h,
    )
    canvas.alpha_composite(icon_img, (pad_x, (bar_h - icon_img.height) // 2))

    text = f"{mouse_percent}%" if mouse_percent is not None else "--"
    bbox = font.getbbox("100%")
    text_h = max(1, bbox[3] - bbox[1])
    text_y = (bar_h - text_h) // 2 - bbox[1]
    text_x = pad_x + icon_img.width + gap
    _composite_gamma_text(canvas, [((text_x, text_y), text)], font, fg, light)
    return canvas


def render_widget_by_style(
    style: WidgetStyle,
    mouse_percent: Optional[int],
    keyboard_percent: Optional[int],
    mouse_connected: bool = True,
    mouse_online: bool = True,
    mouse_charging: bool = False,
    keyboard_connected: bool = True,
    keyboard_charging: bool = False,
    light_theme: Optional[bool] = None,
    dpi: int = 96,
    bar_height: int = 40,
    hovered: bool = False,
    opaque_bg: Optional[Tuple[int, int, int]] = None,
) -> Image.Image:
    """按指定样式分发绘制任务栏嵌入式位图。"""
    if style == WidgetStyle.MOUSE_CAPSULE:
        return render_mouse_capsule_widget(
            mouse_percent=mouse_percent,
            connected=mouse_connected,
            online=mouse_online,
            charging=mouse_charging,
            light_theme=light_theme,
            dpi=dpi,
            bar_height=bar_height,
            hovered=hovered,
            opaque_bg=opaque_bg,
        )
    return render_taskbar_widget(
        mouse_percent=mouse_percent,
        keyboard_percent=keyboard_percent,
        light_theme=light_theme,
        dpi=dpi,
        bar_height=bar_height,
        hovered=hovered,
        opaque_bg=opaque_bg,
        mouse_charging=mouse_charging,
        mouse_connected=mouse_connected,
        keyboard_connected=keyboard_connected,
        keyboard_charging=keyboard_charging,
    )


def render_tray_icon_by_style(
    style: WidgetStyle,
    mouse_percent: Optional[int],
    keyboard_percent: Optional[int],
    mouse_connected: bool = True,
    mouse_online: bool = True,
    mouse_charging: bool = False,
    keyboard_connected: bool = True,
    keyboard_charging: bool = False,
    light_theme: Optional[bool] = None,
    size: int = ICON_SIZE,
    compact_numeric: bool = False,
) -> Image.Image:
    """按当前样式生成正方形系统托盘图标或气泡通知图标。"""
    if style == WidgetStyle.MOUSE_CAPSULE:
        if compact_numeric:
            return render_numeric_tray_icon(
                mouse_percent=mouse_percent,
                connected=mouse_connected,
                online=mouse_online,
                charging=mouse_charging,
                light_theme=light_theme,
                size=size,
            )
        return render_mouse_capsule_icon(
            mouse_percent=mouse_percent,
            connected=mouse_connected,
            online=mouse_online,
            charging=mouse_charging,
            light_theme=light_theme,
            square_size=size,
        )
    return render_tray_icon(
        mouse_percent=mouse_percent,
        keyboard_percent=keyboard_percent,
        light_theme=light_theme,
        size=size,
        mouse_charging=mouse_charging,
        mouse_connected=mouse_connected,
        keyboard_connected=keyboard_connected,
        keyboard_charging=keyboard_charging,
    )


def image_to_premultiplied_bgra(img: Image.Image) -> bytes:
    """将 RGBA Image 转换为 Win32 UpdateLayeredWindow 所需的预乘 BGRA 字节流。"""
    rgba = img if img.mode == "RGBA" else img.convert("RGBA")
    pre = rgba.convert("RGBa")
    r, g, b, a = pre.split()
    return Image.merge("RGBA", (b, g, r, a)).tobytes()
