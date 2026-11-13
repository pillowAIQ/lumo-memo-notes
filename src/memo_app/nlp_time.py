"""中文 NLP 时间识别：从文本中解析截止日期时间。

parse_due("明天下午3点开会") -> (due_ts, "开会")

保守策略：
- 只识别明确的日期/时间表达（相对日期、周几、月日、N天后、钟点）；
- 独立时间表达要求左侧是空白/标点，避免误伤普通叙述；
- 剥离时间短语后文本为空时，调用方应保留原文。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

# ---------- 常量 ----------

_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "末": 5}
_REL_DAYS = {"今天": 0, "今晚": 0, "明天": 1, "后天": 2, "大后天": 3}
_DEFAULT_TIME = (9, 0)  # 只给日期时默认 09:00

_PERIODS = "上午|中午|下午|晚上|凌晨|傍晚|早上|清晨|晚|早"

# "下午3点" / "3点半" / "9点30分" / "14点"（时后不能紧跟"间"，排除"3小时"）
_TIME_RE = re.compile(
    rf"(?P<period>{_PERIODS})?\s*"
    r"(?P<h>\d{1,2})\s*[点时](?!间)"
    r"(?:\s*(?P<m1>\d{1,2})\s*分|\s*半)?"
    r"(?!\d)"
)
# "14:30" / "下午2:30"（可带时段前缀）
_TIME24_RE = re.compile(
    rf"(?P<period>{_PERIODS})?\s*"
    r"(?<![\d:])(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)(?![\d:])"
)

# ---------- 日期表达 ----------

_RE_WEEKDAY = re.compile(
    r"(?<![每隔])(下下|下|上|本|这)?\s*(周|星期|礼拜)([一二三四五六日天末])"
)
_RE_MONTHDAY = re.compile(
    r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]"
)
_RE_AFTER = re.compile(r"(\d+)\s*(?:天|日)\s*后")
_RE_REL = re.compile(r"今天|今晚|明天|后天|大后天")
# 裸"15日/15号"（不含"月"，排除"N天后/N日后"、"每月X日"及量词/类别字误伤）
_RE_BAREDAY = re.compile(
    r"(?<!\d)(?<!每月)(?<!第)(\d{1,2})\s*[日号]"
    r"(?!\s*后|\d|[元记常假期岁本历线楼房室桌车站路口层座栋单元位排码桥街巷弄门窗])"
)


def _apply_period(hour: int, minute: int, period: str | None,
                  pm_lean: bool = False) -> tuple[int, int] | None:
    """按时段词修正为 24 小时制；非法返回 None。

    返回小时可为 24 表示"次日 0 点"（晚上12点/晚上0点 = 24:00），
    由调用方归一化为次日 00:00。
    """
    if hour > 24:
        return None
    if period in ("晚上", "傍晚", "晚"):
        if hour in (0, 12):
            return 24, minute  # 晚上12点/0点 = 次日 00:00
        if hour < 12:
            hour += 12
    elif period == "下午":
        if hour < 12:
            hour += 12  # 下午12点 = 12:00 正午
    elif period == "中午":
        if hour in (1, 2):
            hour += 12
    elif period in ("上午", "早上", "清晨", "早"):
        if hour == 12:
            hour = 0
    elif period == "凌晨":
        if hour == 12:
            hour = 0  # 凌晨12点 = 当日 00:00
    elif period is None and pm_lean and 1 <= hour <= 6:
        # 裸"3点"中文任务场景偏下午；"早上6点"等显式时段不受影响
        hour += 12
    return hour, minute


def _parse_time(h: int, m: int | None, half: bool, period: str | None,
                pm_lean: bool = False):
    minute = 30 if half else (m if m is not None else 0)
    if minute > 59:
        return None
    return _apply_period(h, minute, period, pm_lean)


def _time_at(text: str, pos: int, default_period: str | None = None):
    """在 pos 处尝试匹配时间表达，返回 (end_pos, (h, m)) 或 None。

    default_period：时间未显式带时段词时使用的兜底时段（如"今晚"->晚上）。
    """
    m = _TIME_RE.match(text, pos)
    if m:
        hm = _parse_time(
            int(m.group("h")),
            int(m.group("m1")) if m.group("m1") else None,
            "半" in m.group(0),
            m.group("period") or default_period,
            pm_lean=True,  # "H点"裸钟点偏 PM
        )
        if hm:
            return m.end(), hm
    m24 = _TIME24_RE.match(text, pos)
    if m24:
        hm = _parse_time(
            int(m24.group("h")), int(m24.group("m")), False,
            m24.group("period") or default_period,
        )
        if hm:
            return m24.end(), hm
    return None


def _find_time_after(text: str, pos: int, default_period: str | None = None):
    """日期表达之后（允许分隔符）寻找时间；返回 (end, (h, m)) 或 None。"""
    m = re.match(r"[\s,，、。.；;：:~～\-—/]*", text[pos:])
    start = pos + (m.end() if m else 0)
    return _time_at(text, start, default_period)


def _date_candidates(text: str, now: datetime):
    """产出 (start, end, date) 候选，date 为 datetime.date。"""
    today = now.date()
    monday = today - timedelta(days=today.weekday())

    for m in _RE_WEEKDAY.finditer(text):
        prefix = m.group(1)
        wd = _WEEKDAY.get(m.group(3))
        if wd is None:
            continue
        if prefix == "下下":
            d = monday + timedelta(weeks=2, days=wd)
        elif prefix == "下":
            d = monday + timedelta(weeks=1, days=wd)
        elif prefix in ("本", "这"):
            d = monday + timedelta(days=wd)
        elif prefix == "上":
            d = monday - timedelta(weeks=1, days=wd)
        else:
            d = today + timedelta(days=(wd - today.weekday()) % 7)
        yield m.start(), m.end(), d, None

    for m in _RE_MONTHDAY.finditer(text):
        y = int(m.group(1)) if m.group(1) else now.year
        mo, da = int(m.group(2)), int(m.group(3))
        try:
            d = datetime(y, mo, da).date()
        except ValueError:
            continue
        if not m.group(1) and d < today:
            try:
                d = datetime(y + 1, mo, da).date()
            except ValueError:
                continue
        yield m.start(), m.end(), d, None

    for m in _RE_AFTER.finditer(text):
        yield m.start(), m.end(), today + timedelta(days=int(m.group(1))), None

    for m in _RE_REL.finditer(text):
        word = m.group(0)
        # "今晚"隐含晚间时段
        night = "晚上" if word == "今晚" else None
        yield m.start(), m.end(), today + timedelta(days=_REL_DAYS[word]), night

    for m in _RE_BAREDAY.finditer(text):
        da = int(m.group(1))
        y, mo = now.year, now.month
        try:
            d = datetime(y, mo, da).date()
        except ValueError:
            continue
        if d < today:
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
            try:
                d = datetime(y, mo, da).date()
            except ValueError:
                continue
        yield m.start(), m.end(), d, None


def _time_only_candidates(text: str):
    """独立时间表达（无日期）-> 当天该时刻。

    左侧必须是行首/空白/标点，避免"我9点去"这类叙述误伤。
    """
    for regex in (_TIME_RE, _TIME24_RE):
        for m in regex.finditer(text):
            if m.start() > 0:
                prev = text[m.start() - 1]
                if not prev.isspace() and prev not in ",，、。.；;：:!！?？()（）[]【】\"'“”‘’~～-—":
                    continue
            if regex is _TIME_RE:
                hm = _parse_time(
                    int(m.group("h")),
                    int(m.group("m1")) if m.group("m1") else None,
                    "半" in m.group(0),
                    m.group("period"),
                    pm_lean=True,
                )
            else:
                hm = _parse_time(
                    int(m.group("h")), int(m.group("m")), False, m.group("period")
                )
            if hm:
                yield m.start(), m.end(), hm


def parse_due(text: str) -> tuple[int | None, str]:
    """解析文本中的截止时间。

    返回 (due_ts, 清理后文本)。due_ts 为 Unix 秒；无时间表达返回 (None, 原文)。
    """
    if not text or not text.strip():
        return None, text
    now = datetime.now()

    # 1) 日期 + 可选时间
    cands = []
    for s, e, d, night in _date_candidates(text, now):
        span_end = e
        h, mi = _DEFAULT_TIME
        t = _find_time_after(text, e, night)
        if t is not None:
            span_end, (h, mi) = t
        if night and t is None:
            h, mi = 20, 0  # "今晚"无显式时间 -> 20:00
        if h >= 24:  # "晚上12点"等 = 次日 00:00
            d += timedelta(days=1)
            h -= 24
        dt = datetime.combine(d, datetime.min.time()).replace(hour=h, minute=mi)
        cands.append((s, span_end, int(dt.timestamp())))
    if cands:
        s, e, ts = min(cands, key=lambda c: (c[0], -(c[1] - c[0])))
        return ts, _strip_spans(text, [(s, e)])

    # 2) 仅时间 -> 今天该时刻
    tcands = list(_time_only_candidates(text))
    if tcands:
        s, e, (h, mi) = min(tcands, key=lambda c: c[0])
        base = now + timedelta(days=1) if h >= 24 else now
        h = h - 24 if h >= 24 else h
        dt = base.replace(hour=h, minute=mi, second=0, microsecond=0)
        return int(dt.timestamp()), _strip_spans(text, [(s, e)])

    return None, text


# ---------- 任务标记：!优先级 / @重复 / 提前N分钟 ----------

# 独立 token 才识别：!高 / ！中 / @每周（标记前须空白/行首，后须空白/标点/句末）
_TOKEN_END = r"[\s，,。.;；:：、！？!?]"
_RE_PRIORITY = re.compile(r"(?<!\S)[!！]([高中低])(?=" + _TOKEN_END + r"|$)")
_PRIORITY_MAP = {"低": 1, "中": 2, "高": 3}
_RE_RECUR = re.compile(r"(?<!\S)@(?:每(天|周|月|年))(?=" + _TOKEN_END + r"|$)")
_RECUR_MAP = {"天": "daily", "周": "weekly", "月": "monthly", "年": "yearly"}
# advance 需后接"提醒语境"或"标点/空白* + 句末/提醒词"，避免"提前30分钟，改到…"误删正文
_RE_ADVANCE = re.compile(
    r"提前\s*(半|\d+)\s*(分钟|小时|钟头|天)"
    r"(?=[\s，,。.;；:：、！？!?]*(?:提醒|通知|闹钟|截止|到期)"
    r"|[\s，,。.;；:：、！？!?]*$)"
)
_ADVANCE_UNIT = {"分钟": 1, "小时": 60, "钟头": 60, "天": 1440}
_ADVANCE_MAX = 7 * 1440  # 提前量上限 7 天
# 句首"提醒我/通知我"冗余前缀
_RE_NOTIFY = re.compile(r"^(?:请|帮我|麻烦)?(?:提醒我|通知我)[，,、。.!！?？;；:：\s]*")
_EDGE_PUNCT = r"[\s，,、。.；;：:~～\-—（）「」『』【】/！？!?]+"


def _advance_minutes(num_s: str, unit: str) -> int | None:
    if len(num_s) > 6:
        return None
    if num_s == "半":
        minutes = 30 if unit in ("小时", "钟头") else None
    else:
        minutes = int(num_s) * _ADVANCE_UNIT[unit]
    if minutes is None or minutes <= 0 or minutes > _ADVANCE_MAX:
        return None
    return minutes


def parse_task(text: str) -> tuple[int | None, int, str | None, int, str]:
    """解析截止时间 + 优先级/重复/提前提醒标记。

    返回 (due_ts, priority, recur, advance_min, cleaned_text)。
    priority=0 / recur=None / advance_min=0 表示未识别到对应标记。
    """
    if not text or not text.strip():
        return None, 0, None, 0, text

    spans: list[tuple[int, int]] = []
    priority = 0
    recur = None
    advance = 0

    for m in _RE_PRIORITY.finditer(text):
        priority = _PRIORITY_MAP[m.group(1)]
        spans.append((m.start(), m.end()))
    for m in _RE_RECUR.finditer(text):
        recur = _RECUR_MAP[m.group(1)]
        spans.append((m.start(), m.end()))
    for m in _RE_ADVANCE.finditer(text):
        minutes = _advance_minutes(m.group(1), m.group(2))
        if minutes is None or minutes <= 0:
            continue
        advance = minutes
        spans.append((m.start(), m.end()))

    work = _strip_spans(text, spans) if spans else text
    work = _RE_NOTIFY.sub("", work.lstrip(), count=1)
    work = re.sub(r"^" + _EDGE_PUNCT, "", work)
    due_ts, cleaned = parse_due(work)
    return due_ts, priority, recur, advance, cleaned


def _strip_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """移除时间短语，整理残留空白/标点。"""
    spans = sorted(spans)
    out = []
    pos = 0
    for s, e in spans:
        out.append(text[pos:s])
        pos = e
    out.append(text[pos:])
    cleaned = "".join(out)
    edge = r"[\s,，、。.；;：:~～\-—（）「」『』【】/！？!?]"
    cleaned = re.sub(edge + r"+$", "", cleaned)
    cleaned = re.sub(r"^" + edge + r"+", "", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    return cleaned


def format_due(due_ts: int, now: datetime | None = None) -> str:
    """截止时间的简短展示：'今天 14:30' / '明天' / '周五 09:00' / '03-15'。"""
    now = now or datetime.now()
    dt = datetime.fromtimestamp(due_ts)
    today = now.date()
    d = dt.date()
    hm = f"{dt.hour:02d}:{dt.minute:02d}"
    delta = (d - today).days
    if delta == 0:
        return f"今天 {hm}"
    if delta == 1:
        return f"明天 {hm}"
    if delta == 2:
        return f"后天 {hm}"
    if 0 < delta <= 7:
        wd = "一二三四五六日"[dt.weekday()]
        return f"周{wd} {hm}"
    if d.year == today.year:
        return f"{d.month:02d}-{d.day:02d} {hm}"
    return f"{d.year}-{d.month:02d}-{d.day:02d} {hm}"
