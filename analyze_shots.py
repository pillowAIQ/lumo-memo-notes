# -*- coding: utf-8 -*-
"""对 audit_shots/*.png 做像素级客观分析。"""
import os
import sys
from collections import Counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import memo_app  # noqa: F401

from PySide6.QtGui import QImage, QColor  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_shots")


def load(name):
    img = QImage(os.path.join(OUT, name))
    if img.isNull():
        print("!! cannot load", name)
        return None
    return img.convertToFormat(QImage.Format.Format_ARGB32)


def px(img, x, y):
    return QColor(img.pixel(x, y))


def lum(c):
    return 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()


def stats(img, label=""):
    w, h = img.width(), img.height()
    lums = []
    colors = Counter()
    step = max(1, (w * h) // 20000)
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            c = px(img, x, y)
            lums.append(lum(c))
            colors[(c.red(), c.green(), c.blue())] += 1
    lums.sort()
    n = len(lums)
    mean = sum(lums) / n
    p5, p50, p95 = lums[int(n * .05)], lums[int(n * .5)], lums[int(n * .95)]
    var = sum((v - mean) ** 2 for v in lums) / n
    print(f"[{label}] {w}x{h} mean={mean:.1f} sd={var**0.5:.1f} "
          f"p5={p5:.0f} p50={p50:.0f} p95={p95:.0f}")
    top = colors.most_common(10)
    for (r, g, b), cnt in top:
        print(f"    #{r:02x}{g:02x}{b:02x}  {cnt*16}px")


def region_stats(img, x0, y0, x1, y1, label=""):
    vals = []
    cols = Counter()
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = px(img, x, y)
            vals.append(lum(c))
            cols[(c.red(), c.green(), c.blue())] += 1
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    top = ", ".join(f"#{r:02x}{g:02x}{b:02x}:{n}" for (r, g, b), n in cols.most_common(4))
    print(f"  region {label} ({x0},{y0})-({x1},{y1}): mean={mean:.1f} sd={var**0.5:.1f} top={top}")


def find_color_runs(img, y, x0, x1, target, tol=12):
    """在行 y 上找与 target 颜色接近的连续段。"""
    tr, tg, tb = target
    runs = []
    start = None
    for x in range(x0, x1):
        c = px(img, x, y)
        ok = abs(c.red() - tr) <= tol and abs(c.green() - tg) <= tol and abs(c.blue() - tb) <= tol
        if ok and start is None:
            start = x
        elif not ok and start is not None:
            runs.append((start, x - 1))
            start = None
    if start is not None:
        runs.append((start, x1 - 1))
    return [r for r in runs if r[1] - r[0] > 3]


def main():
    # ---------- 主窗口 ----------
    img = load("01_main_full.png")
    stats(img, "main_full")

    # 窗口四角：检测圆角（角点应接近桌面/透明或非背景色）
    for name, (x, y) in {"TL": (0, 0), "TR": (899, 0), "BL": (0, 759), "BR": (899, 759)}.items():
        print(f"  corner {name}: {px(img, x, y).name()} rgb=({px(img,x,y).red()},{px(img,x,y).green()},{px(img,x,y).blue()})")

    # 标题栏区域
    region_stats(img, 0, 0, 900, 40, "titlebar")
    # 标题栏底边 y=40 附近
    for y in (38, 39, 40, 41):
        c = px(img, 450, y)
        print(f"  titlebar/border y={y}: #{c.red():02x}{c.green():02x}{c.blue():02x}")

    # 侧边栏面板范围检测：x=12..182, 找 bg #efede6
    # 内容区背景
    region_stats(img, 200, 60, 880, 100, "content-top")
    region_stats(img, 200, 400, 880, 440, "content-mid")

    # 卡片检测：在 y=150..740 扫描近白 #ffffff 的行段
    print("\n-- card rows (white #fff runs, y scan) --")
    card_rows = []
    for y in range(60, 740, 2):
        runs = find_color_runs(img, y, 195, 890, (255, 255, 255), tol=6)
        long_runs = [r for r in runs if r[1] - r[0] > 400]
        if long_runs:
            card_rows.append((y, long_runs[0]))
    # 合并连续 y
    merged = []
    for y, r in card_rows:
        if merged and y - merged[-1][1] <= 2 and abs(r[0] - merged[-1][2][0]) < 10:
            merged[-1] = (merged[-1][0], y, r)
        else:
            merged.append((y, y, r))
    for y0, y1, r in merged:
        print(f"  card y {y0}-{y1} (h={y1-y0+1}) x {r[0]}-{r[1]} w={r[1]-r[0]+1}")

    # 卡片左边距对齐检查：找第一张卡片左缘 x
    # 间距：相邻卡片间隔
    gaps = []
    for i in range(1, len(merged)):
        gaps.append(merged[i][0] - merged[i-1][1] - 1)
    print("  inter-card gaps(px):", gaps)

    # 卡片圆角检测：第一张卡片左上角
    if merged:
        cy = merged[0][0]
        # 找卡片左缘
        for dx in range(0, 30):
            c = px(img, 195 + dx, cy)
            print(f"    corner probe x+{dx}: #{c.red():02x}{c.green():02x}{c.blue():02x}")

    # 滚动条检测：右侧 x=885..899 在卡片区是否有非背景竖条
    print("\n-- scrollbar probe (x 880-899) --")
    col_has = {}
    for x in range(878, 900):
        diffs = 0
        for y in range(80, 700, 10):
            c = px(img, x, y)
            # 与内容背景 #f7f6f2 比较
            if abs(c.red() - 0xf7) > 15 or abs(c.green() - 0xf6) > 15 or abs(c.blue() - 0xf2) > 15:
                diffs += 1
        col_has[x] = diffs
    print("  ", col_has)

    # 侧边栏内部：分类行
    region_stats(img, 12, 60, 182, 200, "sidebar-top")

    # ============ hover shots ============
    for name in ("04_titlebar_hover_nav.png", "05_titlebar_hover_max.png", "06_titlebar_hover_close.png"):
        im = load(name)
        stats(im, name)
        # 找最亮/最红区域
        best = None
        for y in range(0, im.height(), 2):
            for x in range(0, im.width(), 2):
                c = px(im, x, y)
                score = c.red() - (c.green() + c.blue()) / 2
                if best is None or score > best[0]:
                    best = (score, x, y, c)
        if best:
            _, x, y, c = best
            print(f"    reddest px at ({x},{y}): #{c.red():02x}{c.green():02x}{c.blue():02x}")

    # ============ 空态 ============
    for name in ("10_main_empty.png", "11_trash_empty.png", "12_trash_full.png"):
        im = load(name)
        stats(im, name)

    # ============ 对话框 ============
    for name in ("20_settings.png", "21_namedialog.png", "22_category_picker.png"):
        im = load(name)
        stats(im, name)
        # 检查对话框边缘：是否有窗口阴影/圆角
        w, h = im.width(), im.height()
        print(f"    corners: TL={px(im,0,0).name()} TR={px(im,w-1,0).name()} BL={px(im,0,h-1).name()} BR={px(im,w-1,h-1).name()}")
        region_stats(im, 0, 0, w, h, "all")

    # ============ QuickCapture / Widget ============
    for name in ("30_quick_capture.png", "40_todo_widget.png", "41_todo_widget_hover.png", "42_todo_widget_empty.png", "02_main_cat_selected.png", "03_search_state.png", "07_card_hover.png"):
        im = load(name)
        stats(im, name)
        # 四角透明度检测
        for cn, (x, y) in {"TL": (0, 0), "TR": (im.width()-1, 0), "BL": (0, im.height()-1), "BR": (im.width()-1, im.height()-1)}.items():
            c = px(im, x, y)
            print(f"    corner {cn}: a={c.alpha()} #{c.red():02x}{c.green():02x}{c.blue():02x}")


if __name__ == "__main__":
    main()
