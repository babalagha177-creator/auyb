import html
import json
import os
import re
import time
import uuid
import unicodedata
import base64
from io import BytesIO
from datetime import datetime, timedelta

import pandas as pd
import pytz
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="نظام باب الآغا", layout="wide")

IRAQ_TZ = pytz.timezone("Asia/Baghdad")
UNIT_OPTIONS = ["قطعة", "عربانة", "كيس", "كيلو", "طاولي", "عجنه", "نص عجنه", "صندوق", "كارتون", "علبة"]
PRODUCT_UNIT_OPTIONS = ["صندوق", "قطعة", "كيلو", "كيس", "عربانه", "طاولي", "نص عجنه", "عجنه", "صينيه", "كارتون", "مخصص"]
INVENTORY_UNIT_OPTIONS = ["عجنه", "نص عجنه", "كيلو", "كيس", "صندوق", "كارتون", "طاولي", "عربانه", "صينيه", "قطعة", "مخصص"]
PRODUCTION_STATUS_OPTIONS = ["بانتظار الإنتاج", "تمت المباشرة", "جاهز"]
# صلاحيات الشاشات + صلاحيات الإدارة (المفاتيح بالإنجليزية للـ JSON في Supabase)
SCREEN_PERMISSION_ORDER = [
    ("can_view_home", "عرض الرئيسية"),
    ("can_view_baker_screen", "عرض شاشة الخلفة"),
    ("can_view_inventory", "عرض شاشة الجرد والطلب"),
    ("can_request_production", "تعديل المطلوب وطلب الإنتاج"),
    ("can_view_preview", "عرض المعاينة"),
    ("can_view_notifications", "عرض الإشعارات"),
]
ADMIN_PERMISSION_ORDER = [
    ("can_add_users", "إضافة مستخدمين جدد"),
    ("can_edit_products", "تعديل وإدارة السلع"),
    ("can_view_archive", "الاطلاع على الأرشيف"),
]
PERMISSION_KEYS = [k for k, _ in SCREEN_PERMISSION_ORDER + ADMIN_PERMISSION_ORDER]
PERMISSION_LABELS_AR = dict(SCREEN_PERMISSION_ORDER + ADMIN_PERMISSION_ORDER)
ROLE_LABEL_TO_CODE = {
    "مدير النظام": "Admin",
    "مسؤول قسم": "DeptManager",
    "خلفة الإنتاج": "Baker",
}
ROLE_CODE_TO_LABEL = {v: k for k, v in ROLE_LABEL_TO_CODE.items()}
# أدوار قديمة في قاعدة البيانات — تُعرض في واجهة التعديل فقط (يُرحّل الدور تلقائياً)
LEGACY_ROLE_TO_LABEL = {"Warehouse": "مسؤول قسم"}
SHIFT_TO_BAKER_COLUMN = {
    "صباحي": "morning_baker_id",
    "مسائي": "evening_baker_id",
    "ليلي": "night_baker_id",
}


def now_baghdad() -> datetime:
    return datetime.now(IRAQ_TZ)


def get_baghdad_now() -> datetime:
    """اسم موحّد لطابع بغداد الحالي (Asia/Baghdad) — يُفضَّل استخدامه في كل منطق زمني."""
    return now_baghdad()


def baghdad_iso_now() -> str:
    return get_baghdad_now().isoformat()


def baghdad_shift_cycle_info(dt: datetime | None = None) -> dict:
    """
    شفتات مخبز باب الآغا بتوقيت بغداد:
    صباحي 06:00–14:59، مسائي 15:00–22:59، ليلي 23:00–05:59 (يمتد بعد منتصف الليل).
    يعيد shift_name للعرض/التخزين و cycle_key فريداً لهذا الشفت.
    """
    dt = dt if dt is not None else get_baghdad_now()
    if dt.tzinfo is None:
        dt = IRAQ_TZ.localize(dt)
    else:
        dt = dt.astimezone(IRAQ_TZ)
    d = dt.date()
    hour = dt.hour
    if 6 <= hour < 15:
        return {"shift_name": "صباحي", "cycle_key": f"{d.isoformat()}-صباحي", "label": "الشفت الصباحي"}
    if 15 <= hour < 23:
        return {"shift_name": "مسائي", "cycle_key": f"{d.isoformat()}-مسائي", "label": "الشفت المسائي"}
    if 0 <= hour < 6:
        base = d - timedelta(days=1)
        return {"shift_name": "ليلي", "cycle_key": f"{base.isoformat()}-ليلي", "label": "الشفت الليلي"}
    return {"shift_name": "ليلي", "cycle_key": f"{d.isoformat()}-ليلي", "label": "الشفت الليلي"}


def baker_column_for_shift_name(shift_name: str | None) -> str:
    return SHIFT_TO_BAKER_COLUMN.get(str(shift_name or "").strip(), "morning_baker_id")


def current_shift_baker_column() -> str:
    return baker_column_for_shift_name(baghdad_shift_cycle_info().get("shift_name"))


def _baghdad_12h_display_parts(dt: datetime) -> tuple[int, str, str]:
    """ساعة 1–12 ودقائق بنصين وص/م ثابتة (لا تعتمد على locale لـ %p)."""
    if dt.tzinfo is None:
        dt = IRAQ_TZ.localize(dt)
    else:
        dt = dt.astimezone(IRAQ_TZ)
    h24 = dt.hour
    h12 = h24 % 12
    if h12 == 0:
        h12 = 12
    ampm = "ص" if h24 < 12 else "م"
    mm = dt.strftime("%M")
    return h12, mm, ampm


def format_baghdad_time(value) -> str:
    """تحويل/عرض الوقت بصيغة بغداد دائماً (بدون إزاحة مزدوجة — يُفضَّح التوقيت عبر parse_to_baghdad_dt)."""
    if value is None or str(value).strip() == "":
        return "-"
    dt = parse_to_baghdad_dt(value)
    if dt is None:
        return str(value)
    try:
        h12, mm, ampm = _baghdad_12h_display_parts(dt)
        return f"{dt.strftime('%Y-%m-%d')} {h12}:{mm} {ampm}"
    except Exception:
        return str(value)


def format_baghdad_compact(value) -> str:
    """تنسيق مختصر: س:د | ي-ش"""
    if value is None or str(value).strip() == "":
        return "-"
    dt = parse_to_baghdad_dt(value)
    if dt is None:
        return str(value)
    try:
        h12, mm, ampm = _baghdad_12h_display_parts(dt)
        return f"{h12}:{mm} {ampm} | " + dt.strftime("%d-%m")
    except Exception:
        return str(value)


def coerce_recommendation_log(value) -> list[dict]:
    """قائمة إدخالات سجل التوصيات من JSONB."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for item in value:
        if isinstance(item, dict):
            out.append(dict(item))
    return out


def recommendation_log_entry_dt(entry: dict) -> datetime | None:
    """قراءة وقت إدخال السجل من المفاتيح المدعومة (at ثم timestamp) بتوقيت بغداد."""
    if not isinstance(entry, dict):
        return None
    dt = parse_to_baghdad_dt(entry.get("at"))
    if dt is not None:
        return dt
    return parse_to_baghdad_dt(entry.get("timestamp"))


def recommendation_log_sorted(entries: list[dict]) -> list[dict]:
    """الأحدث أولاً (LIFO للعرض)."""

    def _ts(e: dict) -> float:
        t = recommendation_log_entry_dt(e)
        return t.timestamp() if t is not None else 0.0

    return sorted(entries, key=_ts, reverse=True)


def _request_qty_or_none(value) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _entry_minute_key(entry: dict) -> str:
    dt = recommendation_log_entry_dt(entry)
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def _is_ghost_ui_log_entry(entry: dict) -> bool:
    kind = str(entry.get("kind") or "").strip()
    notes = str(entry.get("notes") or "").strip()
    return kind == "reopen" or notes == "إعادة فتح نافذة التعديل"


def sanitize_recommendation_log_for_export(raw_log) -> list[dict]:
    """
    تنظيف سجل التوصيات قبل العرض/التصدير:
    - تجاهل أسطر فتح/إعادة فتح الواجهة (ليست تغييرات عمل).
    - تجاهل أي تكرار بنفس الكمية إذا تكرر في نفس الدقيقة مقارنة بالسجل السابق.
    """
    chronological = list(coerce_recommendation_log(raw_log))
    chronological.sort(key=lambda e: recommendation_log_entry_dt(e).timestamp() if recommendation_log_entry_dt(e) else 0.0)
    cleaned: list[dict] = []
    prev_kept: dict | None = None
    for entry in chronological:
        if _is_ghost_ui_log_entry(entry):
            continue
        if prev_kept is not None:
            cur_min = _entry_minute_key(entry)
            prev_min = _entry_minute_key(prev_kept)
            cur_req = _request_qty_or_none(entry.get("request_qty"))
            prev_req = _request_qty_or_none(prev_kept.get("request_qty"))
            cur_cur = _request_qty_or_none(entry.get("current_qty"))
            prev_cur = _request_qty_or_none(prev_kept.get("current_qty"))
            cur_unit = str(entry.get("unit_val") or entry.get("unit") or "").strip()
            prev_unit = str(prev_kept.get("unit_val") or prev_kept.get("unit") or "").strip()
            cur_notes = str(entry.get("notes") or "").strip()
            prev_notes = str(prev_kept.get("notes") or "").strip()
            if cur_min and cur_min == prev_min and cur_req is not None and prev_req is not None and cur_req == prev_req:
                # إزالة التكرار فقط إذا كان الإدخال مطابقاً فعلياً، وليس تعديلًا داخل نفس الدقيقة.
                if cur_cur == prev_cur and cur_unit == prev_unit and cur_notes == prev_notes:
                    continue
        cleaned.append(entry)
        prev_kept = entry
    return recommendation_log_sorted(cleaned)


def format_recommendation_log_for_export(raw_log) -> str:
    """نص واحد للمعاينة والتقارير وPDF/Excel — الأحدث أولاً بتوقيت بغداد."""
    entries = sanitize_recommendation_log_for_export(raw_log)
    if not entries:
        return "—"
    bits: list[str] = []
    for e in entries[:14]:
        ts = format_baghdad_compact(e.get("at"))
        kind = str(e.get("kind") or "").strip()
        rq = e.get("request_qty")
        if rq is not None and str(rq).strip() != "":
            bits.append(f"{ts}-{rq}")
        else:
            bits.append(ts)
    tail = " …" if len(entries) > 14 else ""
    return " | ".join(bits) + tail


# بادئات صفوف التفصيل في جداول التصدير/المعاينة (صف لكل طلب في السجل)
_FLAT_SUB_PREFIX = "    ↳ "
_FLAT_TOTAL_MARKER = "    ✓ "


def recommendation_request_changes_chronological(raw_log) -> list[dict]:
    """إدخالات request_change فقط — من الأقدم للأحدث (لحساب الفروق)."""
    out: list[dict] = []
    for e in coerce_recommendation_log(raw_log):
        if str(e.get("kind") or "").strip() != "request_change":
            continue
        out.append(e)

    def _ts(ev: dict) -> float:
        t = recommendation_log_entry_dt(ev)
        return t.timestamp() if t is not None else 0.0

    return sorted(out, key=_ts)


def _delta_request_change(entry: dict) -> int:
    try:
        new_q = int(float(entry.get("request_qty") or 0))
        prev_q = int(float(entry.get("previous_request_qty") or 0))
    except (TypeError, ValueError):
        return 0
    return new_q - prev_q


def recommendation_log_entry_match_key(entry: dict) -> str:
    """مفتاح ثابت لمطابقة إدخال السجل عند التحديث (بدون entry_id في السجلات القديمة)."""
    eid = str(entry.get("entry_id") or "").strip()
    if eid:
        return f"id:{eid}"
    return "k:" + "|".join(
        [
            str(entry.get("at") or ""),
            str(entry.get("request_qty") or ""),
            str(entry.get("previous_request_qty") or ""),
            str(entry.get("by") or ""),
            str(entry.get("kind") or ""),
        ]
    )


def first_order_request_qty(raw_log, total_request_qty: int) -> int:
    """كمية السطر الأول في الكارت الأصلي (ليس مجموع كل الزيادات)."""
    try:
        tot = int(float(total_request_qty or 0))
    except (TypeError, ValueError):
        tot = 0
    ch = recommendation_request_changes_chronological(raw_log)
    if not ch:
        return tot
    try:
        p0 = int(float(ch[0].get("previous_request_qty") or 0))
        n0 = int(float(ch[0].get("request_qty") or 0))
    except (TypeError, ValueError):
        return tot
    if p0 == 0:
        return n0
    return p0


def supplemental_recommendation_change_entries(raw_log) -> list[dict]:
    """إدخالات request_change للكروت الإضافية (تحت الكارت الأصلي) بترتيب زمني من الأقدم للأحدث."""
    ch = recommendation_request_changes_chronological(raw_log)
    if not ch:
        return []
    try:
        p0 = int(float(ch[0].get("previous_request_qty") or 0))
    except (TypeError, ValueError):
        p0 = 0
    if p0 == 0:
        return ch[1:]
    return ch


def recommendation_original_row_timestamp_display(raw_log, product_last_updated_at) -> str:
    """وقت السطر الأول (أول request_change) بتوقيت بغداد؛ وإلا وقت آخر تحديث للسلعة."""
    ch = recommendation_request_changes_chronological(raw_log)
    if ch:
        return format_baghdad_time(ch[0].get("at"))
    return format_baghdad_time(product_last_updated_at)


def filter_recommendation_log_for_cycle(raw_log, target_cycle_key: str | None) -> list[dict]:
    """إرجاع إدخالات السجل المطابقة للشفت/الدورة المطلوبة فقط."""
    target = str(target_cycle_key or "").strip()
    if not target:
        return []
    out: list[dict] = []
    for entry in coerce_recommendation_log(raw_log):
        ent_dt = parse_to_baghdad_dt(entry.get("at"))
        if ent_dt is None:
            continue
        if baghdad_shift_cycle_info(ent_dt)["cycle_key"] == target:
            out.append(entry)
    return out


def segment_production_status_display(entry: dict, fallback: str) -> str:
    fb = str(fallback or "").strip() or PRODUCTION_STATUS_OPTIONS[0]
    if fb not in PRODUCTION_STATUS_OPTIONS:
        fb = PRODUCTION_STATUS_OPTIONS[0]
    seg = str(entry.get("segment_production_status") or "").strip()
    if seg in PRODUCTION_STATUS_OPTIONS:
        return seg
    return fb


def aggregate_total_production_status(raw_log, product_status: str) -> str:
    """حالة سطر المجموع: «جاهز» فقط إذا كل الأجزاء جاهزة؛ وإلا «قيد الإنتاج»."""
    base = str(product_status or "").strip() or PRODUCTION_STATUS_OPTIONS[0]
    if base not in PRODUCTION_STATUS_OPTIONS:
        base = PRODUCTION_STATUS_OPTIONS[0]
    statuses: list[str] = [base]
    for ent in supplemental_recommendation_change_entries(raw_log):
        statuses.append(segment_production_status_display(ent, base))
    if all(s == "جاهز" for s in statuses):
        return "جاهز"
    return "قيد الإنتاج"


def format_required_total_formula(raw_log, req_total: int) -> str:
    """صيغة المجموع في خانة المطلوب، مثل (11 + 20 = 31) — يجب أن يطابق إجمالي request_qty الحالي في القاعدة."""
    try:
        rt = int(float(req_total))
    except (TypeError, ValueError):
        rt = 0
    extras = supplemental_recommendation_change_entries(raw_log)
    if not extras:
        return str(rt)
    base = int(first_order_request_qty(raw_log, rt))
    ds = [_delta_request_change(e) for e in extras]
    if base + sum(ds) != rt:
        # السجل لا يطابق الطلب الحالي (تعديل خارج السجل، ترحيل، إلخ) — لا تعرض صيغة مضللة.
        return str(rt)
    parts_str = str(base)
    for d in ds:
        if d >= 0:
            parts_str += f" + {d}"
        else:
            parts_str += f" − {abs(int(d))}"
    return f"({parts_str} = {rt})"


def render_recommendation_log_ui(raw_log, *, max_visible: int = 3, target_cycle_key: str | None = None) -> None:
    """عرض سجل التوصيات للشفت الحالي فقط: أحدثها في الأعلى وبخط عريض؛ وأكثر من max_visible مع منسدل «عرض الكل»."""
    cycle_key = str(target_cycle_key or baghdad_shift_cycle_info()["cycle_key"]).strip()
    scoped_log = filter_recommendation_log_for_cycle(raw_log, cycle_key)
    entries = sanitize_recommendation_log_for_export(scoped_log)
    if not entries:
        return
    st.caption("سجل الطلبات والتعديلات (الأحدث في الأعلى)")
    head = entries[:max_visible]
    tail = entries[max_visible:]
    for idx, e in enumerate(head):
        ts = format_baghdad_compact(e.get("at"))
        who = (str(e.get("by") or "—").strip() or "—")
        rq = e.get("request_qty")
        kind = str(e.get("kind") or "").strip()
        extra = []
        if rq is not None and str(rq).strip() != "":
            extra.append(f"مطلوب: {rq}")
        if kind == "reopen":
            extra.append("إعادة فتح نافذة التعديل")
        elif kind == "request_change":
            extra.append("تعديل مطلوب")
        tail_txt = " — ".join([ts, who] + extra) if extra else f"{ts} — {who}"
        if idx == 0:
            st.markdown(f'<p dir="rtl" style="margin:0.15rem 0"><strong>{html.escape(tail_txt)}</strong></p>', unsafe_allow_html=True)
        else:
            st.markdown(f'<p dir="rtl" style="margin:0.15rem 0;color:#555">{html.escape(tail_txt)}</p>', unsafe_allow_html=True)
    if tail:
        with st.expander("عرض الكل"):
            for e in tail:
                ts = format_baghdad_compact(e.get("at"))
                who = (str(e.get("by") or "—").strip() or "—")
                rq = e.get("request_qty")
                bits = [ts, who]
                if rq is not None and str(rq).strip() != "":
                    bits.append(f"مطلوب: {rq}")
                st.markdown(f'<p dir="rtl" style="margin:0.2rem 0">{html.escape(" — ".join(bits))}</p>', unsafe_allow_html=True)


def parse_to_baghdad_dt(value) -> datetime | None:
    """تحويل قيمة الوقت إلى datetime واعٍ بمنطقة بغداد."""
    if value is None or str(value).strip() == "":
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            text = str(value).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return IRAQ_TZ.localize(dt)
        return dt.astimezone(IRAQ_TZ)
    except Exception:
        return None


def is_edit_window_expired(last_updated_at, *, minutes: int = 10) -> bool:
    """يعيد True إذا تجاوزت مدة آخر تحديث نافذة التعديل المحددة."""
    dt = parse_to_baghdad_dt(last_updated_at)
    if dt is None:
        return False
    return (get_baghdad_now() - dt) > timedelta(minutes=minutes)


def should_lock_row_for_shift(
    row_shift_cycle_key,
    last_updated_at,
    *,
    current_cycle_key: str,
    is_admin: bool,
    minutes: int = 10,
) -> bool:
    """
    تطبيق قفل الـ10 دقائق فقط إذا كانت السلعة محفوظة ضمن نفس الشفت الحالي.
    أي سجل من شفت مختلف يبقى مفتوحاً دائماً في الشفت الجديد.
    """
    if is_admin:
        return False
    row_cycle_key = str(row_shift_cycle_key or "").strip()
    if not row_cycle_key or row_cycle_key != str(current_cycle_key or "").strip():
        return False
    return is_edit_window_expired(last_updated_at, minutes=minutes)


def _success_beep_html() -> str:
    # نغمة نجاح قصيرة بصيغة WAV (PCM) مضمّنة داخل الصفحة.
    wav_base64 = (
        "UklGRlQFAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YT"
        "AFAAAgP1Njb3d7f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/"
        "f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f3"
        "9/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/"
        "f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f3"
        "9/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/"
        "f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f3"
        "9/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/"
        "f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f3"
        "9/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/"
    )
    return f"""
    <script>
    (function() {{
      try {{
        const audio = new Audio("data:audio/wav;base64,{wav_base64}");
        audio.volume = 0.35;
        audio.play();
      }} catch (e) {{}}
    }})();
    </script>
    """


def play_success_beep():
    st.markdown(_success_beep_html(), unsafe_allow_html=True)


def normalize_arabic_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    return (
        normalized.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ؤ", "و")
        .replace("ئ", "ي")
        .replace("ى", "ي")
    )


def normalize_username(value: str) -> str:
    base = normalize_arabic_text(value)
    cleaned = unicodedata.normalize("NFD", base)
    cleaned = "".join(ch for ch in cleaned if unicodedata.category(ch) != "Mn")
    cleaned = cleaned.replace(" ", "").replace("_", "").replace("-", "")
    return cleaned


def _normalize_secret_value(val) -> str:
    s = str(val or "").strip()
    if s.startswith("\ufeff"):
        s = s.lstrip("\ufeff").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def _secrets_pick(*keys: str):
    for k in keys:
        try:
            v = st.secrets[k]
        except Exception:
            continue
        if v is None:
            continue
        s = _normalize_secret_value(v)
        if s:
            return s
    return ""


def resolve_supabase_credentials():
    url = ""
    key = ""
    nested = None
    try:
        nested = st.secrets["supabase"]
    except Exception:
        nested = None
    if nested is not None and not isinstance(nested, str):
        getter = getattr(nested, "get", None)
        if callable(getter):
            url = _normalize_secret_value(getter("SUPABASE_URL") or getter("url") or "")
            key = _normalize_secret_value(
                getter("SUPABASE_KEY")
                or getter("key")
                or getter("SUPABASE_ANON_KEY")
                or getter("SUPABASE_SERVICE_ROLE_KEY")
                or ""
            )
    if not url:
        url = _secrets_pick("SUPABASE_URL")
    if not key:
        key = _secrets_pick("SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY")
    return url, key


@st.cache_resource(show_spinner=False)
def _cached_supabase(url: str, key: str):
    return create_client(url, key)


def init_supabase():
    try:
        url, key = resolve_supabase_credentials()
        if not url or not key:
            raise ValueError("SUPABASE credentials missing.")
        return _cached_supabase(url, key)
    except Exception as exc:
        st.error(f"تعذر الاتصال بـ Supabase: {exc}")
        st.stop()


supabase_client = init_supabase()


def _exception_chain_text(exc: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        parts.append(str(cur))
        parts.append(repr(cur))
        msg = getattr(cur, "message", None)
        if msg is not None:
            parts.append(str(msg))
        if isinstance(msg, dict):
            parts.append(str(msg.get("message", "")))
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    return "\n".join(parts).lower()


def is_network_transport_error(exc: BaseException) -> bool:
    blob = _exception_chain_text(exc)
    needles = (
        "10035",
        "winerror",
        "non-blocking socket",
        "socket",
        "timeout",
        "timed out",
        "connection reset",
        "connection aborted",
        "broken pipe",
        "errno 11",
        "eagain",
        "temporarily unavailable",
        "sslerror",
        "readerror",
        "connecterror",
        "network is unreachable",
        "remote end closed connection",
    )
    if any(n in blob for n in needles):
        return True
    if isinstance(exc, (TimeoutError, BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return True
    if isinstance(exc, OSError):
        if getattr(exc, "winerror", None) == 10035:
            return True
        errno = getattr(exc, "errno", None)
        if errno in (11, 35, 10035):
            return True
    return False


def is_schema_missing_error(exc: BaseException) -> bool:
    blob = _exception_chain_text(exc)
    return (
        "pgrst205" in blob
        or "schema cache" in blob
        or "could not find the table" in blob
        or ("42p01" in blob and "undefined_table" in blob)
    )


def supabase_with_retry(operation, *, max_attempts: int = 3, delay_seconds: float = 2.0):
    """
    ينفّذ استدعاء Supabase مع إعادة المحاولة عند أخطاء الشبكة فقط (مثل WinError 10035).
    يعيد (نجاح، النتيجة، آخر استثناء).
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return True, operation(), None
        except Exception as e:
            last_exc = e
            if attempt < max_attempts and is_network_transport_error(e):
                time.sleep(delay_seconds)
                continue
            return False, None, last_exc


def invalidate_products_cache_after_mutation() -> None:
    """بعد أي كتابة على جدول المنتجات — مسح st.cache_data حتى لا تبقى أي دوال مخزّنة تعيد بيانات قديمة."""
    try:
        st.cache_data.clear()
    except Exception:
        pass


def clear_cache_and_rerun() -> None:
    """مسار موحّد: مسح الكاش أولاً ثم إعادة تشغيل التطبيق فوراً."""
    try:
        st.cache_data.clear()
    except Exception:
        pass
    st.rerun()


def settle_write_then_refresh(*, sleep_seconds: float = 0.5) -> None:
    """
    بعد أي عملية كتابة ناجحة: انتظار قصير لتفادي سباق القراءة مع استجابة Supabase،
    ثم مسح الكاش بشكل قطعي قبل إعادة التشغيل.
    """
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)
    clear_cache_and_rerun()


def load_products_live_no_cache() -> pd.DataFrame:
    """جلب مباشر من قاعدة البيانات بدون أي اعتماد على دوال قد تكون مخزّنة."""
    ok, res, exc = supabase_with_retry(lambda: supabase_client.table("products").select("*").order("name").execute())
    if not ok or res is None:
        if exc is not None and is_network_transport_error(exc):
            if not st.session_state.get("_warned_products_load_live"):
                st.session_state["_warned_products_load_live"] = True
                st.warning("يرجى التحقق من اتصال الإنترنت. تعذر جلب قائمة السلع للمعاينة المباشرة.")
        elif exc is not None and is_schema_missing_error(exc):
            show_schema_help_and_stop(exc)
        elif exc is not None:
            st.error(f"تعذر جلب السلع للمعاينة المباشرة: {exc}")
        return pd.DataFrame(
            columns=[
                "id", "name", "unit_val", "section_name", "assigned_baker_id", "morning_baker_id", "evening_baker_id", "night_baker_id", "current_qty", "request_qty",
                "notes", "production_status", "last_updated_by", "last_updated_at", "shift_name", "shift_cycle_key",
                "recommendation_log",
            ]
        )
    raw = pd.DataFrame(res.data or [])
    if raw.empty:
        return pd.DataFrame(
            columns=[
                "id", "name", "unit_val", "section_name", "assigned_baker_id", "morning_baker_id", "evening_baker_id", "night_baker_id", "current_qty", "request_qty",
                "notes", "production_status", "last_updated_by", "last_updated_at", "shift_name", "shift_cycle_key",
                "recommendation_log",
            ]
        )
    if "current_qty" not in raw.columns:
        raw["current_qty"] = 0
    if "request_qty" not in raw.columns:
        raw["request_qty"] = 0
    # توافق خلفي: بعض البيئات قد تستخدم unit بدلاً من unit_val.
    if "unit_val" not in raw.columns and "unit" in raw.columns:
        raw["unit_val"] = raw["unit"]
    if "unit_val" not in raw.columns:
        raw["unit_val"] = "قطعة"
    if "section_name" not in raw.columns:
        raw["section_name"] = "عام"
    if "notes" not in raw.columns:
        raw["notes"] = ""
    if "production_status" not in raw.columns:
        raw["production_status"] = "بانتظار الإنتاج"
    if "last_updated_by" not in raw.columns:
        raw["last_updated_by"] = ""
    if "last_updated_at" not in raw.columns:
        raw["last_updated_at"] = None
    if "shift_name" not in raw.columns:
        raw["shift_name"] = None
    if "shift_cycle_key" not in raw.columns:
        raw["shift_cycle_key"] = None
    if "recommendation_log" not in raw.columns:
        raw["recommendation_log"] = None
    if "morning_baker_id" not in raw.columns:
        raw["morning_baker_id"] = raw["assigned_baker_id"] if "assigned_baker_id" in raw.columns else None
    if "evening_baker_id" not in raw.columns:
        raw["evening_baker_id"] = raw["assigned_baker_id"] if "assigned_baker_id" in raw.columns else None
    if "night_baker_id" not in raw.columns:
        raw["night_baker_id"] = raw["assigned_baker_id"] if "assigned_baker_id" in raw.columns else None
    return raw


def batch_upsert_product_quantities(
    updates: list[dict],
    *,
    assigned_baker_id: str | None = None,
    managed_section_names: list[str] | None = None,
    preserve_request_qty_from_db: bool = False,
    updated_by: str | None = None,
) -> tuple[bool, BaseException | None]:
    """
    يحدّث current_qty و request_qty لعدة سلع في طلب واحد (upsert على id) مع supabase_with_retry.
    يجلب الصفوف كاملة select(\"*\") ثم يدمج الكميات حتى لا يُرسل PostgREST null لأعمدة NOT NULL
    مثل name و unit_val.
    عند تمرير assigned_baker_id يُقتصر الجلب على سلع المستخدم في عمود خلفة الشفت الحالي.
    عند تمرير managed_section_names (غير فارغ) يُقتصر الجلب على سلع تقع ضمن هذه الأقسام (مسؤول القسم).
    عند preserve_request_qty_from_db=True يُبقى request_qty كما في قاعدة البيانات (لشاشة الخلفة — منع تلاعب الطلب).
    """
    if not updates:
        return True, None
    allowed_product_columns = {
        "id",
        "name",
        "unit_val",
        "section_name",
        "assigned_baker_id",
        "morning_baker_id",
        "evening_baker_id",
        "night_baker_id",
        "current_qty",
        "request_qty",
        "created_at",
        "notes",
        "production_status",
        "last_updated_by",
        "last_updated_at",
        "shift_name",
        "shift_cycle_key",
        "recommendation_log",
    }
    by_id: dict[str, dict] = {}
    for u in updates:
        uid = str(u["id"])
        by_id[uid] = {
            "unit_val": str(u.get("unit_val", "") or "").strip(),
            "current_qty": u["current_qty"],
            "request_qty": u["request_qty"],
            "notes": str(u.get("notes", "") or "").strip(),
            "production_status": str(u.get("production_status", "بانتظار الإنتاج") or "بانتظار الإنتاج").strip(),
        }
    ids = list(by_id.keys())
    cid = str(assigned_baker_id).strip() if assigned_baker_id else None
    shift_col = current_shift_baker_column()
    sections = [str(s).strip() for s in (managed_section_names or []) if str(s).strip()]

    def _fetch_full():
        q = supabase_client.table("products").select("*").in_("id", ids)
        if cid:
            q = q.eq(shift_col, cid)
        if sections:
            q = q.in_("section_name", sections)
        return q.execute()

    ok_f, fres, exc_f = supabase_with_retry(_fetch_full)
    if not ok_f:
        return False, exc_f
    rows_data = fres.data or []
    got = {str(r["id"]) for r in rows_data if r.get("id") is not None}
    if got != set(ids):
        if cid or sections:
            return False, PermissionError(
                "تعذر التحقق من ملكية السجلات أو أن السلعة خارج قسمك المصرّح. أعد تحميل الصفحة والمحاولة."
            )
        return False, RuntimeError("لم يُعثر على بعض السلع المحدّثة. أعد تحميل الصفحة.")

    merged: list[dict] = []
    actor = str(updated_by or st.session_state.get("username") or "").strip() or "مستخدم غير معروف"
    ts_baghdad = baghdad_iso_now()
    si = baghdad_shift_cycle_info()
    grace_period_seconds = 600
    now_dt = parse_to_baghdad_dt(ts_baghdad)
    for row in rows_data:
        rid = str(row["id"])
        patch = by_id[rid]
        full = dict(row)
        prev_cycle_key = str(row.get("shift_cycle_key") or "").strip()
        new_cycle_key = str(si["cycle_key"] or "").strip()
        shift_changed = bool(prev_cycle_key) and prev_cycle_key != new_cycle_key
        unit_snapshot = (
            patch.get("unit_val", row.get("unit_val", row.get("unit", "قطعة")))
            or row.get("unit_val", row.get("unit", "قطعة"))
        )
        full["unit_val"] = unit_snapshot
        full["current_qty"] = patch.get("current_qty", row.get("current_qty", 0))
        if preserve_request_qty_from_db:
            full["request_qty"] = int(float(row.get("request_qty") or 0))
        else:
            full["request_qty"] = patch.get("request_qty", row.get("request_qty", 0))
        full["notes"] = str(patch.get("notes", row.get("notes", "") or "")).strip()
        full["production_status"] = str(
            patch.get("production_status", row.get("production_status", "بانتظار الإنتاج") or "بانتظار الإنتاج")
        ).strip()
        if shift_changed:
            # عزل بيانات الشفت: لا ترحيل للملاحظات/الحالة/السجل عند الانتقال لدورة جديدة.
            full["notes"] = ""
            full["production_status"] = "بانتظار الإنتاج"
        full["last_updated_by"] = actor
        full["last_updated_at"] = ts_baghdad
        full["shift_name"] = si["shift_name"]
        full["shift_cycle_key"] = si["cycle_key"]
        log = [] if shift_changed else list(coerce_recommendation_log(row.get("recommendation_log")))
        old_req = int(float(row.get("request_qty") or 0))
        new_req = int(float(full.get("request_qty") or 0))
        old_cur = int(float(row.get("current_qty") or 0))
        new_cur = int(float(full.get("current_qty") or 0))
        old_unit = str(row.get("unit_val") or row.get("unit") or "قطعة").strip() or "قطعة"
        new_unit = str(full.get("unit_val") or full.get("unit") or old_unit).strip() or old_unit
        old_notes = str(row.get("notes") or "").strip()
        new_notes = str(full.get("notes") or "").strip()
        snapshot_changed = (
            new_req != old_req
            or new_cur != old_cur
            or new_unit != old_unit
            or new_notes != old_notes
        )
        if (not shift_changed) and (not preserve_request_qty_from_db) and snapshot_changed:
            # توحيد الترتيب زمنياً قبل اختيار "آخر عنصر" لضمان سلوك فترة السماح بدقة.
            log.sort(key=lambda e: recommendation_log_entry_dt(e).timestamp() if recommendation_log_entry_dt(e) else 0.0)
            last_entry_idx: int | None = len(log) - 1 if log else None
            last_entry_dt: datetime | None = recommendation_log_entry_dt(log[-1]) if log else None

            within_grace_period = (
                now_dt is not None
                and last_entry_dt is not None
                and 0 <= (now_dt - last_entry_dt).total_seconds() <= grace_period_seconds
            )

            if within_grace_period and last_entry_idx is not None:
                # خلال فترة السماح: نعدّل آخر طلب قائم فقط، ولا ننشئ طلباً إضافياً جديداً نهائياً.
                last_entry = dict(log[last_entry_idx])
                unit_snapshot = str(full.get("unit_val") or full.get("unit") or row.get("unit_val") or row.get("unit") or "قطعة").strip() or "قطعة"
                last_entry["request_qty"] = new_req
                last_entry["current_qty"] = int(float(full.get("current_qty") or 0))
                last_entry["unit_val"] = unit_snapshot
                last_entry["unit"] = unit_snapshot
                last_entry["notes"] = str(full.get("notes") or "").strip()
                # لا نغيّر وقت أول طلب حتى لا ينقلب ترتيب "الأساسي/الإضافي" في واجهة الخلفة.
                log[last_entry_idx] = last_entry
            else:
                unit_snapshot = str(full.get("unit_val") or full.get("unit") or row.get("unit_val") or row.get("unit") or "قطعة").strip() or "قطعة"
                log.append(
                    {
                        "entry_id": str(uuid.uuid4()),
                        "at": ts_baghdad,
                        "timestamp": ts_baghdad,
                        "by": actor,
                        "kind": "request_change",
                        "request_qty": new_req,
                        "current_qty": int(float(full.get("current_qty") or 0)),
                        "previous_request_qty": old_req,
                        "unit_val": unit_snapshot,
                        "unit": unit_snapshot,
                        # لقطة مستقلة للملاحظة وقت إنشاء الطلب الإضافي (بدون توريث لاحق).
                        "notes": str(full.get("notes") or "").strip(),
                    }
                )
        # حفظ JSONB كمصفوفة dict نظيفة وقابلة للتسلسل بشكل صريح.
        clean_log = [dict(e) for e in log[-200:] if isinstance(e, dict)]
        full["recommendation_log"] = json.loads(json.dumps(clean_log, ensure_ascii=False))
        # أرسل فقط أعمدة products المعتمدة لتفادي أخطاء schema cache مثل PGRST204 (unit مقابل unit_val).
        sanitized_full = {k: v for k, v in full.items() if k in allowed_product_columns}
        sanitized_full.pop("unit", None)
        merged.append(sanitized_full)

    def _upsert():
        return supabase_client.table("products").upsert(merged, on_conflict="id").execute()

    ok_u, _, exc_u = supabase_with_retry(_upsert)
    if not ok_u:
        return False, exc_u

    def _verify_after_upsert():
        return supabase_client.table("products").select("id,request_qty,current_qty").in_("id", ids).execute()

    ok_v, vres, exc_v = supabase_with_retry(_verify_after_upsert)
    if not ok_v or vres is None:
        return False, exc_v or RuntimeError("تم الحفظ لكن تعذر التحقق من تحديث السجلات.")

    verified_rows = vres.data or []
    verified_ids = {str(r.get("id")) for r in verified_rows if r.get("id") is not None}
    expected_ids = set(ids)
    if verified_ids != expected_ids:
        return False, RuntimeError("اكتمل الحفظ جزئياً: بعض السلع لم تُحدَّث في قاعدة البيانات.")

    if not preserve_request_qty_from_db:
        db_request_by_id = {
            str(r.get("id")): int(float(r.get("request_qty") or 0))
            for r in verified_rows
            if r.get("id") is not None
        }
        expected_request_by_id = {pid: int(float(by_id[pid].get("request_qty") or 0)) for pid in ids}
        mismatched = [
            pid
            for pid in ids
            if int(db_request_by_id.get(pid, 0)) != int(expected_request_by_id.get(pid, 0))
        ]
        if mismatched:
            return False, RuntimeError("تعذر تأكيد تحديث الكمية المطلوبة لبعض السلع بعد الحفظ.")
    db_current_by_id = {
        str(r.get("id")): int(float(r.get("current_qty") or 0))
        for r in verified_rows
        if r.get("id") is not None
    }
    expected_current_by_id = {pid: int(float(by_id[pid].get("current_qty") or 0)) for pid in ids}
    mismatched_current = [
        pid
        for pid in ids
        if int(db_current_by_id.get(pid, 0)) != int(expected_current_by_id.get(pid, 0))
    ]
    if mismatched_current:
        return False, RuntimeError("تعذر تأكيد تحديث الكمية المتوفرة لبعض السلع بعد الحفظ.")

    invalidate_products_cache_after_mutation()
    return True, None


def update_single_product_status(
    product_id: str,
    production_status: str,
    *,
    assigned_baker_id: str | None = None,
    updated_by: str | None = None,
) -> tuple[bool, BaseException | None]:
    """
    تحديث فوري لحالة الإنتاج لسلعة واحدة مع ضبط آخر تحديث والشفت الحالي.
    يُستخدم عند تغيير الخلفة لحالة الإنتاج بدون انتظار زر الحفظ العام.
    """
    pid = str(product_id or "").strip()
    if not pid:
        return False, ValueError("missing product id")

    status_val = str(production_status or "").strip() or "بانتظار الإنتاج"
    actor = str(updated_by or st.session_state.get("username") or "").strip() or "مستخدم غير معروف"
    si = baghdad_shift_cycle_info()
    cid = str(assigned_baker_id).strip() if assigned_baker_id else None
    shift_col = current_shift_baker_column()

    def _update():
        q = (
            supabase_client.table("products")
            .update(
                {
                    "production_status": status_val,
                    "last_updated_by": actor,
                    "last_updated_at": baghdad_iso_now(),
                    "shift_name": si["shift_name"],
                    "shift_cycle_key": si["cycle_key"],
                }
            )
            .eq("id", pid)
        )
        if cid:
            q = q.eq(shift_col, cid)
        return q.execute()

    ok_u, _, exc_u = supabase_with_retry(_update)
    if ok_u:
        invalidate_products_cache_after_mutation()
    return ok_u, exc_u


def update_recommendation_segment_production_status(
    product_id: str,
    segment_entry: dict,
    production_status: str,
    *,
    assigned_baker_id: str | None = None,
    updated_by: str | None = None,
) -> tuple[bool, BaseException | None]:
    """تحديث حالة إنتاج مستقلة لسطر زيادة في recommendation_log."""
    pid = str(product_id or "").strip()
    if not pid:
        return False, ValueError("missing product id")
    mk = recommendation_log_entry_match_key(segment_entry)
    status_val = str(production_status or "").strip() or "بانتظار الإنتاج"
    actor = str(updated_by or st.session_state.get("username") or "").strip() or "مستخدم غير معروف"
    si = baghdad_shift_cycle_info()
    cid = str(assigned_baker_id).strip() if assigned_baker_id else None
    shift_col = current_shift_baker_column()

    def _fetch_one():
        q = supabase_client.table("products").select("*").eq("id", pid)
        if cid:
            q = q.eq(shift_col, cid)
        return q.limit(1).execute()

    ok_f, fres, exc_f = supabase_with_retry(_fetch_one)
    rows = (fres.data if fres else None) or []
    if not ok_f or not rows:
        return False, exc_f or RuntimeError("تعذر جلب السلعة أو التحقق من الربط")
    row = rows[0]
    log = list(coerce_recommendation_log(row.get("recommendation_log")))
    found = False
    for e in log:
        if recommendation_log_entry_match_key(e) == mk:
            e["segment_production_status"] = status_val
            found = True
            break
    if not found:
        return False, ValueError("لم يُعثر على سجل التوصية المطلوب")

    def _upd():
        return (
            supabase_client.table("products")
            .update(
                {
                    "recommendation_log": log[-200:],
                    "last_updated_by": actor,
                    "last_updated_at": baghdad_iso_now(),
                    "shift_name": si["shift_name"],
                    "shift_cycle_key": si["cycle_key"],
                }
            )
            .eq("id", pid)
            .execute()
        )

    ok_u, _, exc_u = supabase_with_retry(_upd)
    if ok_u:
        invalidate_products_cache_after_mutation()
    return ok_u, exc_u


def warn_network_and_stop():
    st.warning("يرجى التحقق من اتصال الإنترنت. تعذر الاتصال بـ Supabase بعد عدة محاولات.")
    st.stop()


st.markdown(
    """
    <style>
    /* خطوط النظام فقط — لا @font-face ولا روابط خارجية (تجنّب أخطاء تحميل مثل HTTP 404) */
    html, body, [data-testid="stAppViewContainer"], .main,
    [data-testid="stAppViewContainer"] .stMarkdown, [data-testid="stAppViewContainer"] [data-baseweb] {
      font-family: "Segoe UI", "Tahoma", "Arial", "Dubai", "Simplified Arabic", "Traditional Arabic", sans-serif !important;
    }
    html, body, [data-testid="stAppViewContainer"], .main {direction: rtl !important; text-align: right !important;}
    .block-container {padding-top: 1rem;}
    .stTabs [data-baseweb="tab-list"] {gap: 8px; flex-wrap: wrap;}
    /* تحسين قراءة جداول Streamlit على الشاشات الصغيرة */
    [data-testid="stDataFrame"] [role="gridcell"],
    [data-testid="stDataFrame"] [role="columnheader"],
    [data-testid="stDataEditor"] [role="gridcell"],
    [data-testid="stDataEditor"] [role="columnheader"] {
      font-size: 13px !important;
      white-space: nowrap !important;
    }
    /* قفل هيكل الأعمدة: إلغاء سحب/ترتيب/تحجيم عناوين الأعمدة */
    [data-testid="stDataEditor"] [role="columnheader"],
    [data-testid="stDataFrame"] [role="columnheader"] {
      pointer-events: none !important;
      cursor: default !important;
      user-select: none !important;
    }
    [data-testid="stDataEditor"] [role="columnheader"] *,
    [data-testid="stDataFrame"] [role="columnheader"] * {
      pointer-events: none !important;
      cursor: default !important;
    }
    /* إخفاء مقابض التحجيم/السحب إن ظهرت ضمن مكوّن الجدول */
    [data-testid="stDataEditor"] [class*="resize"],
    [data-testid="stDataEditor"] [class*="draggable"],
    [data-testid="stDataFrame"] [class*="resize"],
    [data-testid="stDataFrame"] [class*="draggable"] {
      display: none !important;
      pointer-events: none !important;
    }
    @media (max-width: 900px){ .block-container {padding-left: 0.6rem; padding-right: 0.6rem;} }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <script>
    (function () {
      const lockHeaders = () => {
        const roots = document.querySelectorAll('[data-testid="stDataEditor"], [data-testid="stDataFrame"]');
        roots.forEach((root) => {
          root.querySelectorAll('[role="columnheader"]').forEach((header) => {
            header.setAttribute("draggable", "false");
            header.style.pointerEvents = "none";
            header.style.userSelect = "none";
            header.querySelectorAll("*").forEach((el) => {
              el.setAttribute("draggable", "false");
              el.style.pointerEvents = "none";
            });
          });
        });
      };
      lockHeaders();
      const observer = new MutationObserver(lockHeaders);
      observer.observe(document.body, { childList: true, subtree: true });
    })();
    </script>
    """,
    unsafe_allow_html=True,
)


def show_schema_help_and_stop(err: Exception):
    st.error(f"خطأ في بنية قاعدة البيانات (Schema): {err}")
    st.markdown(
        """
### خطوات إصلاح `PGRST205`
1) افتح مشروعك في **Supabase Dashboard**.  
2) اذهب إلى **SQL Editor**.  
3) الصق محتوى ملف `supabase_schema.sql` (أو SQL الذي أرسلته أنت).  
4) نفّذ الاستعلام بالكامل ثم انتظر 5-10 ثوانٍ لتحديث schema cache.  
5) أعد تشغيل تطبيق Streamlit.
        """
    )
    st.stop()


def ensure_required_schema():
    required = ["app_users", "products", "audit_archive", "notifications", "system_settings"]
    for table_name in required:
        ok, _, exc = supabase_with_retry(
            lambda tn=table_name: supabase_client.table(tn).select("*").limit(1).execute()
        )
        if ok:
            continue
        if exc is not None and is_network_transport_error(exc):
            warn_network_and_stop()
        if exc is not None and is_schema_missing_error(exc):
            show_schema_help_and_stop(exc)
        st.error(f"تعذر التحقق من الجداول: {exc}")
        st.stop()


def ensure_seed_users():
    ok, res, exc = supabase_with_retry(
        lambda: supabase_client.table("app_users").select("id,username").execute()
    )
    if not ok or res is None:
        if exc is not None and is_network_transport_error(exc):
            warn_network_and_stop()
        if exc is not None and is_schema_missing_error(exc):
            show_schema_help_and_stop(exc)
        st.error(f"تعذر تهيئة المستخدمين: {exc}")
        st.stop()

    rows = res.data or []

    normalized_existing = {normalize_username(r.get("username", "")) for r in rows}
    seed = [
        {
            "username": "باب الاغا",
            "password_text": "19488491",
            "role": "Admin",
            "managed_sections": [],
            "permissions": {
                "can_add_users": True,
                "can_edit_products": True,
                "can_view_archive": True,
                "can_view_home": True,
                "can_view_baker_screen": True,
                "can_view_inventory": True,
                "can_request_production": True,
                "can_view_preview": True,
                "can_view_notifications": True,
            },
        },
        {
            "username": "المخزن",
            "password_text": "11220099",
            "role": "DeptManager",
            "managed_sections": [],
            "permissions": {
                "can_add_users": False,
                "can_edit_products": False,
                "can_view_archive": False,
                "can_view_home": True,
                "can_view_baker_screen": False,
                "can_view_inventory": True,
                "can_request_production": True,
                "can_view_preview": True,
                "can_view_notifications": True,
            },
        },
    ]
    to_insert = [u for u in seed if normalize_username(u["username"]) not in normalized_existing]
    if to_insert:
        ok_ins, _, exc_ins = supabase_with_retry(
            lambda rows=to_insert: supabase_client.table("app_users").insert(rows).execute()
        )
        if not ok_ins:
            if exc_ins is not None and is_network_transport_error(exc_ins):
                warn_network_and_stop()
            st.error(f"تعذر إنشاء الحسابات الافتراضية: {exc_ins}")
            st.stop()


def migrate_legacy_warehouse_roles():
    """تحويل دور Warehouse القديم إلى DeptManager مع صلاحيات مسؤول القسم."""
    ok, res, exc = supabase_with_retry(
        lambda: supabase_client.table("app_users").select("id,role,permissions").execute()
    )
    if not ok or res is None:
        if exc is not None and is_network_transport_error(exc):
            warn_network_and_stop()
        return
    rows = res.data or []
    patch = {
        "can_add_users": False,
        "can_edit_products": False,
        "can_view_archive": False,
        "can_view_home": True,
        "can_view_baker_screen": False,
        "can_view_inventory": True,
        "can_request_production": True,
        "can_view_preview": True,
        "can_view_notifications": True,
    }
    for r in rows:
        if str(r.get("role", "")).strip() != "Warehouse":
            continue
        merged = {**(r.get("permissions") or {}), **patch}
        ok_u, _, exc_u = supabase_with_retry(
            lambda rid=r["id"], m=merged: supabase_client.table("app_users")
            .update({"role": "DeptManager", "permissions": m})
            .eq("id", rid)
            .execute()
        )
        if not ok_u:
            if exc_u is not None and is_network_transport_error(exc_u):
                warn_network_and_stop()
            if exc_u is not None:
                st.error(f"تعذر ترحيل أدوار المستخدمين (Warehouse): {exc_u}")
                st.stop()


def fetch_user(username_input: str) -> tuple[dict | None, bool]:
    """يجلب المستخدمين مع إعادة المحاولة عند ضعف الشبكة.

    يعيد (مستخدم أو None، نجاح_الاستعلام). إذا كان نجاح_الاستعلام False فلا يُفسَّر الغياب على أنه «غير موجود».
    """
    normalized = normalize_username(username_input)
    ok, res, exc = supabase_with_retry(lambda: supabase_client.table("app_users").select("*").execute())
    if not ok or res is None:
        if exc is not None and is_network_transport_error(exc):
            st.warning("يرجى التحقق من اتصال الإنترنت. تعذر التحقق من بيانات الدخول.")
        elif exc is not None and is_schema_missing_error(exc):
            show_schema_help_and_stop(exc)
        elif exc is not None:
            st.error(f"تعذر جلب المستخدمين: {exc}")
        return None, False
    rows = res.data or []
    for user in rows:
        if normalize_username(user.get("username", "")) == normalized:
            return user, True
    return None, True


def get_login_usernames() -> tuple[list[str], bool]:
    """يجلب أسماء المستخدمين لشاشة الدخول مع إخفاء مستخدم المخزن في الواجهة فقط."""
    ok, res, exc = supabase_with_retry(
        lambda: supabase_client.table("app_users").select("username").order("username").execute()
    )
    if not ok or res is None:
        if exc is not None and is_network_transport_error(exc):
            st.warning("يرجى التحقق من اتصال الإنترنت. تعذر تحميل قائمة المستخدمين.")
        elif exc is not None and is_schema_missing_error(exc):
            show_schema_help_and_stop(exc)
        elif exc is not None:
            st.error(f"تعذر جلب قائمة المستخدمين: {exc}")
        return [], False

    rows = res.data or []
    usernames = [
        str(row.get("username", "")).strip()
        for row in rows
        if str(row.get("username", "")).strip()
    ]
    hidden_name = "المخزن"
    filtered = [u for u in usernames if normalize_username(u) != normalize_username(hidden_name)]
    return filtered, True


def user_has(permission: str) -> bool:
    if st.session_state.get("role", "").lower() == "admin":
        return True
    perms = st.session_state.get("permissions", {}) or {}
    if bool(perms.get(permission, False)):
        return True
    # توافق مع مفاتيح قديمة في JSON المخزّن
    legacy = {
        "can_view_dashboard": (
            "can_view_home",
            "can_view_baker_screen",
            "can_view_inventory",
            "can_view_preview",
        ),
        "can_submit_inventory": ("can_view_inventory",),
    }
    for old_key, new_keys in legacy.items():
        if permission in new_keys and bool(perms.get(old_key, False)):
            return True
    return False


def normalize_permissions_for_session(perms: dict | None, role: str) -> dict:
    """يدمج مفاتيح الصلاحيات القديمة مع النموذج الجديد عند تسجيل الدخول."""
    p = dict(perms or {})
    r = (role or "").strip().lower()
    if p.get("can_view_dashboard"):
        if r == "baker":
            p.setdefault("can_view_baker_screen", True)
            p.setdefault("can_view_notifications", True)
            p.setdefault("can_view_home", True)
        elif r == "deptmanager":
            p.setdefault("can_view_home", True)
            p.setdefault("can_view_inventory", True)
            p.setdefault("can_view_preview", True)
            p.setdefault("can_view_notifications", True)
            if p.get("can_view_inventory"):
                p.setdefault("can_request_production", True)
        else:
            p.setdefault("can_view_home", True)
            p.setdefault("can_view_notifications", True)
    if p.get("can_submit_inventory") or p.get("can_request_production"):
        if r == "deptmanager":
            p.setdefault("can_view_inventory", True)
    return p


def build_sidebar_menu_labels() -> list[str]:
    """قائمة جانبية ديناميكية حسب JSON الصلاحيات؛ المدير يرى كل الصفحات."""
    if (st.session_state.get("role") or "").strip().lower() == "admin":
        return [
            "🏠 الرئيسية",
            "📦 الجرد",
            "🖼️ المعاينة",
            "👨‍🍳 شاشة الخلفة",
            "⚙️ إعدادات النظام",
            "🔔 الإشعارات",
        ]
    out: list[str] = []
    role_lc = (st.session_state.get("role") or "").strip().lower()
    if user_has("can_view_home"):
        out.append("🏠 الرئيسية")
    if user_has("can_view_inventory") and role_lc != "baker":
        out.append("📦 الجرد")
    if user_has("can_view_preview"):
        out.append("🖼️ المعاينة")
    if user_has("can_view_baker_screen"):
        out.append("👨‍🍳 شاشة الخلفة")
    if user_has("can_view_notifications"):
        out.append("🔔 الإشعارات")
    return out


def push_notification(message: str, target_role: str = None):
    ts = baghdad_iso_now()
    payload = {
        "message": message,
        "target_role": target_role,
        "read_by_usernames": [],
        "created_at": ts,
    }
    ok, _, exc = supabase_with_retry(lambda p=payload: supabase_client.table("notifications").insert(p).execute())
    if ok:
        return
    # Fallback لقاعدة قديمة بلا عمود read_by_usernames
    ok2, _, exc2 = supabase_with_retry(
        lambda: supabase_client.table("notifications")
        .insert({"message": message, "target_role": target_role, "created_at": ts})
        .execute()
    )
    if not ok2 and (exc2 or exc) is not None:
        if is_network_transport_error(exc2 or exc):
            if not st.session_state.get("_warned_notification_push"):
                st.session_state["_warned_notification_push"] = True
                st.warning("يرجى التحقق من اتصال الإنترنت. تعذر إرسال الإشعار.")


def _read_by_usernames(notification: dict) -> list[str]:
    value = notification.get("read_by_usernames")
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return []


def _is_read_by_current_user(notification: dict, current_username: str) -> bool:
    if not current_username:
        return False
    return current_username in _read_by_usernames(notification)


def format_notification_time(value) -> str:
    return format_baghdad_time(value)


def notifications_page():
    role = st.session_state.get("role", "")
    current_username = st.session_state.get("username", "")

    def _fetch_notifications():
        q = supabase_client.table("notifications").select("*").order("created_at", desc=True).limit(20)
        if role:
            q = q.or_(f"target_role.is.null,target_role.eq.{role}")
        return q.execute()

    ok, res, exc = supabase_with_retry(_fetch_notifications)
    if not ok or res is None:
        if exc is not None and is_network_transport_error(exc):
            st.warning("يرجى التحقق من اتصال الإنترنت. تعذر تحميل الإشعارات.")
        elif exc is not None and is_schema_missing_error(exc):
            show_schema_help_and_stop(exc)
        elif exc is not None:
            st.error(f"تعذر تحميل الإشعارات: {exc}")
        return
    rows = res.data or []
    unread = [r for r in rows if not _is_read_by_current_user(r, current_username)]

    st.header("🔔 الإشعارات")
    st.caption(f"عدد غير المقروء: {len(unread)}")
    if unread and st.button("تحديد الكل كمقروء", use_container_width=True):
        if not current_username:
            st.error("تعذر تحديد اسم المستخدم الحالي.")
            return
        for n in unread:
            current_readers = _read_by_usernames(n)
            if current_username not in current_readers:
                current_readers.append(current_username)
            ok_u, _, exc_u = supabase_with_retry(
                lambda nid=n["id"], readers=current_readers: supabase_client.table("notifications")
                .update({"read_by_usernames": readers})
                .eq("id", nid)
                .execute()
            )
            if not ok_u:
                if exc_u is not None and is_network_transport_error(exc_u):
                    st.warning("يرجى التحقق من اتصال الإنترنت. تعذر تحديث حالة الإشعارات.")
                    return
                if exc_u is not None and is_schema_missing_error(exc_u):
                    show_schema_help_and_stop(exc_u)
                st.error(f"تعذر تحديث الإشعار: {exc_u}")
                return
        st.rerun()

    if not rows:
        st.info("لا توجد إشعارات حالياً.")
        return

    for n in rows:
        created_at = format_notification_time(n.get("created_at"))
        role_tag = n.get("target_role") or "الكل"
        prefix = "🟡 غير مقروء" if not _is_read_by_current_user(n, current_username) else "✅ مقروء"
        st.info(f"{prefix} | الفئة: {role_tag} | الوقت: {created_at}\n\n{n.get('message', 'تنبيه')}")

def coerce_managed_sections_list(value) -> list[str]:
    """يحوّل قيمة managed_sections من Supabase/JSON إلى قائمة أسماء أقسام نظيفة."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            s = value.strip()
            return [s] if s else []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def session_managed_sections() -> list[str]:
    return coerce_managed_sections_list(st.session_state.get("managed_sections"))


def filter_products_for_session(df: pd.DataFrame) -> pd.DataFrame:
    """يضيّق الجرد/المعاينة: مسؤول القسم يرى أقسامه فقط؛ خلفة بصلاحية جرد ترى سلعها المسندة فقط."""
    if df.empty:
        return df
    role = (st.session_state.get("role") or "").strip().lower()
    if role == "admin":
        return df.copy()
    if role == "deptmanager":
        secs = session_managed_sections()
        if not secs:
            return df.copy()
        if "section_name" not in df.columns:
            return df.iloc[0:0].copy()
        return df[df["section_name"].astype(str).isin(set(secs))].copy()
    if role == "baker" and user_has("can_view_inventory"):
        uid = str(st.session_state.get("user_id") or "").strip()
        col = current_shift_baker_column()
        if not uid or col not in df.columns:
            return df.iloc[0:0].copy()
        return df[df[col].astype(str) == uid].copy()
    return df.copy()


def filter_deptmanager_current_shift_products(df: pd.DataFrame) -> pd.DataFrame:
    """نفس منطق شاشة الجرد لمسؤول القسم: الشفت الحالي (بغداد) + السجلات القديمة ذات النشاط بدون shift_cycle_key."""
    if df.empty:
        return df.copy()
    if (st.session_state.get("role") or "").strip().lower() != "deptmanager":
        return df.copy()
    if "shift_cycle_key" not in df.columns:
        return df.copy()
    _si_inv = baghdad_shift_cycle_info()
    cur_ck = _si_inv["cycle_key"]
    sk = df["shift_cycle_key"].apply(lambda x: str(x or "").strip())
    rqz = pd.to_numeric(df["request_qty"], errors="coerce").fillna(0)
    cqz = pd.to_numeric(df["current_qty"], errors="coerce").fillna(0)
    legacy_open = (sk == "") & ((rqz > 0) | (cqz > 0))
    return df[(sk == cur_ck) | legacy_open].copy()


def clear_inventory_session_widgets_for_ids(product_ids: list[str]) -> None:
    """إزالة مفاتيح st.number_input للجرد حتى تُعاد القراءة من قاعدة البيانات بعد الحفظ أو فتح نافذة جديدة."""
    for pid in product_ids:
        p = str(pid).strip()
        if not p:
            continue
        for prefix in ("inv_req_", "inv_cur_", "inv_notes_", "inv_unit_", "inv_uc_"):
            k = f"{prefix}{p}"
            if k in st.session_state:
                del st.session_state[k]


def clear_baker_dashboard_session_widgets_for_ids(product_ids: list[str]) -> None:
    """إزالة مفاتيح شاشة الخلفة/الموحدة حتى لا تبقى أرقام قديمة في الجلسة بعد إعادة فتح التعديل أو الحفظ."""
    for pid in product_ids:
        p = str(pid).strip()
        if not p:
            continue
        for prefix in ("bk_cur_", "bk_notes_", "bk_status_", "bk_req_", "bk_unit_", "bk_uc_"):
            k = f"{prefix}{p}"
            if k in st.session_state:
                del st.session_state[k]
        seg_pref = f"bk_seg_status_{p}_"
        for k in list(st.session_state.keys()):
            if isinstance(k, str) and k.startswith(seg_pref):
                del st.session_state[k]


def load_products() -> pd.DataFrame:
    ok, res, exc = supabase_with_retry(lambda: supabase_client.table("products").select("*").order("name").execute())
    if not ok or res is None:
        if exc is not None and is_network_transport_error(exc):
            if not st.session_state.get("_warned_products_load"):
                st.session_state["_warned_products_load"] = True
                st.warning("يرجى التحقق من اتصال الإنترنت. تعذر جلب قائمة السلع.")
        elif exc is not None and is_schema_missing_error(exc):
            show_schema_help_and_stop(exc)
        elif exc is not None:
            st.error(f"تعذر جلب السلع: {exc}")
        return pd.DataFrame(
            columns=[
                "id", "name", "unit_val", "section_name", "assigned_baker_id", "morning_baker_id", "evening_baker_id", "night_baker_id", "current_qty", "request_qty",
                "notes", "production_status", "last_updated_by", "last_updated_at", "shift_name", "shift_cycle_key",
                "recommendation_log",
            ]
        )
    raw = pd.DataFrame(res.data or [])
    if raw.empty:
        return pd.DataFrame(
            columns=[
                "id", "name", "unit_val", "section_name", "assigned_baker_id", "morning_baker_id", "evening_baker_id", "night_baker_id", "current_qty", "request_qty",
                "notes", "production_status", "last_updated_by", "last_updated_at", "shift_name", "shift_cycle_key",
                "recommendation_log",
            ]
        )
    if "current_qty" not in raw.columns:
        raw["current_qty"] = 0
    if "request_qty" not in raw.columns:
        raw["request_qty"] = 0
    # توافق خلفي: بعض البيئات قد تستخدم unit بدلاً من unit_val.
    if "unit_val" not in raw.columns and "unit" in raw.columns:
        raw["unit_val"] = raw["unit"]
    if "unit_val" not in raw.columns:
        raw["unit_val"] = "قطعة"
    if "section_name" not in raw.columns:
        raw["section_name"] = "عام"
    if "notes" not in raw.columns:
        raw["notes"] = ""
    if "production_status" not in raw.columns:
        raw["production_status"] = "بانتظار الإنتاج"
    if "last_updated_by" not in raw.columns:
        raw["last_updated_by"] = ""
    if "last_updated_at" not in raw.columns:
        raw["last_updated_at"] = None
    if "shift_name" not in raw.columns:
        raw["shift_name"] = None
    if "shift_cycle_key" not in raw.columns:
        raw["shift_cycle_key"] = None
    if "recommendation_log" not in raw.columns:
        raw["recommendation_log"] = None
    if "morning_baker_id" not in raw.columns:
        raw["morning_baker_id"] = raw["assigned_baker_id"] if "assigned_baker_id" in raw.columns else None
    if "evening_baker_id" not in raw.columns:
        raw["evening_baker_id"] = raw["assigned_baker_id"] if "assigned_baker_id" in raw.columns else None
    if "night_baker_id" not in raw.columns:
        raw["night_baker_id"] = raw["assigned_baker_id"] if "assigned_baker_id" in raw.columns else None
    return raw


def get_users_for_assign():
    ok, res, exc = supabase_with_retry(
        lambda: supabase_client.table("app_users").select("id,username,role").order("username").execute()
    )
    if not ok or res is None:
        if exc is not None and is_network_transport_error(exc) and not st.session_state.get("_warned_users_assign"):
            st.session_state["_warned_users_assign"] = True
            st.warning("يرجى التحقق من اتصال الإنترنت. تعذر جلب قائمة المستخدمين للربط.")
        return []
    return res.data or []


def cycle_key_for_baghdad_6am(dt: datetime) -> str:
    pivot = dt.replace(hour=6, minute=0, second=0, microsecond=0)
    if dt < pivot:
        pivot = pivot - timedelta(days=1)
    return pivot.strftime("%Y-%m-%d-06")


def insert_archive_rows_with_compat(rows: list[dict]) -> tuple[bool, Exception | None]:
    """إدراج سجلات الأرشيف مع توافق رجعي إذا لم تُطبَّق أعمدة اللقطة بعد."""
    ok_a, _, exc_a = supabase_with_retry(
        lambda payload=rows: supabase_client.table("audit_archive").insert(payload).execute()
    )
    if ok_a:
        return True, None
    err_txt = str(exc_a or "").lower()
    needs_legacy_fallback = any(
        key in err_txt
        for key in (
            "product_name",
            "section_name",
            "unit_val",
            "production_status",
            "last_updated_by",
            "last_updated_at",
        )
    )
    if not needs_legacy_fallback:
        return False, exc_a
    keep_keys = {
        "product_id",
        "archived_qty",
        "archived_request_qty",
        "archive_date",
        "shift_name",
        "shift_cycle_key",
    }
    legacy_rows = [{k: v for k, v in r.items() if k in keep_keys} for r in rows]
    ok_b, _, exc_b = supabase_with_retry(
        lambda payload=legacy_rows: supabase_client.table("audit_archive").insert(payload).execute()
    )
    if ok_b:
        return True, None
    return False, exc_b


def ensure_archive_cycle():
    # توافق رجعي: الأرشفة التلقائية المعتمدة أصبحت على حدود الشفت (بتوقيت بغداد) فقط.
    ensure_shift_boundary()


def _shift_suffix_from_cycle_key(cycle_key: str) -> str:
    parts = str(cycle_key or "").rsplit("-", 1)
    return parts[-1] if parts else ""


def ensure_shift_boundary():
    """
    عند بداية شفت جديد (توقيت بغداد): أرشفة الكميات النشطة ثم تصفيرها
    مع الإبقاء على السجل في audit_archive وتحديث shift_name للأرشفة.
    """
    now = get_baghdad_now()
    cur = baghdad_shift_cycle_info(now)
    ck = cur["cycle_key"]
    ok_s, sres, exc_s = supabase_with_retry(
        lambda: supabase_client.table("system_settings").select("*").eq("key", "last_shift_cycle_key").limit(1).execute()
    )
    if not ok_s or sres is None:
        if exc_s is not None and is_network_transport_error(exc_s):
            return
        if exc_s is not None and is_schema_missing_error(exc_s):
            show_schema_help_and_stop(exc_s)
        return

    s = sres.data or []
    last = (s[0] if s else {}).get("value")
    if last == ck:
        return

    closed_shift_label = _shift_suffix_from_cycle_key(last) if last else ""

    if last is not None:
        ok_p, pres, exc_p = supabase_with_retry(
            lambda: supabase_client.table("products")
            .select("id,name,section_name,unit_val,current_qty,request_qty,notes,production_status,last_updated_by,last_updated_at,shift_name,shift_cycle_key")
            .execute()
        )
        if not ok_p or pres is None:
            if exc_p is not None and is_network_transport_error(exc_p):
                return
            return

        products = pres.data or []
        archive_rows = []
        for p in products:
            q1 = int(p.get("current_qty") or 0)
            q2 = int(p.get("request_qty") or 0)
            if q1 <= 0 and q2 <= 0:
                continue
            row_shift_name = (
                closed_shift_label
                or str(p.get("shift_name") or "").strip()
                or _shift_suffix_from_cycle_key(str(p.get("shift_cycle_key") or "").strip())
                or cur.get("shift_name")
            )
            row = {
                "product_id": p["id"],
                "product_name": str(p.get("name") or "").strip() or None,
                "section_name": str(p.get("section_name") or "").strip() or None,
                "unit_val": str(p.get("unit_val") or "").strip() or None,
                "archived_qty": q1,
                "archived_request_qty": q2,
                "notes": str(p.get("notes") or "").strip() or None,
                "production_status": str(p.get("production_status") or "").strip() or None,
                "last_updated_by": str(p.get("last_updated_by") or "").strip() or None,
                "last_updated_at": p.get("last_updated_at"),
                "archive_date": now.isoformat(),
                "shift_name": row_shift_name or None,
                "shift_cycle_key": last,
            }
            archive_rows.append(row)
        if archive_rows:
            ok_a, exc_a = insert_archive_rows_with_compat(archive_rows)
            if not ok_a:
                if exc_a is not None and is_network_transport_error(exc_a):
                    return
                return
        ids = [p["id"] for p in products]
        if ids:
            ok_z, _, exc_z = supabase_with_retry(
                lambda pids=ids: supabase_client.table("products")
                .update(
                    {
                        "current_qty": 0,
                        "request_qty": 0,
                        "recommendation_log": [],
                        "shift_name": None,
                        "shift_cycle_key": None,
                    }
                )
                .in_("id", pids)
                .execute()
            )
            if not ok_z and exc_z is not None and is_network_transport_error(exc_z):
                return

    ok_u, _, exc_u = supabase_with_retry(
        lambda: supabase_client.table("system_settings")
        .upsert({"key": "last_shift_cycle_key", "value": ck, "updated_at": now.isoformat()}, on_conflict="key")
        .execute()
    )
    if not ok_u:
        if exc_u is not None and is_network_transport_error(exc_u):
            return
        return
    if last is not None:
        push_notification(
            f"انتهى الشفت ({closed_shift_label or 'سابق'}) — أُرشفت الكميات وبدأ شفت جديد ({cur['shift_name']}).",
            target_role=None,
        )


def filter_products_reports_activity(products_df: pd.DataFrame) -> pd.DataFrame:
    """تقارير المدير: سلع بها نشاط فقط (مطلوب > 0 أو متوفر > 0)."""
    if products_df is None or products_df.empty:
        return products_df if products_df is not None else pd.DataFrame()
    df = products_df.copy()
    rq = pd.to_numeric(df.get("request_qty", 0), errors="coerce").fillna(0)
    cq = pd.to_numeric(df.get("current_qty", 0), errors="coerce").fillna(0)
    mask = (rq > 0) | (cq > 0)
    return df.loc[mask].copy()


def build_export_df(products_df: pd.DataFrame) -> pd.DataFrame:
    """جدول التقارير/التصدير: صف مستقل لكل إدخال داخل recommendation_log (الأحدث أولاً)."""
    cols_order = [
        "السلعة",
        "الوحدة",
        "القسم",
        "المتوفر",
        "المطلوب",
        "الحالة",
        "المسؤول الحالي",
        "الشفت",
        "ملاحظات",
        "آخر تحديث",
    ]
    if products_df.empty:
        return pd.DataFrame(columns=cols_order)
    rows: list[dict] = []
    for _, row in products_df.iterrows():
        pname = str(row.get("name") or "").strip()
        sec = str(row.get("section_name") or "عام").strip() or "عام"
        try:
            cur_q = int(float(row.get("current_qty") or 0))
        except (TypeError, ValueError):
            cur_q = 0
        try:
            req_total = int(float(row.get("request_qty") or 0))
        except (TypeError, ValueError):
            req_total = 0
        notes = str(row.get("notes") or "").strip()
        actor = str(row.get("last_updated_by") or "").strip() or "—"
        prod_status = str(row.get("production_status") or "بانتظار الإنتاج").strip() or "بانتظار الإنتاج"
        if prod_status not in PRODUCTION_STATUS_OPTIONS:
            prod_status = PRODUCTION_STATUS_OPTIONS[0]
        if "shift_name" in row.index and row.get("shift_name") is not None and str(row.get("shift_name")).strip():
            shift_cell = str(row.get("shift_name")).strip()
        else:
            shift_cell = "—"
        row_cycle_key = str(row.get("shift_cycle_key") or "").strip()
        if not row_cycle_key:
            row_cycle_key = baghdad_shift_cycle_info()["cycle_key"]
        raw_log = filter_recommendation_log_for_cycle(row.get("recommendation_log"), row_cycle_key)
        entries = [dict(e) for e in coerce_recommendation_log(raw_log) if not _is_ghost_ui_log_entry(e)]
        entries.sort(
            key=lambda e: recommendation_log_entry_dt(e).timestamp() if recommendation_log_entry_dt(e) else 0.0,
            reverse=True,
        )
        if not entries:
            rows.append(
                {
                    "_sort_ts": 0.0,
                    "السلعة": pname,
                    "الوحدة": str(row.get("unit_val") or "قطعة").strip() or "قطعة",
                    "القسم": sec,
                    "المتوفر": cur_q,
                    "المطلوب": req_total,
                    "الحالة": prod_status,
                    "المسؤول الحالي": actor,
                    "الشفت": shift_cell,
                    "ملاحظات": notes or "—",
                    "آخر تحديث": format_baghdad_time(row.get("last_updated_at")),
                }
            )
            continue

        for e in entries:
            ent_dt = parse_to_baghdad_dt(e.get("at"))
            ent_ts = ent_dt.timestamp() if ent_dt is not None else 0.0
            try:
                ent_req = int(float(e.get("request_qty") if e.get("request_qty") is not None else req_total))
            except (TypeError, ValueError):
                ent_req = req_total
            try:
                ent_cur = int(float(e.get("current_qty") if e.get("current_qty") is not None else cur_q))
            except (TypeError, ValueError):
                ent_cur = cur_q
            ent_notes = str(e.get("notes") or "").strip()
            ent_status = segment_production_status_display(e, prod_status) if str(e.get("kind") or "").strip() == "request_change" else prod_status
            rows.append(
                {
                    "_sort_ts": ent_ts,
                    "السلعة": pname,
                    "الوحدة": str(e.get("unit_val") or e.get("unit") or row.get("unit_val") or row.get("unit") or "قطعة").strip() or "قطعة",
                    "القسم": sec,
                    "المتوفر": ent_cur,
                    "المطلوب": ent_req,
                    "الحالة": ent_status,
                    "المسؤول الحالي": str(e.get("by") or actor).strip() or "—",
                    "الشفت": shift_cell,
                    "ملاحظات": ent_notes or "—",
                    "آخر تحديث": format_baghdad_time(e.get("at") or e.get("timestamp")),
                }
            )
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=cols_order)
    out = out.sort_values(by="_sort_ts", ascending=False, kind="stable").drop(columns=["_sort_ts"], errors="ignore")
    return out.reindex(columns=cols_order)


def format_baghdad_archive_time(value) -> str:
    """تنسيق تاريخ الأرشيف بصيغة واضحة: يوم-شهر-سنة | ساعة:دقيقة ص/م."""
    if value is None or str(value).strip() == "":
        return "—"
    dt = parse_to_baghdad_dt(value)
    if dt is None:
        return str(value)
    try:
        h12, mm, ampm = _baghdad_12h_display_parts(dt)
        return f"{dt.strftime('%d-%m-%Y')} | {h12}:{mm} {ampm}"
    except Exception:
        return str(value)


def build_archive_display_df(archive_rows: list[dict], products_df: pd.DataFrame) -> pd.DataFrame:
    """عرض أرشيف إداري مرتب ومقروء بدون أعمدة IDs الخام."""
    cols = [
        "التاريخ",
        "الشفت",
        "اسم السلعة",
        "القسم",
        "الوحدة",
        "الكمية المتوفرة",
        "الكمية المطلوبة",
        "الملاحظات",
        "حالة الإنتاج",
        "المسؤول",
        "آخر تحديث (بغداد - دقيقة)",
    ]
    if not archive_rows:
        return pd.DataFrame(columns=cols)

    prod_map: dict[str, dict] = {}
    if products_df is not None and not products_df.empty:
        for _, p in products_df.iterrows():
            pid = str(p.get("id") or "").strip()
            if not pid:
                continue
            prod_map[pid] = {
                "name": str(p.get("name") or "").strip() or "—",
                "section_name": str(p.get("section_name") or "عام").strip() or "عام",
                "unit_val": str(p.get("unit_val") or "قطعة").strip() or "قطعة",
            }

    rows: list[dict] = []
    for rec in archive_rows:
        pid = str(rec.get("product_id") or "").strip()
        prod = prod_map.get(pid, {})
        dt = parse_to_baghdad_dt(rec.get("archive_date"))
        sort_ts = dt.timestamp() if dt is not None else 0.0
        shift_label = str(rec.get("shift_name") or "").strip()
        if not shift_label:
            shift_label = _shift_suffix_from_cycle_key(str(rec.get("shift_cycle_key") or "").strip()) or "—"
        rows.append(
            {
                "_sort_ts": sort_ts,
                "التاريخ": format_baghdad_archive_time(rec.get("archive_date")),
                "الشفت": shift_label,
                "اسم السلعة": str(rec.get("product_name") or "").strip() or prod.get("name", "سلعة محذوفة/غير معروفة"),
                "القسم": str(rec.get("section_name") or "").strip() or prod.get("section_name", "—"),
                "الوحدة": str(rec.get("unit_val") or "").strip() or prod.get("unit_val", "—"),
                "الكمية المتوفرة": int(float(rec.get("archived_qty") or 0)),
                "الكمية المطلوبة": int(float(rec.get("archived_request_qty") or 0)),
                "الملاحظات": str(rec.get("notes") or "").strip() or "—",
                "حالة الإنتاج": str(rec.get("production_status") or "").strip() or "—",
                "المسؤول": str(rec.get("last_updated_by") or "").strip() or "—",
                "آخر تحديث (بغداد - دقيقة)": format_baghdad_time(rec.get("last_updated_at")),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=cols)
    out = out.sort_values(by="_sort_ts", ascending=False, kind="stable").drop(columns=["_sort_ts"], errors="ignore")
    return out.reindex(columns=cols)


def archive_date_key_baghdad(value) -> str:
    """مفتاح تاريخي يومي (YYYY-MM-DD) حسب توقيت بغداد."""
    dt = parse_to_baghdad_dt(value)
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d")


def build_flat_preview_display_df(preview_source: pd.DataFrame) -> pd.DataFrame:
    """ورقة المعاينة للمدير/مسؤول القسم: صف مستقل لكل إدخال سجل (الأحدث أولاً)."""
    cols = [
        "name",
        "unit_val",
        "section_name",
        "current_qty",
        "request_qty",
        "production_status",
        "notes",
        "last_updated_by",
        "last_updated_at_baghdad",
    ]
    if preview_source.empty:
        return pd.DataFrame(columns=cols)
    rows: list[dict] = []
    for _, row in preview_source.iterrows():
        pname = str(row.get("name") or "").strip()
        sec = str(row.get("section_name") or "عام").strip() or "عام"
        try:
            cur_q = int(float(row.get("current_qty") or 0))
        except (TypeError, ValueError):
            cur_q = 0
        try:
            req_total = int(float(row.get("request_qty") or 0))
        except (TypeError, ValueError):
            req_total = 0
        notes = str(row.get("notes") or "").strip()
        status = str(row.get("production_status") or "بانتظار الإنتاج").strip() or "بانتظار الإنتاج"
        if status not in PRODUCTION_STATUS_OPTIONS:
            status = PRODUCTION_STATUS_OPTIONS[0]
        actor = str(row.get("last_updated_by") or "").strip() or "—"
        row_cycle_key = str(row.get("shift_cycle_key") or "").strip()
        if not row_cycle_key:
            row_cycle_key = baghdad_shift_cycle_info()["cycle_key"]
        raw_log = filter_recommendation_log_for_cycle(row.get("recommendation_log"), row_cycle_key)
        entries = [dict(e) for e in coerce_recommendation_log(raw_log) if not _is_ghost_ui_log_entry(e)]
        entries.sort(
            key=lambda e: recommendation_log_entry_dt(e).timestamp() if recommendation_log_entry_dt(e) else 0.0,
            reverse=True,
        )
        if not entries:
            rows.append(
                {
                    "_sort_ts": 0.0,
                    "name": pname,
                    "unit_val": str(row.get("unit_val") or row.get("unit") or "قطعة").strip() or "قطعة",
                    "section_name": sec,
                    "current_qty": cur_q,
                    "request_qty": req_total,
                    "production_status": status,
                    "notes": notes,
                    "last_updated_by": actor,
                    "last_updated_at_baghdad": format_baghdad_time(row.get("last_updated_at")),
                }
            )
            continue

        for e in entries:
            ent_dt = parse_to_baghdad_dt(e.get("at"))
            ent_ts = ent_dt.timestamp() if ent_dt is not None else 0.0
            try:
                ent_req = int(float(e.get("request_qty") if e.get("request_qty") is not None else req_total))
            except (TypeError, ValueError):
                ent_req = req_total
            try:
                ent_cur = int(float(e.get("current_qty") if e.get("current_qty") is not None else cur_q))
            except (TypeError, ValueError):
                ent_cur = cur_q
            ent_notes = str(e.get("notes") or "").strip()
            ent_status = segment_production_status_display(e, status) if str(e.get("kind") or "").strip() == "request_change" else status
            unit_from_entry = str(e.get("unit_val") or e.get("unit") or "").strip()
            if not unit_from_entry:
                unit_from_entry = str(row.get("unit_val") or row.get("unit") or "قطعة").strip() or "قطعة"
            rows.append(
                {
                    "_sort_ts": ent_ts,
                    "name": pname,
                    "unit_val": unit_from_entry,
                    "section_name": sec,
                    "current_qty": ent_cur,
                    "request_qty": ent_req,
                    "production_status": ent_status,
                    "notes": ent_notes or "—",
                    "last_updated_by": str(e.get("by") or actor).strip() or "—",
                    "last_updated_at_baghdad": format_baghdad_time(e.get("at") or e.get("timestamp")),
                }
            )
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=cols)
    out = out.sort_values(by="_sort_ts", ascending=False, kind="stable").drop(columns=["_sort_ts"], errors="ignore")
    return out.reindex(columns=cols)


def _style_flat_preview_df(df: pd.DataFrame):
    """حاليًا بدون تلوين خاص بعد اعتماد صف مستقل لكل سجل."""

    def _row_style(s: pd.Series) -> list[str]:
        return [""] * len(s)

    try:
        return df.style.apply(_row_style, axis=1)
    except Exception:
        return df


def export_excel_bytes(df: pd.DataFrame) -> bytes:
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="inventory")
    return bio.getvalue()


def build_pdf_bytes(display_df: pd.DataFrame, exported_by: str | None = None) -> bytes:
    import arabic_reshaper
    from bidi.algorithm import get_display
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    def _shape_line(text: str) -> str:
        raw = str(text if text is not None else "")
        return get_display(arabic_reshaper.reshape(raw))

    # خطوط من النظام أو مجلد المشروع فقط — بلا أي تحميل من الإنترنت أو GitHub.
    project_dir = os.path.dirname(os.path.abspath(__file__))
    regular_candidates: list[str] = []
    bold_candidates: list[str] = []
    if os.name == "nt":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        fonts_dir = os.path.join(windir, "Fonts")
        regular_candidates.extend(
            [
                os.path.join(fonts_dir, "trado.ttf"),
                os.path.join(fonts_dir, "arabtype.ttf"),
                os.path.join(fonts_dir, "tahoma.ttf"),
                os.path.join(fonts_dir, "arial.ttf"),
            ]
        )
        bold_candidates.extend(
            [
                os.path.join(fonts_dir, "tahomabd.ttf"),
                os.path.join(fonts_dir, "arialbd.ttf"),
            ]
        )
    regular_candidates.extend(
        [
            os.path.join(project_dir, "fonts", "NotoNaskhArabic-Regular.ttf"),
            os.path.join(project_dir, "NotoNaskhArabic-Regular.ttf"),
        ]
    )
    bold_candidates.extend(
        [
            os.path.join(project_dir, "fonts", "NotoNaskhArabic-Bold.ttf"),
            os.path.join(project_dir, "NotoNaskhArabic-Bold.ttf"),
        ]
    )
    pdf_font_name = "Helvetica"
    pdf_font_bold = "Helvetica-Bold"
    font_internal_name = "BabAghaPdfArabic"
    font_internal_bold = "BabAghaPdfArabicBold"

    for fp in regular_candidates:
        try:
            if os.path.isfile(fp) and os.path.getsize(fp) > 5000:
                if font_internal_name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(font_internal_name, fp))
                pdf_font_name = font_internal_name
                break
        except Exception:
            continue

    for fp in bold_candidates:
        try:
            if os.path.isfile(fp) and os.path.getsize(fp) > 5000:
                if font_internal_bold not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(font_internal_bold, fp))
                pdf_font_bold = font_internal_bold
                break
        except Exception:
            continue
    if pdf_font_name == "Helvetica":
        pdf_font_bold = "Helvetica-Bold"
    elif pdf_font_bold == "Helvetica-Bold":
        pdf_font_bold = pdf_font_name

    # نفس ترتيب المعاينة المفلترة والمسطحة (build_export_df)، مع عرض RTL:
    # العمود الأول منطقيًا يظهر أقصى اليمين داخل الجدول.
    logical_cols = [str(c) for c in display_df.columns.tolist()] if not display_df.empty else []
    if not logical_cols:
        logical_cols = [
            "السلعة",
            "الوحدة",
            "القسم",
            "المتوفر",
            "المطلوب",
            "الحالة",
            "المسؤول الحالي",
            "الشفت",
            "ملاحظات",
            "آخر تحديث",
        ]
    visual_cols = list(reversed(logical_cols))

    table_data: list[list[Paragraph]] = []
    stylesheet = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BabAghaPdfTitle",
        parent=stylesheet["Title"],
        fontName=pdf_font_bold,
        fontSize=18,
        leading=24,
        alignment=1,
        textColor=colors.HexColor("#5A2E1A"),
    )
    cell_style = ParagraphStyle(
        "BabAghaPdfCell",
        parent=stylesheet["BodyText"],
        fontName=pdf_font_name,
        fontSize=12,
        leading=16,
        alignment=2,
        wordWrap="RTL",
        textColor=colors.HexColor("#2F1D12"),
    )
    header_style = ParagraphStyle(
        "BabAghaPdfHeader",
        parent=cell_style,
        fontName=pdf_font_bold,
        fontSize=13,
        leading=17,
    )
    empty_style = ParagraphStyle(
        "BabAghaPdfEmpty",
        parent=cell_style,
        alignment=1,
        fontSize=13,
    )
    page_meta_style = ParagraphStyle(
        "BabAghaPdfPageMeta",
        parent=cell_style,
        fontName=pdf_font_name,
        fontSize=9,
        leading=11,
        alignment=0,
        textColor=colors.HexColor("#4A2A1A"),
    )

    header_row = [Paragraph(_shape_line(col), header_style) for col in visual_cols]
    table_data.append(header_row)

    if display_df.empty:
        empty_cells = [Paragraph("", cell_style) for _ in visual_cols]
        mid_idx = len(empty_cells) // 2 if empty_cells else 0
        if empty_cells:
            empty_cells[mid_idx] = Paragraph(_shape_line("لا توجد بيانات للتصدير."), empty_style)
        table_data.append(empty_cells)
    else:
        for _, row in display_df.iterrows():
            row_cells: list[Paragraph] = []
            for col in visual_cols:
                raw_val = row.get(col, "")
                cell_text = "—" if raw_val is None or str(raw_val).strip() == "" else str(raw_val).strip()
                row_cells.append(Paragraph(_shape_line(cell_text), cell_style))
            table_data.append(row_cells)

    # توزيع أعرض للأعمدة النصية ومرن لباقي الأعمدة (A4 أفقي لقراءة أوضح 12+).
    page_w, _ = landscape(A4)
    left_right_margin = 12 * mm
    usable_w = page_w - (left_right_margin * 2)
    wide_cols = {"السلعة", "ملاحظات", "الحالة", "المسؤول الحالي"}
    weights = [1.8 if col in wide_cols else 1.0 for col in visual_cols]
    total_weight = sum(weights) if weights else 1.0
    col_widths = [(usable_w * wgt / total_weight) for wgt in weights] if visual_cols else []

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), pdf_font_bold),
                ("FONTNAME", (0, 1), (-1, -1), pdf_font_name),
                ("FONTSIZE", (0, 0), (-1, 0), 13),
                ("FONTSIZE", (0, 1), (-1, -1), 12),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6E2C2C")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#FFF8EE")),
                ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#2F1D12")),
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.55, colors.HexColor("#B08B63")),
                ("BOX", (0, 0), (-1, -1), 1.0, colors.HexColor("#6A3A20")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#FFFDF8"), colors.HexColor("#FFF6E8")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        rightMargin=left_right_margin,
        leftMargin=left_right_margin,
        topMargin=20 * mm,
        bottomMargin=16 * mm,
        title="مخابز باب الآغا — تقرير الجرد",
    )
    story = [
        Paragraph(_shape_line("مخابز باب الآغا — تقرير الجرد"), title_style),
        Spacer(1, 7 * mm),
        table,
    ]
    shift_info = baghdad_shift_cycle_info()
    now_baghdad = datetime.now(IRAQ_TZ)
    shift_name = str(shift_info.get("shift_name") or "—").strip() or "—"
    shift_date = now_baghdad.strftime("%Y-%m-%d")
    export_stamp = now_baghdad.strftime("%Y-%m-%d %H:%M:%S")
    exported_by_label = str(exported_by or "").strip() or "غير محدد"
    header_meta_text = _shape_line(f"الشفت: {shift_name} | التاريخ: {shift_date}")
    footer_text = _shape_line(
        f"تم تصدير هذا التقرير بتاريخ {export_stamp} بتوقيت بغداد — بواسطة المستخدم: {exported_by_label}."
    )

    def _draw_page_chrome(canv, _doc):
        canv.saveState()
        header_par = Paragraph(header_meta_text, page_meta_style)
        hw, hh = header_par.wrap(_doc.width, 14 * mm)
        header_par.drawOn(canv, _doc.leftMargin, _doc.height + _doc.bottomMargin + max((8 * mm - hh), 0))

        footer_par = Paragraph(footer_text, page_meta_style)
        fw, fh = footer_par.wrap(_doc.width, 12 * mm)
        footer_par.drawOn(canv, _doc.leftMargin, max((7 * mm - fh / 2), 2 * mm))
        canv.restoreState()

    doc.build(story, onFirstPage=_draw_page_chrome, onLaterPages=_draw_page_chrome)
    return buf.getvalue()


def login_screen():
    st.title("🍞 نظام باب الآغا - تسجيل الدخول")
    users_list, users_ok = get_login_usernames()
    if not users_ok:
        return
    if not users_list:
        st.warning("لا يوجد مستخدمون متاحون حالياً لتسجيل الدخول.")
        return

    c1, c2 = st.columns(2)
    with c1:
        username = st.selectbox("اختر المستخدم", users_list, index=None, placeholder="اختر المستخدم")
    with c2:
        password = st.text_input("كلمة المرور", type="password")

    if st.button("تسجيل الدخول", use_container_width=True):
        if not username:
            st.error("يرجى اختيار المستخدم أولاً.")
            return
        user, query_ok = fetch_user(username)
        if not query_ok:
            return
        if not user:
            st.error("المستخدم غير موجود.")
            return
        if str(user.get("password_text", "")) != str(password):
            st.error("كلمة المرور غير صحيحة.")
            return
        st.session_state["is_logged_in"] = True
        st.session_state["user_id"] = user.get("id")
        st.session_state["username"] = user.get("username")
        st.session_state["role"] = user.get("role", "")
        st.session_state["permissions"] = normalize_permissions_for_session(
            user.get("permissions", {}) or {}, str(user.get("role", "") or "")
        )
        st.session_state["managed_sections"] = coerce_managed_sections_list(user.get("managed_sections"))
        push_notification(f"قام {user.get('username', 'مستخدم')} بتسجيل الدخول", target_role=None)
        st.success("تم تسجيل الدخول بنجاح.")
        st.rerun()


def create_user_form():
    st.subheader("إضافة مستخدم جديد")
    with st.form("create_user"):
        uname = st.text_input("اسم المستخدم")
        pwd = st.text_input("كلمة المرور", type="password")
        selected_role_label = st.selectbox("الدور", list(ROLE_LABEL_TO_CODE.keys()))
        role_name = ROLE_LABEL_TO_CODE[selected_role_label]
        permissions: dict = {}
        st.markdown("**صلاحيات الشاشات**")
        cols_s = st.columns(2)
        for idx, (key, _) in enumerate(SCREEN_PERMISSION_ORDER):
            with cols_s[idx % 2]:
                permissions[key] = st.checkbox(
                    PERMISSION_LABELS_AR.get(key, key), value=False, key=f"newuser_perm_{key}"
                )
        st.markdown("**صلاحيات الإدارة** (اختياري — عادة للمدير فقط)")
        cols_a = st.columns(2)
        admin_keys = [k for k, _ in ADMIN_PERMISSION_ORDER]
        for idx, key in enumerate(admin_keys):
            with cols_a[idx % 2]:
                permissions[key] = st.checkbox(
                    PERMISSION_LABELS_AR.get(key, key), value=False, key=f"newuser_admin_{key}"
                )
        managed_secs: list[str] = []
        if role_name == "DeptManager":
            pref = load_products()
            opts = sorted({str(x).strip() for x in pref.get("section_name", pd.Series(dtype=object)).dropna().tolist() if str(x).strip()})
            managed_secs = st.multiselect(
                "الأقسام التي يديرها مسؤول القسم (جرد ومعاينة)",
                options=opts if opts else ["عام"],
                default=[],
                key="newuser_managed_sections",
            )
            extra_sec = st.text_input("إضافة أسماء أقسام جديدة (افصل بينها بفاصلة)", key="newuser_extra_sections")
            if extra_sec.strip():
                managed_secs = list(dict.fromkeys(managed_secs + [s.strip() for s in extra_sec.split(",") if s.strip()]))
        if st.form_submit_button("حفظ المستخدم"):
            if not uname.strip() or not pwd.strip():
                st.error("يرجى إدخال الاسم وكلمة المرور.")
                return
            row = {
                "username": uname.strip(),
                "password_text": pwd.strip(),
                "role": role_name,
                "permissions": permissions,
                "managed_sections": managed_secs if role_name == "DeptManager" else [],
            }
            ok_ins, _, exc_ins = supabase_with_retry(lambda r=row: supabase_client.table("app_users").insert(r).execute())
            if not ok_ins:
                if exc_ins is not None and is_network_transport_error(exc_ins):
                    st.warning("يرجى التحقق من اتصال الإنترنت. تعذر حفظ المستخدم.")
                elif "23505" in str(exc_ins).lower() or "duplicate key" in str(exc_ins).lower():
                    st.warning("عذراً، اسم المستخدم هذا مسجل مسبقاً. يرجى اختيار اسم آخر.")
                else:
                    st.error(f"تعذر إنشاء المستخدم: {exc_ins}")
                return
            st.success("تم إنشاء المستخدم بنجاح.")
            push_notification(f"تم إنشاء مستخدم جديد: {uname.strip()}", target_role="Admin")


def is_protected_primary_admin(user_row: dict) -> bool:
    """الحساب الأساسي للمدير — يُسمح بتغيير كلمة المرور فقط (لا حذف ولا تغيير دور)."""
    return normalize_username(user_row.get("username", "")) == normalize_username("باب الاغا")


def manage_existing_users_section():
    st.subheader("إدارة وتعديل المستخدمين الحاليين")
    ok, res, exc = supabase_with_retry(
        lambda: supabase_client.table("app_users")
        .select("id,username,role,managed_sections")
        .order("username")
        .execute()
    )
    if not ok or res is None:
        if exc is not None and is_network_transport_error(exc):
            st.warning("يرجى التحقق من اتصال الإنترنت. تعذر جلب المستخدمين.")
        else:
            st.error(f"تعذر جلب المستخدمين: {exc}")
        return
    rows = res.data or []
    if not rows:
        st.info("لا يوجد مستخدمون في النظام.")
        return

    labels = [str(r.get("username", "")) for r in rows]
    pick = st.selectbox("اختر المستخدم", labels, key="admin_manage_existing_user_pick")
    user_row = next(r for r in rows if str(r.get("username", "")) == pick)
    uid = user_row.get("id")
    uid_str = str(uid)
    protected = is_protected_primary_admin(user_row)
    is_dept = str(user_row.get("role", "")).strip().lower() == "deptmanager"

    if protected:
        st.warning("هذا حساب المدير الأساسي — يمكن تغيير كلمة المرور فقط لمنع فقدان الوصول للنظام.")
        new_pwd = st.text_input("كلمة المرور الجديدة", type="password", key="protected_admin_new_pwd")
        if st.button("حفظ كلمة المرور", key="protected_admin_save_pwd"):
            if not str(new_pwd or "").strip():
                st.error("يرجى إدخال كلمة مرور جديدة.")
            else:
                ok_u, _, exc_u = supabase_with_retry(
                    lambda: supabase_client.table("app_users")
                    .update({"password_text": str(new_pwd).strip()})
                    .eq("id", uid)
                    .execute()
                )
                if not ok_u:
                    if exc_u is not None and is_network_transport_error(exc_u):
                        st.warning("يرجى التحقق من اتصال الإنترنت.")
                    else:
                        st.error(f"تعذر التحديث: {exc_u}")
                else:
                    st.success("تم تحديث كلمة المرور.")
                    st.rerun()
        return

    # عند تغيير المستخدم المختار: مسح مفاتيح «أقسام مسؤول القسم» من الجلسة ثم إعادة التهيئة من قاعدة البيانات (مفاتيح الويدجت مربوطة بـ user_id فقط).
    _sec_track = "_admin_section_editor_track_uid"
    if str(st.session_state.get(_sec_track, "") or "") != uid_str:
        for _k in list(st.session_state.keys()):
            if not isinstance(_k, str):
                continue
            if _k.startswith("edit_user_managed_sections_") or _k.startswith("edit_user_extra_managed_"):
                del st.session_state[_k]
        st.session_state[_sec_track] = uid_str
        # تهيئة فورية من قاعدة البيانات للمستخدم الجديد (تجنّب بقاء أقسام المستخدم السابق في الواجهة)
        if is_dept:
            pref_seed = load_products()
            opts_seed = sorted(
                {
                    str(x).strip()
                    for x in pref_seed.get("section_name", pd.Series(dtype=object)).dropna().tolist()
                    if str(x).strip()
                }
            )
            cms_seed = coerce_managed_sections_list(user_row.get("managed_sections"))
            opts_seed = sorted(set(opts_seed) | set(cms_seed))
            opt_seed = opts_seed if opts_seed else ["عام"]
            st.session_state[f"edit_user_managed_sections_{uid_str}"] = [x for x in cms_seed if x in opt_seed]
            st.session_state[f"edit_user_extra_managed_{uid_str}"] = ""

    tab_pw, tab_role, tab_sec, tab_del = st.tabs(
        ["تغيير كلمة المرور", "تغيير الدور", "أقسام مسؤول القسم", "حذف المستخدم"]
    )
    with tab_pw:
        npw = st.text_input("كلمة المرور الجديدة", type="password", key=f"edit_user_new_pwd_{uid_str}")
        if st.button("حفظ كلمة المرور", key=f"edit_user_save_pwd_{uid_str}"):
            if not str(npw or "").strip():
                st.error("يرجى إدخال كلمة مرور.")
            else:
                ok_u, _, exc_u = supabase_with_retry(
                    lambda: supabase_client.table("app_users")
                    .update({"password_text": str(npw).strip()})
                    .eq("id", uid)
                    .execute()
                )
                if not ok_u:
                    if exc_u is not None and is_network_transport_error(exc_u):
                        st.warning("يرجى التحقق من اتصال الإنترنت.")
                    else:
                        st.error(f"تعذر التحديث: {exc_u}")
                else:
                    st.success("تم تحديث كلمة المرور.")
                    st.rerun()
    with tab_role:
        rc = str(user_row.get("role", "") or "").strip()
        current_label = None
        for code, label in ROLE_CODE_TO_LABEL.items():
            if code.lower() == rc.lower():
                current_label = label
                break
        if current_label is None and rc in LEGACY_ROLE_TO_LABEL:
            current_label = LEGACY_ROLE_TO_LABEL[rc]
        display_role = (
            current_label
            if current_label is not None and current_label in ROLE_LABEL_TO_CODE
            else (rc or "—")
        )
        select_label = (
            current_label
            if current_label is not None and current_label in ROLE_LABEL_TO_CODE
            else list(ROLE_LABEL_TO_CODE.keys())[0]
        )
        st.markdown(
            f'<p dir="rtl" style="margin-bottom:0.75rem">الدور الحالي لهذا المستخدم هو: '
            f"<strong>{html.escape(str(display_role))}</strong></p>",
            unsafe_allow_html=True,
        )
        role_keys = list(ROLE_LABEL_TO_CODE.keys())
        idx = role_keys.index(select_label) if select_label in role_keys else 0
        new_label = st.selectbox("الدور الجديد", role_keys, index=idx, key=f"edit_user_role_select_{uid_str}")
        if st.button("حفظ الدور", key=f"edit_user_save_role_{uid_str}"):
            new_code = ROLE_LABEL_TO_CODE[new_label]
            ok_u, _, exc_u = supabase_with_retry(
                lambda: supabase_client.table("app_users").update({"role": new_code}).eq("id", uid).execute()
            )
            if not ok_u:
                if exc_u is not None and is_network_transport_error(exc_u):
                    st.warning("يرجى التحقق من اتصال الإنترنت.")
                else:
                    st.error(f"تعذر تحديث الدور: {exc_u}")
            else:
                st.session_state["_admin_flash_role_saved"] = True
                st.rerun()
    with tab_sec:
        if not is_dept:
            st.caption("هذا القسم مخصص لحسابات «مسؤول قسم» فقط.")
        else:
            pref = load_products()
            opts = sorted(
                {str(x).strip() for x in pref.get("section_name", pd.Series(dtype=object)).dropna().tolist() if str(x).strip()}
            )
            current_ms = coerce_managed_sections_list(user_row.get("managed_sections"))
            opts = sorted(set(opts) | set(current_ms))
            opt_list = opts if opts else ["عام"]
            ms_key = f"edit_user_managed_sections_{uid_str}"
            ex_key = f"edit_user_extra_managed_{uid_str}"
            if ms_key not in st.session_state:
                st.session_state[ms_key] = [x for x in current_ms if x in opt_list]
            if ex_key not in st.session_state:
                st.session_state[ex_key] = ""
            sel = st.multiselect(
                "الأقسام المصرّح بجردها ومعاينتها",
                options=opt_list,
                key=ms_key,
            )
            extra_ms = st.text_input("أقسام إضافية (مفصولة بفاصلة)", key=ex_key)
            merged_ms = list(dict.fromkeys(sel + [s.strip() for s in (extra_ms or "").split(",") if s.strip()]))
            if st.button("حفظ الأقسام", key=f"edit_user_save_managed_{uid_str}"):
                ok_u, _, exc_u = supabase_with_retry(
                    lambda ms=merged_ms: supabase_client.table("app_users")
                    .update({"managed_sections": ms})
                    .eq("id", uid)
                    .execute()
                )
                if not ok_u:
                    if exc_u is not None and is_network_transport_error(exc_u):
                        st.warning("يرجى التحقق من اتصال الإنترنت.")
                    else:
                        st.error(f"تعذر حفظ الأقسام: {exc_u}")
                else:
                    st.success("تم تحديث أقسام المستخدم.")
                    if str(st.session_state.get("user_id", "")) == str(uid):
                        st.session_state["managed_sections"] = merged_ms
                    st.rerun()
    with tab_del:
        st.error("سيتم حذف المستخدم نهائياً من قاعدة البيانات.")
        if st.button("حذف المستخدم", type="primary", key=f"edit_user_delete_{uid_str}"):
            ok_d, _, exc_d = supabase_with_retry(lambda: supabase_client.table("app_users").delete().eq("id", uid).execute())
            if not ok_d:
                if exc_d is not None and is_network_transport_error(exc_d):
                    st.warning("يرجى التحقق من اتصال الإنترنت.")
                else:
                    st.error(f"تعذر الحذف: {exc_d}")
            else:
                st.success("تم حذف المستخدم.")
                if str(st.session_state.get("user_id", "")) == str(uid):
                    st.session_state.clear()
                st.rerun()


def _baker_cards_init_session(
    source: pd.DataFrame,
    *,
    is_baker_role: bool,
    can_edit_request: bool,
    can_edit_unit: bool,
) -> None:
    """تهيئة مفاتيح الجلسة لشاشة الخلفة (منسدلات) حسب الصلاحيات."""
    for i in range(len(source)):
        row = source.iloc[i]
        pid = str(row["id"])
        cq = int(pd.to_numeric(row.get("current_qty"), errors="coerce") or 0)
        nk = f"bk_notes_{pid}"
        if not is_baker_role and nk not in st.session_state:
            st.session_state[nk] = str(row.get("notes") or "")
        sk = f"bk_status_{pid}"
        if sk not in st.session_state:
            ps = str(row.get("production_status") or "بانتظار الإنتاج").strip()
            st.session_state[sk] = ps if ps in PRODUCTION_STATUS_OPTIONS else PRODUCTION_STATUS_OPTIONS[0]
        ck = f"bk_cur_{pid}"
        if ck not in st.session_state:
            st.session_state[ck] = cq
        if can_edit_request:
            rk = f"bk_req_{pid}"
            if rk not in st.session_state:
                st.session_state[rk] = int(pd.to_numeric(row.get("request_qty"), errors="coerce") or 0)
        if can_edit_unit:
            raw_u = str(row.get("unit_val", "قطعة") or "قطعة").strip() or "قطعة"
            uk = f"bk_unit_{pid}"
            if uk not in st.session_state:
                st.session_state[uk] = raw_u if raw_u in INVENTORY_UNIT_OPTIONS else "مخصص"
            uck = f"bk_uc_{pid}"
            if raw_u not in INVENTORY_UNIT_OPTIONS and uck not in st.session_state:
                st.session_state[uck] = raw_u


def _baker_cards_collect_updates(
    source: pd.DataFrame,
    *,
    is_baker_role: bool,
    can_edit_request: bool,
    can_edit_unit: bool,
) -> list[dict]:
    """يجمع تحديثات منسدلات الخلفة من session_state حسب الصلاحيات."""
    updates: list[dict] = []
    for i in range(len(source)):
        row = source.iloc[i]
        pid = str(row["id"])
        if can_edit_request:
            req = int(pd.to_numeric(st.session_state.get(f"bk_req_{pid}", row.get("request_qty") or 0), errors="coerce") or 0)
        else:
            req = int(pd.to_numeric(row.get("request_qty"), errors="coerce") or 0)
        if can_edit_unit:
            sel_unit = str(st.session_state.get(f"bk_unit_{pid}", row.get("unit_val") or "قطعة") or "قطعة").strip()
            if sel_unit == "مخصص":
                custom_u = str(st.session_state.get(f"bk_uc_{pid}", "") or "").strip()
                unit_val = custom_u or "مخصص"
            else:
                unit_val = sel_unit or "قطعة"
        else:
            unit_val = str(row.get("unit_val", "قطعة") or "قطعة").strip() or "قطعة"
        if is_baker_role:
            cur = int(pd.to_numeric(row.get("current_qty"), errors="coerce") or 0)
            notes_val = str(row.get("notes") or "").strip()
        else:
            cur = int(st.session_state.get(f"bk_cur_{pid}", row.get("current_qty") or 0))
            notes_val = str(st.session_state.get(f"bk_notes_{pid}", row.get("notes") or "") or "").strip()
        status_val = str(st.session_state.get(f"bk_status_{pid}", "بانتظار الإنتاج") or "").strip()
        if not status_val:
            status_val = "بانتظار الإنتاج"
        old_cur = int(pd.to_numeric(row.get("current_qty"), errors="coerce") or 0)
        old_req = int(pd.to_numeric(row.get("request_qty"), errors="coerce") or 0)
        old_notes = str(row.get("notes") or "").strip()
        old_status = str(row.get("production_status") or "بانتظار الإنتاج").strip() or "بانتظار الإنتاج"
        old_unit = str(row.get("unit_val", "قطعة") or "قطعة").strip() or "قطعة"
        changed = (
            cur != old_cur
            or req != old_req
            or notes_val != old_notes
            or status_val != old_status
            or unit_val != old_unit
        )
        if not changed:
            continue
        updates.append(
            {
                "id": pid,
                "unit_val": unit_val,
                "current_qty": cur,
                "request_qty": req,
                "notes": notes_val,
                "production_status": status_val,
            }
        )
    return updates


def _inventory_expander_title_unit(pid: str, raw_u: str) -> str:
    """وحدة العرض في عنوان المنسدل (من الجلسة إن وُجدت، وإلا من قاعدة البيانات)."""
    raw_u = (raw_u or "قطعة").strip() or "قطعة"
    key_u = f"inv_unit_{pid}"
    if key_u not in st.session_state:
        return raw_u
    sel = str(st.session_state.get(key_u) or "قطعة").strip()
    if sel == "مخصص":
        uc = str(st.session_state.get(f"inv_uc_{pid}", "") or "").strip()
        return uc or "مخصص"
    return sel or raw_u


def _inventory_list_collect_updates(
    source: pd.DataFrame,
    *,
    can_request: bool,
    can_edit_unit: bool,
) -> list[dict]:
    """يجمع تحديثات شاشة الجرد (عرض القائمة) من مفاتيح session_state الموحدة بمعرّف السلعة."""
    updates: list[dict] = []
    for i in range(len(source)):
        row = source.iloc[i]
        pid = str(row["id"])
        cur = int(float(st.session_state.get(f"inv_cur_{pid}", row.get("current_qty") or 0)))
        notes_val = str(st.session_state.get(f"inv_notes_{pid}", row.get("notes") or "") or "").strip()
        if can_request:
            req = int(float(st.session_state.get(f"inv_req_{pid}", row.get("request_qty") or 0)))
        else:
            req = int(float(row.get("request_qty") or 0))
        if can_edit_unit:
            sel = st.session_state.get(f"inv_unit_{pid}", row.get("unit_val", "قطعة"))
            sel = str(sel or "قطعة").strip()
            if sel == "مخصص":
                unit_val = str(st.session_state.get(f"inv_uc_{pid}", "") or "").strip() or "مخصص"
            else:
                unit_val = sel or "قطعة"
        else:
            unit_val = str(row.get("unit_val", "قطعة") or "قطعة").strip() or "قطعة"
        status_val = str(row.get("production_status", "بانتظار الإنتاج") or "بانتظار الإنتاج").strip()
        old_cur = int(pd.to_numeric(row.get("current_qty"), errors="coerce") or 0)
        old_req = int(pd.to_numeric(row.get("request_qty"), errors="coerce") or 0)
        old_notes = str(row.get("notes") or "").strip()
        old_status = str(row.get("production_status") or "بانتظار الإنتاج").strip() or "بانتظار الإنتاج"
        old_unit = str(row.get("unit_val", "قطعة") or "قطعة").strip() or "قطعة"
        changed = (
            cur != old_cur
            or req != old_req
            or notes_val != old_notes
            or status_val != old_status
            or unit_val != old_unit
        )
        if not changed:
            continue
        updates.append(
            {
                "id": pid,
                "unit_val": unit_val,
                "current_qty": cur,
                "request_qty": req,
                "notes": notes_val,
                "production_status": status_val if status_val else "بانتظار الإنتاج",
            }
        )
    return updates


def render_inventory(products_df: pd.DataFrame):
    st.header("📦 الجرد اليومي")
    _inv_shift = baghdad_shift_cycle_info()
    st.caption(f"الشفت الحالي (بغداد): {_inv_shift['label']} — تُسجَّل كل عمليات الحفظ على هذا الشفت.")
    if (st.session_state.get("role") or "").strip().lower() == "baker":
        st.warning("شاشة الجرد العامة غير متاحة لدور الخلفة. استخدم شاشة الخلفة فقط.")
        return
    if (st.session_state.get("role") or "").strip().lower() == "deptmanager":
        ms = session_managed_sections()
        if ms:
            st.caption("**أقسام مسؤوليتك:** " + "، ".join(ms))
        else:
            st.caption(
                "لم تُحدد أقسام في ملفك — يُعرض جرد **جميع** السلع. يمكن للمدير ضبط «أقسام مسؤول القسم» من إدارة المستخدمين."
            )
        st.caption("يُعرض جرد **الشفت الحالي** فقط (توقيت بغداد)؛ مع بقاء أرشيف الشفت السابق في التقارير وعند المدير.")
    if products_df.empty:
        st.info("لا توجد سلع بعد. أضف سلعة من إعدادات النظام.")
        return

    can_req = user_has("can_request_production")
    if not can_req:
        st.caption("يمكنك تعديل **المتوفر** فقط. حقل **المطلوب** غير متاح لصلاحيات حسابك.")

    source = products_df.copy()
    if "name" not in source.columns:
        source["name"] = ""
    if "unit_val" not in source.columns:
        source["unit_val"] = "قطعة"
    if "section_name" not in source.columns:
        source["section_name"] = "عام"
    if "id" not in source.columns:
        source["id"] = source.index.astype(str)

    source = source.reset_index(drop=True)
    product_pick_options = [("__all__", "كل سلع القسم")]
    for i in range(len(source)):
        row = source.iloc[i]
        pid = str(row.get("id") or "").strip()
        name_val = str(row.get("name") or "").strip()
        if not pid or not name_val:
            continue
        unit_val = str(row.get("unit_val") or "قطعة").strip() or "قطعة"
        section_val = str(row.get("section_name") or "عام").strip() or "عام"
        product_pick_options.append((pid, f"{name_val} — {unit_val} ({section_val})"))

    pick_map = {k: v for k, v in product_pick_options}
    selected_pid = st.selectbox(
        "بحث عن سلعة",
        options=list(pick_map.keys()),
        format_func=lambda x: pick_map.get(x, str(x)),
        key="inventory_product_pick_main",
        help="القائمة تعرض كل سلع قسمك مباشرة؛ اختر سلعة أو اتركها على «كل سلع القسم».",
    )
    if selected_pid != "__all__":
        source = source[source["id"].astype(str) == selected_pid]

    if source.empty:
        st.info("لا توجد نتائج.")
        return
    source = source.reset_index(drop=True)
    users = get_users_for_assign()
    assignee_map = {str(u.get("id")): str(u.get("username", "")).strip() for u in users if u.get("id") is not None}
    if "section_name" not in source.columns:
        source["section_name"] = "عام"
    if "unit_val" not in source.columns:
        source["unit_val"] = "قطعة"
    if "notes" not in source.columns:
        source["notes"] = ""
    if "current_qty" not in source.columns:
        source["current_qty"] = 0
    if "request_qty" not in source.columns:
        source["request_qty"] = 0
    if "last_updated_by" not in source.columns:
        source["last_updated_by"] = ""
    if "last_updated_at" not in source.columns:
        source["last_updated_at"] = None
    if "production_status" not in source.columns:
        source["production_status"] = "بانتظار الإنتاج"
    if "assigned_baker_id" not in source.columns:
        source["assigned_baker_id"] = None
    for c in ("morning_baker_id", "evening_baker_id", "night_baker_id"):
        if c not in source.columns:
            source[c] = source["assigned_baker_id"]

    can_edit_unit = True
    st.caption("كل سلعة في منسدل — الاسم والوحدة في العنوان؛ تمرير رأسي فقط. القيم تُحفظ في الجلسة حتى الحفظ.")

    for i in range(len(source)):
        row = source.iloc[i]
        pid = str(row["id"])
        name_plain = str(row.get("name", "") or "")
        raw_u = str(row.get("unit_val") or "قطعة").strip() or "قطعة"
        rq0 = int(pd.to_numeric(row.get("request_qty"), errors="coerce") or 0)
        cq0 = int(pd.to_numeric(row.get("current_qty"), errors="coerce") or 0)
        notes0 = str(row.get("notes") or "")
        _live_shift = baghdad_shift_cycle_info().get("shift_name")
        _live_col = baker_column_for_shift_name(_live_shift)
        baker_plain = str(assignee_map.get(str(row.get(_live_col) or ""), "") or "غير محدد").strip()
        sender_plain = str(row.get("last_updated_by") or "").strip()
        t_compact = format_baghdad_compact(row.get("last_updated_at"))
        role_lc = (st.session_state.get("role") or "").strip().lower()
        is_admin_role = role_lc == "admin"
        row_locked = should_lock_row_for_shift(
            row.get("shift_cycle_key"),
            row.get("last_updated_at"),
            current_cycle_key=_inv_shift["cycle_key"],
            is_admin=is_admin_role,
            minutes=10,
        )

        title_unit = _inventory_expander_title_unit(pid, raw_u)
        exp_label = f"{name_plain} — {title_unit}"

        with st.expander(exp_label, expanded=False):
            if row_locked:
                st.caption("🔒 انتهت صلاحية التعديل (10 دقائق)")
            render_recommendation_log_ui(row.get("recommendation_log"))
            if can_req:
                st.number_input(
                    "المطلوب للإنتاج",
                    min_value=0,
                    value=int(rq0),
                    step=1,
                    key=f"inv_req_{pid}",
                    disabled=row_locked,
                )
            else:
                st.write(f"**المطلوب للإنتاج:** {int(rq0)}")
            st.number_input(
                "المتوفر (الكمية الفعلية)",
                min_value=0,
                value=int(cq0),
                step=1,
                key=f"inv_cur_{pid}",
                disabled=row_locked,
            )
            nkey = f"inv_notes_{pid}"
            if nkey not in st.session_state:
                st.session_state[nkey] = notes0
            st.text_area("ملاحظات", key=nkey, placeholder="اختياري", height=88, disabled=row_locked)
            if can_edit_unit:
                st.caption("الوحدة")
                not_in_list = bool(raw_u) and raw_u not in INVENTORY_UNIT_OPTIONS
                def_ix = (
                    INVENTORY_UNIT_OPTIONS.index("مخصص")
                    if not_in_list
                    else (
                        INVENTORY_UNIT_OPTIONS.index(raw_u)
                        if raw_u in INVENTORY_UNIT_OPTIONS
                        else INVENTORY_UNIT_OPTIONS.index("قطعة")
                    )
                )
                st.selectbox(
                    "الوحدة",
                    INVENTORY_UNIT_OPTIONS,
                    index=def_ix,
                    key=f"inv_unit_{pid}",
                    label_visibility="collapsed",
                    disabled=row_locked,
                )
                uc_key = f"inv_uc_{pid}"
                if not_in_list and uc_key not in st.session_state:
                    st.session_state[uc_key] = raw_u
                if str(st.session_state.get(f"inv_unit_{pid}", "")) == "مخصص":
                    st.text_input("وحدة مخصصة", key=uc_key, placeholder="اكتب الوحدة", disabled=row_locked)
            else:
                st.caption("الوحدة")
                st.markdown(f"**{html.escape(raw_u)}**", unsafe_allow_html=True)
            if row_locked and can_req:
                st.caption("يمكنك إنشاء **توصية جديدة** لإعادة فتح التعديل على هذه السلعة لمدة 10 دقائق.")
                if st.button("➕ إنشاء توصية جديدة", key=f"inv_new_reco_{pid}"):
                    # تصفير حقول الإدخال قبل فتح نافذة التعديل حتى لا ترث قيم جلسة سابقة.
                    clear_inventory_session_widgets_for_ids([pid])
                    si_new = baghdad_shift_cycle_info()
                    now_baghdad = baghdad_iso_now()
                    patch = {
                        "notes": "",
                        "last_updated_by": str(st.session_state.get("username") or ""),
                        "last_updated_at": now_baghdad,
                        "shift_name": si_new["shift_name"],
                        "shift_cycle_key": si_new["cycle_key"],
                    }
                    ok_n, _, exc_n = supabase_with_retry(
                        lambda p=patch, _pid=pid: supabase_client.table("products").update(p).eq("id", _pid).execute()
                    )
                    if not ok_n:
                        if exc_n is not None and is_network_transport_error(exc_n):
                            st.warning("يرجى التحقق من اتصال الإنترنت.")
                        else:
                            st.error(f"تعذر إنشاء توصية جديدة: {exc_n}")
                    else:
                        invalidate_products_cache_after_mutation()
                        clear_inventory_session_widgets_for_ids([pid])
                        st.success("تم فتح تحديث جديد لهذه السلعة.")
                        settle_write_then_refresh()
            st.caption(f"الخلفة: {baker_plain} — المرسل: {sender_plain or '—'} — {t_compact}")

    if st.button("✅ حفظ الجرد", key="inventory_save_main"):
        updates = _inventory_list_collect_updates(
            source,
            can_request=can_req,
            can_edit_unit=can_edit_unit,
        )
        role_lc = (st.session_state.get("role") or "").strip().lower()
        if role_lc != "admin":
            locked_ids = {
                str(source.iloc[i]["id"])
                for i in range(len(source))
                if should_lock_row_for_shift(
                    source.iloc[i].get("shift_cycle_key"),
                    source.iloc[i].get("last_updated_at"),
                    current_cycle_key=_inv_shift["cycle_key"],
                    is_admin=False,
                    minutes=10,
                )
            }
            if locked_ids:
                updates = [u for u in updates if str(u.get("id")) not in locked_ids]
        if not updates:
            st.warning("لا توجد كميات فعلية للحفظ.")
            return
        if role_lc == "deptmanager":
            secs = session_managed_sections()
            ok_up, exc_up = batch_upsert_product_quantities(
                updates, managed_section_names=secs if secs else None, updated_by=str(st.session_state.get("username") or "")
            )
        else:
            ok_up, exc_up = batch_upsert_product_quantities(
                updates, updated_by=str(st.session_state.get("username") or "")
            )
        if not ok_up:
            if exc_up is not None and is_network_transport_error(exc_up):
                st.warning("يرجى التحقق من اتصال الإنترنت.")
            else:
                st.error(f"تعذر حفظ الجرد: {exc_up}")
            return
        actor = st.session_state.get("username", "مستخدم غير معروف")
        push_notification(f"تم حفظ جرد جديد بواسطة: {actor}", target_role=None)
        clear_inventory_session_widgets_for_ids([str(u.get("id")) for u in updates if u.get("id") is not None])
        st.success("تم الحفظ بنجاح.")
        st.toast("تم حفظ وإرسال البيانات بنجاح!", icon="✅")
        play_success_beep()
        settle_write_then_refresh()


def render_preview(products_df: pd.DataFrame):
    st.header("🖼️ ورقة المعاينة")
    # سحب مباشر من قاعدة البيانات لضمان أن المعاينة تعرض أحدث الأرقام فوراً.
    preview_live_df = load_products_live_no_cache()
    products_df = preview_live_df
    if products_df.empty:
        st.info("لا يوجد بيانات.")
        return
    visible = products_df[(products_df["current_qty"].fillna(0) > 0) | (products_df["request_qty"].fillna(0) > 0)]
    if visible.empty:
        st.info("المعاينة تعرض فقط السلع ذات كميات فعلية > 0.")
        return
    preview_source = visible.reset_index(drop=True).copy()
    if "production_status" not in preview_source.columns:
        preview_source["production_status"] = "بانتظار الإنتاج"
    if "notes" not in preview_source.columns:
        preview_source["notes"] = ""
    if "last_updated_by" not in preview_source.columns:
        preview_source["last_updated_by"] = ""
    if "last_updated_at" not in preview_source.columns:
        preview_source["last_updated_at"] = None
    if "recommendation_log" not in preview_source.columns:
        preview_source["recommendation_log"] = None

    flat_disp = build_flat_preview_display_df(preview_source)
    flat_ar = flat_disp.rename(
        columns={
            "name": "السلعة",
            "unit_val": "الوحدة",
            "section_name": "القسم",
            "current_qty": "المتوفر",
            "request_qty": "المطلوب",
            "production_status": "الحالة",
            "notes": "ملاحظات",
            "last_updated_by": "آخر تحديث بواسطة",
            "last_updated_at_baghdad": "آخر تحديث (بغداد)",
        }
    )
    st.caption("عرض موحّد للجميع: أحدث طلب في الأعلى، وكل صف يمثل إدخالاً مستقلاً من سجل التوصيات.")
    st.dataframe(_style_flat_preview_df(flat_ar), use_container_width=True, hide_index=True)

    export_preview = build_export_df(filter_products_reports_activity(preview_source))
    st.caption("تصدير الحالة المعروضة أدناه (سلع بها نشاط فقط: مطلوب > 0 أو متوفر > 0).")
    st.download_button(
        "📄 تصدير PDF A4 (المعاينة)",
        data=build_pdf_bytes(export_preview, exported_by=str(st.session_state.get("username") or "").strip() or None),
        file_name="preview_inventory_a4.pdf",
        mime="application/pdf",
        key="preview_export_pdf",
    )
    st.download_button(
        "📊 تصدير Excel (المعاينة)",
        data=export_excel_bytes(export_preview),
        file_name="preview_inventory.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="preview_export_xlsx",
    )


def render_master_dashboard(products_df: pd.DataFrame):
    """لوحة الخلفة: المدير يرى جميع المهام النشطة؛ غير المدير يرى سلع الشفت الحالي المسندة لمعرّفه."""
    role = (st.session_state.get("role") or "").strip()
    current_user_id = str(st.session_state.get("user_id") or "").strip()
    current_username = st.session_state.get("username") or "مستخدم"
    is_admin = role.lower() == "admin"

    users = get_users_for_assign()
    assignable_roles = ("baker", "deptmanager")
    bakers = [u for u in users if str(u.get("role", "")).lower() in assignable_roles]
    baker_map = {str(u["id"]): u.get("username", "") for u in bakers}

    df = products_df.copy()
    shift_info = baghdad_shift_cycle_info()
    active_shift_name = str(shift_info.get("shift_name") or "").strip()
    active_baker_col = baker_column_for_shift_name(active_shift_name)
    if active_baker_col not in df.columns:
        fallback_col = "assigned_baker_id" if "assigned_baker_id" in df.columns else None
        df[active_baker_col] = df[fallback_col] if fallback_col else None

    mine = df[df[active_baker_col].astype(str) == current_user_id].copy()
    role_lc = role.strip().lower()
    is_baker_role = role_lc == "baker"
    can_edit_request = is_admin
    can_edit_unit = is_admin

    st.header("👨‍🍳 شاشة الخلفة")
    if is_admin:
        st.markdown(f"### عرض موحد للمدير — **{current_username}**")
        st.caption("واجهة المنسدلات نفسها الخاصة بالخلفة، مع صلاحية المدير لتعديل جميع الحقول.")
    else:
        st.markdown(f"### مسؤوليتك الحالية — **{current_username}**")
        st.caption(
            f"تُعرض السلع المربوطة بحسابك في عمود **{active_shift_name}** فقط. على شاشة الخلفة يظهر **فقط** ما طُلب "
            "إنتاجه (المطلوب > 0). الحفظ دفعة واحدة مع تحقق من الربط."
        )

    if not current_user_id:
        st.error("تعذر تحديد معرّف المستخدم. أعد تسجيل الدخول.")
        return

    if is_admin:
        source_base = df[(df["current_qty"].fillna(0) > 0) | (df["request_qty"].fillna(0) > 0)].copy()
        if source_base.empty:
            st.info("لا توجد مهام فعّالة للخلفة حالياً.")
            return
    else:
        if mine.empty:
            st.info("لا توجد سلع مسندة إلى حسابك. اطلب من المدير ربط السلع بك من «إدارة السلع».")
            return
        _rq_vis = pd.to_numeric(mine["request_qty"], errors="coerce").fillna(0)
        source_base = mine[_rq_vis > 0].copy()
        if source_base.empty:
            st.info(
                "لا توجد طلبات إنتاج نشطة لك حالياً (المطلوب = 0). ستظهر السلع هنا فور أن يضع مسؤول القسم كمية في «المطلوب» من شاشة الجرد."
            )
            return

    _si_live = shift_info
    st.caption(f"**الشفت الحالي (بغداد):** {_si_live['label']}")

    if not source_base.empty:
        cur_ck = _si_live["cycle_key"]
        if "shift_cycle_key" in source_base.columns:
            sk = source_base["shift_cycle_key"].apply(lambda x: str(x or "").strip())
            source_base = source_base[sk == cur_ck].copy()
        else:
            source_base = source_base.iloc[0:0].copy()
        if source_base.empty:
            st.info(
                "لا توجد مهام مسجّلة في **الشفت الحالي** بعد. بعد تحديث الجرد أو الطلب من شاشة الجرد في هذا الشفت ستظهر السلع المرتبطة بحسابك هنا."
            )
            return

    section_vals = [str(x).strip() for x in source_base.get("section_name", pd.Series([])).dropna().unique().tolist() if str(x).strip()]
    if len(section_vals) == 1:
        st.info(f"**القسم:** {section_vals[0]} — **{len(source_base)}** سلعة.")
    else:
        st.info(f"السلع موزعة على أكثر من قسم — **{len(source_base)}** سلعة إجمالاً.")

    allowed_ids = {str(x) for x in source_base["id"].tolist()}
    if is_baker_role:
        st.caption("**الاسم/الوحدة/المطلوب/المتوفر** للعرض فقط. عدّل **حالة الإنتاج** (تُحفظ فوراً عند التغيير) أو اضغط **حفظ** إن وُجدت تغييرات أخرى مسموحة.")
    elif is_admin:
        st.caption("واجهة موحدة: يمكنك تعديل **الوحدة** و**المطلوب** و**المتوفر** إضافة إلى **الحالة** و**الملاحظات**.")
    else:
        st.caption("يمكنك تعديل **المتوفر** و**الحالة** و**الملاحظات**.")

    search = st.text_input("بحث عن سلعة", key="baker_dashboard_search")
    source = source_base.copy()
    if search:
        source = source_base[source_base["name"].astype(str).str.contains(search, regex=False, na=False)].copy()

    if source.empty:
        st.info("لا توجد نتائج للبحث.")
        return

    source = source.reset_index(drop=True)
    if "section_name" not in source.columns:
        source["section_name"] = "عام"
    if "production_status" not in source.columns:
        source["production_status"] = "بانتظار الإنتاج"

    _baker_cards_init_session(
        source,
        is_baker_role=is_baker_role,
        can_edit_request=can_edit_request,
        can_edit_unit=can_edit_unit,
    )
    if is_baker_role:
        st.caption(
            "قائمة منسدلة لكل سلعة — البطاقة تقرأ **المطلوب** و**المتوفر** مباشرة من قاعدة البيانات بدون أي حسابات مشتقة. "
            "**حالة الإنتاج** تُحدَّث فوراً وتصل للمدير ومسؤول القسم."
        )
    else:
        st.caption(
            "قائمة منسدلة لكل سلعة — مرّر عمودياً فقط. **الملاحظات** (للمسؤول/المدير) تُحفظ مع الجرد؛ **حالة الإنتاج** تُحدَّث عند الحفظ أو فوراً للخلفة."
        )

    st.markdown(
        """
        <style>
        div[data-testid="stExpander"] details > summary {
            direction: rtl;
            text-align: right;
            font-size: 0.95rem;
        }
        div[data-testid="stExpander"] details > summary p {
            direction: rtl;
            text-align: right;
            margin: 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    for i in range(len(source)):
        row = source.iloc[i]
        pid = str(row["id"])
        name_plain = str(row.get("name", "") or "").replace("\n", " ").strip()
        section_plain = str(row.get("section_name", "") or "عام").replace("\n", " ").strip() or "عام"
        raw_u = str(row.get("unit_val") or "قطعة").strip() or "قطعة"
        rq0 = int(pd.to_numeric(row.get("request_qty"), errors="coerce") or 0)
        cq0 = int(pd.to_numeric(row.get("current_qty"), errors="coerce") or 0)
        scoped_cycle_log = filter_recommendation_log_for_cycle(
            row.get("recommendation_log"),
            _si_live["cycle_key"],
        )
        scoped_cycle_entries = [dict(e) for e in coerce_recommendation_log(scoped_cycle_log) if not _is_ghost_ui_log_entry(e)]
        scoped_cycle_entries.sort(key=lambda e: recommendation_log_entry_dt(e).timestamp() if recommendation_log_entry_dt(e) else 0.0)
        sender_plain = str(row.get("last_updated_by") or "").strip()

        current_for_short = cq0 if is_baker_role else int(st.session_state.get(f"bk_cur_{pid}", cq0))
        request_for_short = int(st.session_state.get(f"bk_req_{pid}", rq0)) if can_edit_request else rq0
        row_locked = should_lock_row_for_shift(
            row.get("shift_cycle_key"),
            row.get("last_updated_at"),
            current_cycle_key=_si_live["cycle_key"],
            is_admin=is_admin,
            minutes=10,
        )
        exp_lbl = f"{name_plain} — {section_plain}  •  المطلوب: {request_for_short} | المتوفر: {current_for_short}"
        with st.expander(exp_lbl, expanded=False):
            if row_locked and not is_baker_role:
                st.caption("🔒 انتهت صلاحية التعديل (10 دقائق)")
            if scoped_cycle_entries:
                st.caption("طلبات السلعة (كل طلب في بطاقة مستقلة)")
                for idx_log, log_entry in enumerate(scoped_cycle_entries):
                    req_card = _request_qty_or_none(log_entry.get("request_qty"))
                    if req_card is None:
                        req_card = int(rq0)
                    cur_card = _request_qty_or_none(log_entry.get("current_qty"))
                    if cur_card is None:
                        cur_card = int(cq0)
                    unit_card = str(log_entry.get("unit_val") or log_entry.get("unit") or raw_u).strip() or "—"
                    note_card = str(log_entry.get("notes") or "").strip() or "—"
                    sender_card = str(log_entry.get("by") or sender_plain).strip() or "—"
                    time_card = format_baghdad_time(log_entry.get("at") or log_entry.get("timestamp"))
                    card_title = "الطلب الأول" if idx_log == 0 else f"طلب جديد #{idx_log + 1}"
                    with st.container(border=True):
                        st.markdown(f"**{card_title}**")
                        col_log_req, col_log_cur, col_log_meta = st.columns([1.25, 1, 1], gap="small")
                        with col_log_req:
                            st.markdown(f"### 🎯 المطلوب: **{req_card}**")
                        with col_log_cur:
                            st.markdown(f"#### 📦 المتوفر: {cur_card}")
                            st.markdown(f"**الوحدة:** {html.escape(unit_card)}", unsafe_allow_html=True)
                        with col_log_meta:
                            st.markdown(f"👤 **بواسطة:** {html.escape(sender_card)}", unsafe_allow_html=True)
                            st.markdown(f"🕒 **الوقت:** {html.escape(str(time_card))}", unsafe_allow_html=True)
                        st.markdown(
                            f'<div dir="rtl" style="text-align:right;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:0.5rem 0.75rem;margin-top:0.35rem">'
                            f'📝 <strong>ملاحظة:</strong> {html.escape(note_card)}'
                            f"</div>",
                            unsafe_allow_html=True,
                        )
            else:
                st.info("لا يوجد سجل طلبات لهذه السلعة بعد.")
            st.caption("تعديل الكميات/الملاحظات يتم على السجل الحالي فقط (بدون إنشاء بطاقة إضافية).")
            if can_edit_request:
                st.number_input(
                    "المطلوب",
                    min_value=0,
                    value=int(rq0),
                    step=1,
                    key=f"bk_req_{pid}",
                    disabled=row_locked,
                )
            if not is_baker_role:
                st.number_input(
                    "المتوفر",
                    min_value=0,
                    value=int(st.session_state.get(f"bk_cur_{pid}", cq0)),
                    step=1,
                    key=f"bk_cur_{pid}",
                    disabled=row_locked,
                )
                if can_edit_unit:
                    not_in_list = bool(raw_u) and raw_u not in INVENTORY_UNIT_OPTIONS
                    default_ix = (
                        INVENTORY_UNIT_OPTIONS.index("مخصص")
                        if not_in_list
                        else (
                            INVENTORY_UNIT_OPTIONS.index(raw_u)
                            if raw_u in INVENTORY_UNIT_OPTIONS
                            else INVENTORY_UNIT_OPTIONS.index("قطعة")
                        )
                    )
                    st.selectbox(
                        "الوحدة",
                        INVENTORY_UNIT_OPTIONS,
                        index=default_ix,
                        key=f"bk_unit_{pid}",
                        disabled=row_locked,
                    )
                    if str(st.session_state.get(f"bk_unit_{pid}", "")) == "مخصص":
                        st.text_input(
                            "وحدة مخصصة",
                            key=f"bk_uc_{pid}",
                            placeholder="اكتب الوحدة",
                            disabled=row_locked,
                        )
            if not is_baker_role:
                st.text_input("ملاحظات", key=f"bk_notes_{pid}", placeholder="اختياري", disabled=row_locked)
            # حالة الإنتاج تخص السلعة ككل، لذلك تُعرض مرة واحدة أسفل السجل.
            if is_baker_role:
                st.selectbox(
                    "حالة الإنتاج",
                    PRODUCTION_STATUS_OPTIONS,
                    key=f"bk_status_{pid}",
                    help="تحديث فوري: يُحفظ في قاعدة البيانات مباشرة عند التغيير",
                    disabled=False,
                )
                selected_status = str(st.session_state.get(f"bk_status_{pid}", "بانتظار الإنتاج") or "بانتظار الإنتاج").strip()
                old_status = str(row.get("production_status") or "بانتظار الإنتاج").strip() or "بانتظار الإنتاج"
                if selected_status != old_status:
                    ok_st, exc_st = update_single_product_status(
                        pid,
                        selected_status,
                        assigned_baker_id=current_user_id,
                        updated_by=current_username,
                    )
                    if not ok_st:
                        if exc_st is not None and is_network_transport_error(exc_st):
                            st.warning("يرجى التحقق من اتصال الإنترنت.")
                        else:
                            st.error(f"تعذر تحديث حالة الإنتاج: {exc_st}")
                    else:
                        push_notification(
                            f"قام {current_username} بتحديث حالة الإنتاج للسلعة: {name_plain}",
                            target_role=None,
                        )
                        st.success("تم تحديث حالة الإنتاج — وصل التحديث إلى المدير ومسؤول القسم.")
                        play_success_beep()
                        clear_cache_and_rerun()
            else:
                st.selectbox(
                    "حالة الإنتاج",
                    PRODUCTION_STATUS_OPTIONS,
                    key=f"bk_status_{pid}",
                    help="لإبلاغ المسؤول بتقدم التحضير",
                    disabled=row_locked,
                )
            if row_locked and (can_edit_request or role_lc == "deptmanager"):
                st.caption("يمكنك إنشاء توصية جديدة لإعادة فتح هذه السلعة لمدة 10 دقائق.")
                if st.button("➕ إنشاء توصية جديدة", key=f"bk_new_reco_{pid}"):
                    # تصفير حقول الإدخال قبل فتح نافذة التعديل حتى لا ترث قيم جلسة سابقة.
                    clear_baker_dashboard_session_widgets_for_ids([pid])
                    si_new = baghdad_shift_cycle_info()
                    now_baghdad = baghdad_iso_now()
                    patch = {
                        "notes": "",
                        "last_updated_by": current_username,
                        "last_updated_at": now_baghdad,
                        "shift_name": si_new["shift_name"],
                        "shift_cycle_key": si_new["cycle_key"],
                    }
                    ok_n, _, exc_n = supabase_with_retry(
                        lambda p=patch, _pid=pid: supabase_client.table("products").update(p).eq("id", _pid).execute()
                    )
                    if not ok_n:
                        if exc_n is not None and is_network_transport_error(exc_n):
                            st.warning("يرجى التحقق من اتصال الإنترنت.")
                        else:
                            st.error(f"تعذر إنشاء توصية جديدة: {exc_n}")
                    else:
                        invalidate_products_cache_after_mutation()
                        clear_baker_dashboard_session_widgets_for_ids([pid])
                        st.success("تم فتح تحديث جديد لهذه السلعة.")
                        settle_write_then_refresh()

    if st.button("✅ حفظ جرد قسمي", use_container_width=True, key="baker_dash_save"):
        updates = _baker_cards_collect_updates(
            source,
            is_baker_role=is_baker_role,
            can_edit_request=can_edit_request,
            can_edit_unit=can_edit_unit,
        )
        updates = [u for u in updates if str(u["id"]) in allowed_ids]
        if not is_admin and not is_baker_role:
            locked_ids = {
                str(source.iloc[i]["id"])
                for i in range(len(source))
                if should_lock_row_for_shift(
                    source.iloc[i].get("shift_cycle_key"),
                    source.iloc[i].get("last_updated_at"),
                    current_cycle_key=_si_live["cycle_key"],
                    is_admin=False,
                    minutes=10,
                )
            }
            if locked_ids:
                updates = [u for u in updates if str(u.get("id")) not in locked_ids]
        if not updates:
            if is_baker_role:
                st.info("لا توجد تغييرات للحفظ من زر الحفظ. **حالة الإنتاج** تُحدَّث تلقائياً عند تغييرها من القائمة أعلاه.")
            else:
                st.warning("لا توجد كميات للحفظ (أدخل متوفراً أو مطلوباً أكبر من صفر).")
            return

        upsert_kwargs = {
            "preserve_request_qty_from_db": is_baker_role,
            "updated_by": current_username,
        }
        # المدير يرى نفس الشاشة الموحدة لكن بدون تقييد الخلفة.
        if not is_admin:
            upsert_kwargs["assigned_baker_id"] = current_user_id
        ok_up, exc_up = batch_upsert_product_quantities(
            updates,
            **upsert_kwargs,
        )
        if not ok_up:
            if isinstance(exc_up, PermissionError):
                st.error(str(exc_up))
            elif exc_up is not None and is_network_transport_error(exc_up):
                st.warning("يرجى التحقق من اتصال الإنترنت.")
            else:
                st.error(f"تعذر حفظ الجرد: {exc_up}")
            return

        for u in updates:
            upid = str(u["id"])
            for prefix in ("bk_cur_", "bk_notes_", "bk_status_", "bk_req_", "bk_unit_", "bk_uc_"):
                k = f"{prefix}{upid}"
                if k in st.session_state:
                    del st.session_state[k]

        push_notification(f"تم حفظ جرد الخلفة بواسطة: {current_username}", target_role=None)
        st.success("تم الحفظ بنجاح — وصل التحديث إلى المدير ومسؤول القسم.")
        play_success_beep()
        settle_write_then_refresh()


def render_admin(products_df: pd.DataFrame):
    st.header("🔐 إعدادات النظام")
    gate = st.text_input("كلمة مرور إعدادات النظام", type="password")
    if gate != "19488491":
        st.info("إعدادات النظام محمية بكلمة المرور.")
        return

    if st.session_state.pop("_admin_flash_role_saved", False):
        st.success("تم تغيير دور المستخدم بنجاح.")

    def _is_factory_reset_manager() -> bool:
        """مسح شامل للمدير فقط — في الجلسة يُخزَّن رمز الدور من Supabase عادةً «Admin» (مدير النظام)."""
        r = str(st.session_state.get("role") or "").strip()
        return r == "Admin" or r == "مدير"

    def _run_factory_reset() -> tuple[bool, str]:
        if not _is_factory_reset_manager():
            return False, "غير مصرّح بتنفيذ المسح الشامل."
        all_products_marker = "00000000-0000-0000-0000-000000000000"

        ok_log, _, exc_log = supabase_with_retry(
            lambda: supabase_client.table("products")
            .update({"recommendation_log": []})
            .neq("id", all_products_marker)
            .execute()
        )
        if not ok_log:
            return False, f"تعذر تفريغ recommendation_log من جدول المنتجات: {exc_log}"

        ok_arc, _, exc_arc = supabase_with_retry(
            lambda: supabase_client.table("audit_archive")
            .delete()
            .neq("archive_id", all_products_marker)
            .execute()
        )
        if not ok_arc:
            return False, f"تعذر تفريغ جدول الأرشيف audit_archive: {exc_arc}"

        ok_inv, _, exc_inv = supabase_with_retry(
            lambda: supabase_client.table("products")
            .update(
                {
                    "request_qty": 0,
                    "current_qty": 0,
                    "notes": "",
                    "production_status": "بانتظار الإنتاج",
                }
            )
            .neq("id", all_products_marker)
            .execute()
        )
        if not ok_inv:
            return False, f"تعذر تصفير الجرد في جدول products: {exc_inv}"

        return True, ""

    _mgr_reset = _is_factory_reset_manager()
    _tab_labels = ["المستخدمون", "السلع", "التقارير"] + (
        ["الإعدادات المتقدمة"] if _mgr_reset else []
    )
    tabs = st.tabs(_tab_labels)
    with tabs[0]:
        if user_has("can_add_users"):
            create_user_form()
            manage_existing_users_section()
        else:
            st.warning("لا تملك صلاحية إضافة مستخدمين.")
    with tabs[1]:
        if not user_has("can_edit_products"):
            st.warning("لا تملك صلاحية تعديل السلع.")
        else:
            st.subheader("إضافة سلعة جديدة")
            users = get_users_for_assign()
            # مهام الإنتاج للخلفة فقط — استبعاد Admin ومسؤول القسم وأي دور غير Baker
            baker_users = [u for u in users if str(u.get("role", "")).strip().lower() == "baker"]
            baker_options = {u["username"]: u["id"] for u in baker_users}
            with st.form("add_product_form"):
                name = st.text_input("اسم السلعة")
                unit_choice = st.selectbox("وحدة القياس", PRODUCT_UNIT_OPTIONS)
                custom_unit = ""
                if unit_choice == "مخصص":
                    custom_unit = st.text_input("اكتب وحدة القياس المخصصة")
                section = st.text_input("القسم", value="عام")
                notes = st.text_input("ملاحظات السلعة", value="")
                st.markdown("**توزيع الخلفات حسب الشفت** (دور خلفة الإنتاج فقط)")
                owner_name_morning = st.selectbox(
                    "☀️ اختر خلفة الشفت الصباحي",
                    list(baker_options.keys()) if baker_options else [""],
                    key="add_product_morning_baker",
                )
                owner_name_evening = st.selectbox(
                    "🌆 اختر خلفة الشفت المسائي",
                    list(baker_options.keys()) if baker_options else [""],
                    key="add_product_evening_baker",
                )
                owner_name_night = st.selectbox(
                    "🌙 اختر خلفة الشفت الليلي",
                    list(baker_options.keys()) if baker_options else [""],
                    key="add_product_night_baker",
                )
                if st.form_submit_button("حفظ"):
                    if not name.strip():
                        st.error("يرجى إدخال اسم السلعة.")
                    else:
                        final_unit = custom_unit.strip() if unit_choice == "مخصص" else unit_choice
                        if not final_unit:
                            st.error("يرجى إدخال وحدة القياس المخصصة.")
                            return
                        _si_new = baghdad_shift_cycle_info()
                        ins_row = {
                            "name": name.strip(),
                            "unit_val": final_unit,
                            "section_name": section.strip() or "عام",
                            "assigned_baker_id": None,
                            "morning_baker_id": baker_options.get(owner_name_morning),
                            "evening_baker_id": baker_options.get(owner_name_evening),
                            "night_baker_id": baker_options.get(owner_name_night),
                            "current_qty": 0,
                            "request_qty": 0,
                            "notes": notes.strip(),
                            "production_status": "بانتظار الإنتاج",
                            "last_updated_by": str(st.session_state.get("username") or ""),
                            "last_updated_at": baghdad_iso_now(),
                            "shift_name": _si_new["shift_name"],
                            "shift_cycle_key": _si_new["cycle_key"],
                        }
                        ok_pi, _, exc_pi = supabase_with_retry(
                            lambda r=ins_row: supabase_client.table("products").insert(r).execute()
                        )
                        if not ok_pi:
                            if exc_pi is not None and is_network_transport_error(exc_pi):
                                st.warning("يرجى التحقق من اتصال الإنترنت.")
                            else:
                                st.error(f"تعذر إضافة السلعة: {exc_pi}")
                        else:
                            invalidate_products_cache_after_mutation()
                            st.success("تمت إضافة السلعة.")
                            push_notification(f"تمت إضافة سلعة جديدة: {name.strip()}", target_role=None)
                            clear_cache_and_rerun()
            with st.expander("✏️ تعديل ربط الخلفات حسب الشفت"):
                if products_df.empty:
                    st.info("لا توجد سلع للتعديل حالياً.")
                else:
                    df_edit = products_df.sort_values("name").reset_index(drop=True)
                    idx_edit = st.selectbox(
                        "اختر السلعة للتعديل",
                        range(len(df_edit)),
                        format_func=lambda i: str(df_edit.iloc[i]["name"]),
                        key="edit_product_shift_assign_pick",
                    )
                    prow = df_edit.iloc[int(idx_edit)]
                    default_m = str(prow.get("morning_baker_id") or prow.get("assigned_baker_id") or "").strip()
                    default_e = str(prow.get("evening_baker_id") or prow.get("assigned_baker_id") or "").strip()
                    default_n = str(prow.get("night_baker_id") or prow.get("assigned_baker_id") or "").strip()
                    baker_names = list(baker_options.keys()) if baker_options else []

                    def _name_from_id(uid: str) -> str:
                        for nm, bid in baker_options.items():
                            if str(bid) == uid:
                                return nm
                        return baker_names[0] if baker_names else ""

                    pick_m = st.selectbox(
                        "☀️ خلفة الشفت الصباحي",
                        baker_names if baker_names else [""],
                        index=(baker_names.index(_name_from_id(default_m)) if baker_names else 0),
                        key="edit_product_morning_baker",
                    )
                    pick_e = st.selectbox(
                        "🌆 خلفة الشفت المسائي",
                        baker_names if baker_names else [""],
                        index=(baker_names.index(_name_from_id(default_e)) if baker_names else 0),
                        key="edit_product_evening_baker",
                    )
                    pick_n = st.selectbox(
                        "🌙 خلفة الشفت الليلي",
                        baker_names if baker_names else [""],
                        index=(baker_names.index(_name_from_id(default_n)) if baker_names else 0),
                        key="edit_product_night_baker",
                    )
                    if st.button("💾 حفظ توزيع الشفتات", key="edit_product_shift_assign_save"):
                        pid = str(prow.get("id") or "").strip()
                        patch = {
                            "morning_baker_id": baker_options.get(pick_m),
                            "evening_baker_id": baker_options.get(pick_e),
                            "night_baker_id": baker_options.get(pick_n),
                        }
                        ok_ep, _, exc_ep = supabase_with_retry(
                            lambda p=patch, _pid=pid: supabase_client.table("products").update(p).eq("id", _pid).execute()
                        )
                        if not ok_ep:
                            if exc_ep is not None and is_network_transport_error(exc_ep):
                                st.warning("يرجى التحقق من اتصال الإنترنت.")
                            else:
                                st.error(f"تعذر تحديث ربط الشفتات: {exc_ep}")
                        else:
                            invalidate_products_cache_after_mutation()
                            st.success("تم تحديث ربط الخلفات للشفتات بنجاح.")
                            clear_cache_and_rerun()
            with st.expander("🗑️ حذف سلعة"):
                if products_df.empty:
                    st.info("لا توجد سلع للحذف حالياً.")
                else:
                    df_del = products_df.sort_values("name").reset_index(drop=True)
                    pick_idx = st.selectbox(
                        "اختر السلعة",
                        range(len(df_del)),
                        format_func=lambda i: str(df_del.iloc[i]["name"]),
                        key="delete_product_select",
                    )
                    if st.button("حذف السلعة", key="delete_product_btn"):
                        pid = str(df_del.iloc[int(pick_idx)]["id"])
                        pname = str(df_del.iloc[int(pick_idx)]["name"])
                        ok_del, _, exc_del = supabase_with_retry(
                            lambda: supabase_client.table("products").delete().eq("id", pid).execute()
                        )
                        if not ok_del:
                            if exc_del is not None and is_network_transport_error(exc_del):
                                st.warning("يرجى التحقق من اتصال الإنترنت.")
                            else:
                                st.error(f"تعذر حذف السلعة: {exc_del}")
                        else:
                            invalidate_products_cache_after_mutation()
                            st.success(f"تم حذف السلعة «{pname}» نهائياً من النظام.")
                            clear_cache_and_rerun()
    with tabs[2]:
        reports_source = filter_products_reports_activity(products_df)
        shift_opts = ["الكل", "صباحي", "مسائي", "ليلي"]
        shift_f = st.selectbox(
            "تصفية التقارير حسب الشفت",
            shift_opts,
            key="admin_reports_shift_filter",
        )
        if shift_f != "الكل" and not reports_source.empty and "shift_name" in reports_source.columns:
            reports_source = reports_source[
                reports_source["shift_name"].astype(str).str.strip() == shift_f
            ].copy()
        display = build_export_df(reports_source)
        st.dataframe(display, use_container_width=True)
        st.download_button(
            "📄 تصدير PDF A4",
            data=build_pdf_bytes(display, exported_by=str(st.session_state.get("username") or "").strip() or None),
            file_name="inventory_a4.pdf",
            mime="application/pdf",
        )
        st.download_button(
            "📊 تصدير Excel",
            data=export_excel_bytes(display),
            file_name="inventory.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.divider()
        st.subheader("🗂️ التقارير التاريخية (الأرشيف)")
        ok_hr, hres, exc_hr = supabase_with_retry(
            lambda: supabase_client.table("audit_archive").select("*").order("archive_date", desc=True).execute()
        )
        if not ok_hr or hres is None:
            if exc_hr is not None and is_network_transport_error(exc_hr):
                st.warning("يرجى التحقق من اتصال الإنترنت. تعذر جلب بيانات الأرشيف للتقارير.")
            else:
                st.error(f"تعذر جلب بيانات الأرشيف للتقارير: {exc_hr}")
        else:
            all_archive = hres.data or []
            available_dates: list[str] = []
            for r in all_archive:
                dk = archive_date_key_baghdad(r.get("archive_date"))
                if dk:
                    available_dates.append(dk)
            available_dates = sorted(set(available_dates), reverse=True)
            if not available_dates:
                st.info("لا توجد بيانات مؤرشفة حتى الآن.")
            else:
                c_date, c_shift = st.columns([1, 1])
                with c_date:
                    picked_date = st.selectbox("التاريخ", available_dates, key="admin_history_date")
                with c_shift:
                    picked_shift = st.selectbox(
                        "الشفت",
                        ["الكل", "صباحي", "مسائي", "ليلي"],
                        key="admin_history_shift",
                    )

                history_rows = [r for r in all_archive if archive_date_key_baghdad(r.get("archive_date")) == picked_date]
                if picked_shift != "الكل":
                    history_rows = [r for r in history_rows if str(r.get("shift_name") or "").strip() == picked_shift]

                history_display = build_archive_display_df(history_rows, products_df)
                st.dataframe(history_display, use_container_width=True, height=420)
                st.download_button(
                    "📄 تصدير PDF للتقرير التاريخي",
                    data=build_pdf_bytes(
                        history_display,
                        exported_by=str(st.session_state.get("username") or "").strip() or None,
                    ),
                    file_name=f"historical_report_{picked_date}.pdf",
                    mime="application/pdf",
                    key="admin_history_pdf",
                )
                st.download_button(
                    "📊 تصدير Excel للتقرير التاريخي",
                    data=export_excel_bytes(history_display),
                    file_name=f"historical_report_{picked_date}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="admin_history_xlsx",
                )
    if _mgr_reset:
        with tabs[3]:
            st.subheader("⚙️ الإعدادات المتقدمة")
            with st.expander("⚠️ منطقة الخطر - مسح بيانات النظام", expanded=False):
                confirmation_phrase = "أنا متأكد من مسح البيانات"
                typed = st.text_input(
                    "اكتب جملة التأكيد التالية لتفعيل الزر:",
                    key="factory_reset_confirmation_phrase",
                    placeholder=confirmation_phrase,
                )
                typed_norm = str(typed or "").strip()
                confirmed = typed_norm == confirmation_phrase
                if not confirmed:
                    st.caption("لن يتم تفعيل الزر إلا بعد كتابة الجملة كما هي تماماً.")

                if st.button(
                    "🧨 تنفيذ مسح شامل (Factory Reset)",
                    type="primary",
                    disabled=not confirmed,
                    key="factory_reset_execute_btn",
                ):
                    with st.spinner("جاري مسح السجلات والأرشيف وتصفير الجرد..."):
                        ok_reset, err_reset = _run_factory_reset()
                    if not ok_reset:
                        err_s = str(err_reset or "")
                        if any(
                            n in err_s.lower()
                            for n in ("timeout", "timed out", "connection", "socket", "ssl", "network")
                        ):
                            st.warning("يرجى التحقق من اتصال الإنترنت.")
                        else:
                            st.error(err_s)
                    else:
                        actor = str(st.session_state.get("username") or "").strip() or "مدير"
                        ts_line = format_baghdad_time(baghdad_iso_now())
                        push_notification(
                            f"تنبيه إداري: مسح شامل لبيانات النظام (Factory Reset) — نفّذه «{actor}» في {ts_line}.",
                            target_role=None,
                        )
                        st.success("تم تنفيذ المسح الشامل بنجاح. النظام الآن بحالة نظيفة.")
                        st.cache_data.clear()
                        st.rerun()


ensure_required_schema()
ensure_seed_users()
migrate_legacy_warehouse_roles()
ensure_archive_cycle()
ensure_shift_boundary()

if not st.session_state.get("is_logged_in"):
    login_screen()
    st.stop()

# تحديث الصلاحيات في الجلسة إن وُجدت مفاتيح قديمة في JSON دون إجبار إعادة الدخول
st.session_state["permissions"] = normalize_permissions_for_session(
    st.session_state.get("permissions", {}) or {}, str(st.session_state.get("role") or "")
)

st.sidebar.title("🏢 نظام باب الآغا")
_rc_sess = str(st.session_state.get("role") or "")
_role_disp = ROLE_CODE_TO_LABEL.get(_rc_sess, LEGACY_ROLE_TO_LABEL.get(_rc_sess, _rc_sess))
st.sidebar.success(f"المستخدم: {st.session_state.get('username')} ({_role_disp})")
if st.sidebar.button("تسجيل خروج"):
    st.session_state.clear()
    st.rerun()

products_all = load_products()
products = filter_products_for_session(products_all)

visible = build_sidebar_menu_labels()
if not visible:
    st.sidebar.error("لا توجد صفحات مسموحة لحسابك. اطلب من المدير ضبط صلاحيات الشاشات في JSON.")
    st.stop()
menu = st.sidebar.radio("القائمة", visible)

now = get_baghdad_now()
_h12, _mm, _ampm_now = _baghdad_12h_display_parts(now)
_shift_ui = baghdad_shift_cycle_info(now)
st.caption(
    f"الوقت الرسمي (بغداد): {now.strftime('%Y-%m-%d')} {_h12}:{_mm}:{now.strftime('%S')} {_ampm_now} — {_shift_ui['label']}"
)

if menu == "🏠 الرئيسية":
    st.title("نظام إدارة جرد باب الآغا")
    st.write("النظام يعمل بتوقيت بغداد، مع أرشفة آلية 6 صباحاً، وحفظ الجرد على دفعات لتقليل الضغط على الشبكة.")
elif menu == "📦 الجرد":
    render_inventory(products)
elif menu == "🖼️ المعاينة":
    render_preview(products)
elif menu == "👨‍🍳 شاشة الخلفة":
    render_master_dashboard(products_all)
elif menu == "⚙️ إعدادات النظام":
    render_admin(products_all)
elif menu == "🔔 الإشعارات":
    notifications_page()