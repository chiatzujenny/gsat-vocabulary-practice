import hmac
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from progress_store import ProgressStore

st.set_page_config(page_title="學測英文單字練習", page_icon="📘", layout="centered")
BANK = json.loads(Path(__file__).with_name("questions.json").read_text(encoding="utf-8"))


def get_settings():
    try:
        settings = st.secrets["progress"]
        url = settings["database_url"]
        password = settings["admin_password"]
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        parsed = make_url(url)
        if (parsed.drivername not in ("postgresql", "postgresql+psycopg2")
                or parsed.query.get("sslmode") not in ("require", "verify-ca", "verify-full")
                or len(password) < 24):
            return None
        return url, password
    except (ArgumentError, KeyError, FileNotFoundError, TypeError, ValueError):
        return None


@st.cache_resource(show_spinner=False)
def connect_store(url):
    store = ProgressStore(url)
    store.initialize()
    return store


def start_quiz(pool, count, quiz_type="一般練習"):
    import random
    selected = random.sample(pool, min(count, len(pool)))
    st.session_state.quiz = [dict(q, options=random.sample(q["options"], len(q["options"]))) for q in selected]
    st.session_state.responses = {}
    st.session_state.submitted = False
    st.session_state.saved = False
    st.session_state.quiz_type = quiz_type
    st.session_state.attempt_id = str(uuid.uuid4())
    st.session_state.quiz_id = st.session_state.get("quiz_id", 0) + 1


def logout():
    for key in list(st.session_state):
        del st.session_state[key]
    st.rerun()


def save_attempt(store):
    try:
        store.save(st.session_state.attempt_id, st.session_state.student_id,
                   st.session_state.student_name, st.session_state.quiz_type,
                   st.session_state.quiz, st.session_state.responses)
        st.session_state.saved = True
    except Exception:
        st.session_state.saved = False


st.title("📘 學測英文單字練習")
settings = get_settings()
if settings is None:
    st.error("網站管理員正在設定作答紀錄功能，請稍後再試。")
    st.stop()
try:
    store = connect_store(settings[0])
except Exception:
    st.error("目前無法連線作答紀錄，請稍後再試或聯絡網站管理員。")
    st.stop()

if "role" not in st.session_state:
    student_tab, admin_tab = st.tabs(["學生登入", "管理員登入"])
    with student_tab:
        st.write("請輸入管理員提供的個人代碼和您的名稱。")
        with st.form("student_login"):
            name = st.text_input("名稱", max_chars=80)
            code = st.text_input("個人代碼", type="password", max_chars=128)
            enter = st.form_submit_button("登入並開始練習", type="primary")
        st.caption("交卷後會保存名稱、作答內容與成績，供管理員查看。請妥善保管個人代碼。")
        if enter:
            name, code = name.strip(), code.strip()
            if not name or not code:
                st.warning("請填寫名稱和個人代碼。")
            else:
                try:
                    student_id = store.authenticate_student(code)
                except Exception:
                    st.error("無法驗證代碼，請稍後再試。")
                else:
                    if student_id:
                        st.session_state.role = "student"
                        st.session_state.student_id = student_id
                        st.session_state.student_name = name
                        st.rerun()
                    else:
                        st.error("個人代碼不正確，請向管理員確認。")
    with admin_tab:
        with st.form("admin_login"):
            admin_password = st.text_input("管理員密碼", type="password")
            admin_enter = st.form_submit_button("登入管理頁面")
        if admin_enter:
            try:
                now = int(time.time())
                if store.admin_locked(now):
                    st.warning("登入嘗試過於頻繁，請稍候幾秒再試。")
                elif hmac.compare_digest(admin_password.encode("utf-8"), settings[1].encode("utf-8")):
                    store.clear_admin_failures()
                    st.session_state.role = "admin"
                    st.rerun()
                else:
                    store.record_admin_failure(now)
                    st.error("管理員密碼不正確。")
            except Exception:
                st.error("目前無法驗證管理員登入，請稍後再試。")
    st.stop()

if st.session_state.role == "admin":
    st.subheader("管理頁面")
    if st.button("登出"):
        logout()
    st.markdown("### 建立學生代碼")
    st.caption("每位學生使用不同的代碼。代碼只在建立時顯示一次，請複製後交給該學生。")
    if st.button("產生一組學生代碼"):
        try:
            st.session_state.new_code = store.create_student_code()
        except Exception:
            st.error("目前無法建立代碼，請稍後再試。")
    if st.session_state.get("new_code"):
        st.code(st.session_state.new_code)
        st.warning("離開此頁後無法再次查看這組代碼，請先安全地交給學生。")
    st.markdown("### 作答紀錄")
    try:
        rows = store.list_attempts()
    except Exception:
        st.error("目前無法讀取作答紀錄，請稍後再試。")
        st.stop()
    if not rows:
        st.info("目前尚無已完成的測驗。")
    else:
        names = {row["student_id"]: row["student_name"] for row in reversed(rows)}
        labels = {sid: f"{name}（代碼識別 {sid[:8]}）" for sid, name in names.items()}
        selected = st.selectbox("選擇學生", list(labels), format_func=lambda sid: labels[sid])
        student_rows = [row for row in rows if row["student_id"] == selected]
        st.write(f"共 {len(student_rows)} 次作答；同一代碼會歸在同一位學生。")
        for row in student_rows:
            date = datetime.fromisoformat(row["submitted_at"]).astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M") + "（台灣時間）"
            with st.expander(f"{date} · {row['student_name']} · {row['quiz_type']} · {row['score']}/{row['total']}"):
                for i, answer in enumerate(row["answers"], 1):
                    st.write(f"{'✅' if answer['correct'] else '❌'} {i}. {answer['question']}")
                    st.write(f"作答：{answer['selected']}　｜　正確答案：{answer['answer']}")
    st.stop()

st.caption(f"目前登入：{st.session_state.student_name}")
with st.sidebar:
    if st.button("登出", disabled=st.session_state.get("submitted", False) and not st.session_state.get("saved", False)):
        logout()
    st.header("練習設定")
    count = st.selectbox("題數", [5, 10, 15, len(BANK)], index=1, format_func=lambda n: f"{n} 題")
    st.caption(f"目前題庫共 {len(BANK)} 題，題目與選項會隨機排列。")
    if st.button("開始新的測驗", type="primary", use_container_width=True):
        if st.session_state.get("submitted") and not st.session_state.get("saved"):
            st.warning("上次測驗尚未儲存，請先重試儲存。")
        else:
            start_quiz(BANK, count)
            st.rerun()

if "quiz" not in st.session_state:
    st.markdown("### 準備好了嗎？")
    st.write("選擇題數後，按下「開始練習」。完成後可查看中文解析，並針對錯題再練習。")
    if st.button("開始練習", type="primary"):
        start_quiz(BANK, count)
        st.rerun()
    st.stop()

quiz = st.session_state.quiz
if not st.session_state.submitted:
    st.subheader(f"本次測驗・共 {len(quiz)} 題")
    st.write("請為每題選出最適合填入句子的答案。")
    with st.form("quiz_form"):
        for i, q in enumerate(quiz):
            st.markdown(f"**{i + 1}. {q['question']}**")
            st.radio("選擇答案", q["options"], index=None, key=f"answer_{st.session_state.quiz_id}_{i}", label_visibility="collapsed")
            st.divider()
        submitted = st.form_submit_button("交卷並查看解析", type="primary", use_container_width=True)
        if submitted:
            responses = {i: st.session_state.get(f"answer_{st.session_state.quiz_id}_{i}") for i in range(len(quiz))}
            if any(value is None for value in responses.values()):
                st.warning("還有題目未作答，請完成所有題目後再交卷。")
            else:
                st.session_state.responses = responses
                st.session_state.submitted = True
                save_attempt(store)
                st.rerun()
else:
    responses = st.session_state.responses
    wrong = [q for i, q in enumerate(quiz) if responses[i] != q["answer"]]
    score = len(quiz) - len(wrong)
    st.subheader("測驗結果")
    st.metric("答對題數", f"{score} / {len(quiz)}", f"正確率 {score / len(quiz):.0%}")
    if st.session_state.saved:
        st.success("作答紀錄已儲存。")
    else:
        st.error("作答紀錄尚未儲存，請先不要關閉或重新整理頁面。")
        if st.button("重試儲存"):
            save_attempt(store)
            st.rerun()
    if wrong and st.session_state.saved:
        if st.button(f"重練 {len(wrong)} 題錯題", type="primary"):
            start_quiz(wrong, len(wrong), "錯題重練")
            st.rerun()
    elif not wrong:
        st.success("全部答對了！")
    st.markdown("### 答案與解析")
    for i, q in enumerate(quiz):
        correct = responses[i] == q["answer"]
        with st.expander(f"{'✅' if correct else '❌'} 第 {i + 1} 題 · {q['category']}", expanded=not correct):
            st.write(q["question"])
            st.write(f"你的答案：{responses[i]}")
            st.write(f"正確答案：**{q['answer']}**")
            st.write(q["explanation"])
