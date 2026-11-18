


import base64
import calendar
import copy
import io
import json
import re
from datetime import date, datetime, time

import pandas as pd
import streamlit as st

MODEL = "claude-sonnet-5-5"  # 写真の読み取りに使うモデル名（変更可）

st.set_page_config(page_title="次の一手（仮）", layout="centered")
st.markdown(
    """<style>
table.mc{width:100%;text-align:center;border-collapse:separate;border-spacing:3px}
table.mc th{font-weight:400;font-size:13px;opacity:.6}
table.mc td{border-radius:8px;padding:4px 0;line-height:1.3}
table.mc td.sel{background:rgba(74,144,217,.28)}
table.mc td.td{font-weight:700}
table.mc small{color:#4a90d9;font-size:10px}
@keyframes qin{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
[class*="st-key-qbox"]{animation:qin .4s ease-out}
</style>""",
    unsafe_allow_html=True,
)

DAYS = ["月", "火", "水", "木", "金"]
WD = DAYS + ["土", "日"]
DEFAULT = {
    "tt": [[""] * 5 for _ in range(6)],  # 6限 × 月〜金
    "it": [""] * 5,                       # 曜日別の持ち物
    "base": "スマホ,PC,バッグ",            # 毎日の持ち物
    "pl": {},                             # 日付 -> 予定のリスト
    "ck": {},                             # 日付 -> 持ち物チェック
    "lg": [],                             # 完了ログ（空白の計測用）
    "td": [],                             # 時間なしのToDo
    "qa": {},                             # 生活リズムの回答
    "qdone": False,
}


def split(s):
    return [x.strip() for x in re.split(r"[,、\n]", s) if x.strip()]


def mins(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def hm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def tm(a, k, d):
    h, m = (a.get(k) or d).split(":")
    return time(int(h), int(m))


def fs(t):
    return t.strftime("%H:%M")


def ix(opts, v):
    return opts.index(v) if v in opts else 0


def hrs(bed, wake):
    return ((mins(wake) - mins(bed)) % 1440) / 60


def items_for(d):
    extra = split(S["it"][d.weekday()]) if d.weekday() < 5 else []
    return split(S["base"]) + extra


def month_html(sel):
    out = "<table class='mc'><tr>" + "".join(f"<th>{w}</th>" for w in "月火水木金土日") + "</tr>"
    for wk in calendar.Calendar(0).monthdatescalendar(sel.year, sel.month):
        out += "<tr>"
        for x in wk:
            if x.month != sel.month:
                out += "<td></td>"
                continue
            cls = "sel" if x == sel else ("td" if x == today else "")
            dot = "●" if S["pl"].get(x.isoformat()) else "&nbsp;"
            out += f"<td class='{cls}'>{x.day}<br><small>{dot}</small></td>"
        out += "</tr>"
    return out + "</table>"


# ---------- 空き枠の計算（コード側の担当。AIは使わない） ----------
def free_slots(d):
    a = S["qa"]
    wd = d.weekday() < 5
    p = "wd" if wd else "we"
    wake = mins(a.get(p + "_wake", "06:30" if wd else "08:00"))
    bed = mins(a.get(p + "_bed", "23:30"))
    end = bed if bed > wake else 1440  # 日付をまたぐ就寝は24:00で打ち切る
    busy = []
    if wd:
        busy.append((wake, mins(a.get("home", "17:30"))))  # 起床〜帰宅（朝の準備＋登校＋学校）
        if a.get("juku") and WD[d.weekday()] in a.get("juku_days", []):
            busy.append((mins(a.get("juku_s", "18:00")), mins(a.get("juku_home", "21:30"))))
    for k in ("dinner", "bath"):
        s = mins(a.get(k, "19:00" if k == "dinner" else "21:30"))
        busy.append((s, s + 30))
    for q in S["pl"].get(d.isoformat(), []):
        busy.append((mins(q["s"]), mins(q["e"])))
    out, cur = [], wake
    for s, e in sorted(busy):
        s = min(s, end)
        if s - cur >= 15:
            out.append((cur, s))
        cur = max(cur, e)
    if end - cur >= 15:
        out.append((cur, end))
    return out


def propose(d):
    """固定ルール版：未予定のToDoを、空き枠の先頭から30分ずつ割り当てる。"""
    iso = d.isoformat()
    sched = {q.get("t") for q in S["pl"].get(iso, [])}
    todo = [t for t in S["td"] if not t["d"] and t["id"] not in sched]
    now_m = m_now if d == today else 0
    slots = [(max(s, now_m), e) for s, e in free_slots(d) if e - max(s, now_m) >= 20]
    return slots, [(s, min(e, s + 30), t) for t, (s, e) in zip(todo, slots)]


# ---------- 質問（1画面1テーマ） ----------
def q_sleep(p):
    def f(a):
        c1, c2 = st.columns(2)
        return {
            p + "_bed": fs(c1.time_input("就寝", tm(a, p + "_bed", "23:30"), key=p + "b")),
            p + "_wake": fs(c2.time_input("起床", tm(a, p + "_wake", "06:30" if p == "wd" else "08:00"), key=p + "w")),
        }
    return f


def q_school(a):
    c1, c2 = st.columns(2)
    return {
        "leave": fs(c1.time_input("家を出る時刻", tm(a, "leave", "07:30"), key="lv")),
        "home": fs(c2.time_input("家に着く時刻（塾のない日）", tm(a, "home", "17:30"), key="hm")),
    }


def q_juku(a):
    yes = st.radio("塾に通っていますか", ["はい", "いいえ"], index=0 if a.get("juku") else 1, horizontal=True, key="jk") == "はい"
    out = {"juku": yes}
    if yes:
        out["juku_days"] = st.multiselect("曜日", WD, default=a.get("juku_days", []), key="jd")
        c1, c2, c3 = st.columns(3)
        out["juku_s"] = fs(c1.time_input("開始", tm(a, "juku_s", "18:00"), key="js"))
        out["juku_e"] = fs(c2.time_input("終了", tm(a, "juku_e", "21:00"), key="je"))
        out["juku_home"] = fs(c3.time_input("家に着く", tm(a, "juku_home", "21:30"), key="jh"))
    return out


AFTER = ["休憩（スマホ・動画）", "食事", "入浴", "勉強", "仮眠", "その他"]
MORN = ["朝食", "身支度", "スマホ", "勉強", "通学の準備", "ゆっくりする"]
STUCK = ["朝", "帰宅直後", "夕食後", "寝る前", "特にない"]


def q_after(a):
    return {"after": st.radio("家に着いて、最初にすること", AFTER, index=ix(AFTER, a.get("after")), key="af")}


def q_morn(a):
    return {"morning": st.multiselect("朝にすること（複数可）", MORN, default=a.get("morning", []), key="mo")}


def q_meal(a):
    c1, c2 = st.columns(2)
    return {
        "dinner": fs(c1.time_input("夕食を始める時刻", tm(a, "dinner", "19:00"), key="dn")),
        "bath": fs(c2.time_input("入浴を始める時刻", tm(a, "bath", "21:30"), key="ba")),
    }


def q_stuck(a):
    return {"stuck": st.radio("「次、何するんだっけ」となりやすい時間帯", STUCK, index=ix(STUCK, a.get("stuck")), key="sk")}


def q_goal(a):
    return {"goal_h": st.slider("平日に勉強にあてたい時間（時間）", 0.0, 6.0, float(a.get("goal_h", 2.0)), 0.5, key="gl")}


QS = [
    ("平日の睡眠", q_sleep("wd")), ("休日の睡眠", q_sleep("we")), ("登校と帰宅", q_school),
    ("塾", q_juku), ("帰宅後", q_after), ("朝", q_morn),
    ("夕食と入浴", q_meal), ("つまずきやすい時間", q_stuck), ("勉強時間", q_goal),
]
N = len(QS)


# ---------- 写真から時間割を読み取る（AIの担当） ----------
PROMPT = (
    "この画像は日本の高校の時間割です。月〜金、1〜6限の科目名を読み取り、JSONだけを返してください。"
    '形式: {"rows": [[月1限,火1限,水1限,木1限,金1限], ... 6行]}。'
    "読めないコマは空文字にし、科目名は短い表記にしてください。"
)


def api_key():
    try:
        return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        return None


def read_timetable(photo):
    import anthropic
    from PIL import Image, ImageOps

    im = ImageOps.exif_transpose(Image.open(photo)).convert("RGB")
    im.thumbnail((1600, 1600))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    img = base64.standard_b64encode(buf.getvalue()).decode()
    msg = anthropic.Anthropic(api_key=api_key()).messages.create(
        model=MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img}},
            {"type": "text", "text": PROMPT},
        ]}],
    )
    txt = "".join(b.text for b in msg.content if b.type == "text")
    rows = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))["rows"]  # 不正なら例外 → 呼び出し側で表示
    return [[str(x or "").strip() for x in (list(r) + [""] * 5)[:5]] for r in (rows + [[]] * 6)[:6]]


if "S" not in st.session_state:
    st.session_state.S = copy.deepcopy(DEFAULT)
S = st.session_state.S

now = datetime.now()
today = now.date()
tomorrow = date.fromordinal(today.toordinal() + 1)
m_now = now.hour * 60 + now.minute

tab_home, tab_cal, tab_tt = st.tabs(["ホーム", "カレンダー", "時間割"])

# ---------- ホーム ----------
with tab_home:
    # 質問ウィジェット
    qi = st.session_state.get("qi")
    if qi is not None:
        title, fn = QS[qi]
        left = N - qi - 1
        with st.container(border=True, key=f"qbox{qi}"):
            st.progress(qi / N, text=f"{qi + 1}/{N}　" + (f"あと{left}問です！" if left else "最後の質問です！"))
            st.subheader(title)
            vals = fn(S["qa"])
            c1, c2 = st.columns(2)
            if qi > 0 and c1.button("戻る"):
                S["qa"].update(vals)
                st.session_state.qi = qi - 1
                st.rerun()
            if c2.button("次へ" if left else "完了", type="primary"):
                S["qa"].update(vals)
                if left:
                    st.session_state.qi = qi + 1
                else:
                    S["qdone"] = True
                    st.session_state.qi = None
                    st.toast("回答ありがとう！空き時間を計算しました。")
                st.rerun()
        st.stop()

    with st.container(border=True):
        a = S["qa"]
        st.markdown("**生活リズム**")
        if not S["qdone"]:
            st.caption(f"{N}問・約1分。答えた内容から、空き時間を自動で計算します。")
        else:
            st.caption(f"睡眠　平日 {hrs(a['wd_bed'], a['wd_wake']):.1f}時間 / 休日 {hrs(a['we_bed'], a['we_wake']):.1f}時間")
            if a.get("juku"):
                st.caption(f"塾　{'・'.join(a.get('juku_days', []))}　{a['juku_s']}–{a['juku_e']}（帰宅 {a['juku_home']}）")
            st.caption(f"つまずきやすい時間帯: {a['stuck']}")
        if st.button("はじめる" if not S["qdone"] else "回答を修正", type="primary" if not S["qdone"] else "secondary"):
            st.session_state.qi = 0
            st.rerun()

    plans = sorted(S["pl"].get(today.isoformat(), []), key=lambda p: p["s"])
    todo_plans = [p for p in plans if not p["d"]]
    cur = next((p for p in todo_plans if mins(p["s"]) <= m_now < mins(p["e"])), None)
    nxt = next((p for p in todo_plans if mins(p["s"]) > m_now), None)
    undone = [t for t in S["td"] if not t["d"]]

    with st.container(border=True):
        st.caption("今")
        if cur:
            st.subheader(cur["x"])
            st.caption(f"{cur['s']}–{cur['e']}")
            if st.button("完了", type="primary"):
                cur["d"] = True
                for t in S["td"]:
                    if t["id"] == cur.get("t"):
                        t["d"] = True
                S["lg"].append({"ts": now.isoformat(timespec="seconds"), "day": today.isoformat(), "id": cur["id"]})
                st.rerun()
        else:
            st.subheader("空白")
            st.caption("次の一手を決めて動き出しましょう")
            if undone:
                st.write(f"候補: {undone[0]['x']}")

    with st.container(border=True):
        st.caption("次")
        if nxt:
            st.subheader(nxt["x"])
            st.caption(f"{nxt['s']}–{nxt['e']}")
        else:
            st.caption("今日の予定はこれで終わりです")

    if not S["pl"].get(tomorrow.isoformat()):
        st.warning("明日の予定がまだありません。今夜のうちに決めておくと、朝の空白が減ります。")

    with st.container(border=True):
        st.markdown("**今日の空き枠**")
        if not S["qdone"]:
            st.caption("質問に答えると、空き枠を自動で計算します。")
        else:
            slots, prop = propose(today)
            if not slots:
                st.caption("これから20分以上の空きはありません。")
            for s, e in slots:
                st.caption(f"{hm(s)}–{hm(e)}（{e - s}分）")
            if prop:
                st.markdown("**割り当て案**")
                for s, e, t in prop:
                    st.write(f"{hm(s)}–{hm(e)}　{t['x']}")
                if st.button("この案を予定に追加"):
                    base = int(datetime.now().timestamp() * 1000)
                    for i, (s, e, t) in enumerate(prop):
                        S["pl"].setdefault(today.isoformat(), []).append(
                            {"id": base + i, "s": hm(s), "e": hm(e), "x": t["x"], "d": False, "t": t["id"]}
                        )
                    st.rerun()
            elif slots:
                st.caption("やることを追加すると、空き枠に割り当てます。")

    with st.container(border=True):
        st.markdown("**やること（時間なし）**")
        for t in sorted(S["td"], key=lambda t: t["d"]):
            c1, c2 = st.columns([5, 1])
            v = c1.checkbox(t["x"], value=t["d"], key=f"td{t['id']}")
            if v != t["d"]:
                t["d"] = v
                st.rerun()
            if c2.button("削除", key=f"tdr{t['id']}"):
                S["td"] = [u for u in S["td"] if u["id"] != t["id"]]
                st.rerun()
        with st.form("addtd", clear_on_submit=True):
            x = st.text_input("追加", placeholder="Legend 例題3問")
            if st.form_submit_button("追加") and x.strip():
                S["td"].append({"id": int(datetime.now().timestamp() * 1000), "x": x.strip(), "d": False})
                st.rerun()

    with st.container(border=True):
        c1, c2 = st.columns([1, 1])
        c1.markdown("**持ち物**")
        pick = c2.radio("対象", ["今日", "明日"], index=1 if now.hour >= 17 else 0, horizontal=True, label_visibility="collapsed")
        target = tomorrow if pick == "明日" else today
        iso = target.isoformat()
        lst = items_for(target)
        if not lst:
            st.caption("時間割タブで持ち物を登録してください。")
        checks = S["ck"].setdefault(iso, {})
        for it in lst:
            v = st.checkbox(it, value=checks.get(it, False), key=f"ck-{iso}-{it}")
            if v != checks.get(it, False):
                checks[it] = v

# ---------- カレンダー ----------
with tab_cal:
    d = st.date_input("日付を選ぶ", value=today)
    iso = d.isoformat()
    st.markdown(f"**{d.year}年{d.month}月**")
    st.markdown(month_html(d), unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown(f"**{d.month}/{d.day} の予定**")
        day_plans = sorted(S["pl"].get(iso, []), key=lambda p: p["s"])
        if not day_plans:
            st.caption("まだありません。")
        for p in day_plans:
            c1, c2 = st.columns([4, 1])
            c1.write(f"{p['s']}–{p['e']}　{p['x']}" + ("　✓" if p["d"] else ""))
            if c2.button("削除", key=f"rm-{p['id']}"):
                S["pl"][iso] = [q for q in S["pl"][iso] if q["id"] != p["id"]]
                st.rerun()
        with st.form("add", clear_on_submit=True):
            c1, c2 = st.columns(2)
            s = c1.time_input("開始", value=time(16, 30))
            e = c2.time_input("終了", value=time(17, 0))
            x = st.text_input("内容", placeholder="Legend 例題3問")
            if st.form_submit_button("追加"):
                if not x.strip():
                    st.error("内容を入力してください。")
                elif s >= e:
                    st.error("終了は開始より後にしてください。")
                else:
                    S["pl"].setdefault(iso, []).append(
                        {"id": int(datetime.now().timestamp() * 1000), "s": fs(s), "e": fs(e), "x": x.strip(), "d": False}
                    )
                    st.rerun()

# ---------- 時間割・持ち物 ----------
with tab_tt:
    with st.container(border=True):
        st.markdown("**時間割の写真**")
        photo = st.file_uploader("写真を選ぶ", type=["png", "jpg", "jpeg"])
        if photo:
            st.image(photo)
            if api_key():
                if st.button("写真から時間割を読み取る", type="primary"):
                    rows = None
                    with st.spinner("読み取り中…"):
                        try:
                            rows = read_timetable(photo)
                        except Exception as ex:
                            st.error(f"読み取りに失敗しました: {ex}")
                    if rows:
                        S["tt"] = rows
                        st.session_state.ttv = st.session_state.get("ttv", 0) + 1
                        st.rerun()
                st.caption("写真はAIサービスに送信されます。氏名や学校名が写らないようにしてください。")
            else:
                st.info("APIキーが未設定です。.streamlit/secrets.toml に ANTHROPIC_API_KEY を設定すると、写真から自動で入力できます。")

    with st.container(border=True):
        st.markdown("**時間割**（読み取り後は、間違いを直してください）")
        df = pd.DataFrame(S["tt"], columns=DAYS, index=[f"{i}限" for i in range(1, 7)])
        edited = st.data_editor(df, key=f"tt{st.session_state.get('ttv', 0)}")
        new_tt = edited.fillna("").values.tolist()
        if new_tt != S["tt"]:
            S["tt"] = new_tt

    with st.container(border=True):
        st.markdown("**持ち物**")
        base = st.text_input("毎日（カンマ区切り）", S["base"])
        if base != S["base"]:
            S["base"] = base
        for i, name in enumerate(DAYS):
            v = st.text_input(f"{name}曜日だけ", S["it"][i], key=f"it{i}", placeholder="体操服")
            if v != S["it"][i]:
                S["it"][i] = v