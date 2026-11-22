import json
import random
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="學測英文單字練習", page_icon="📘", layout="centered")
BANK = json.loads(Path(__file__).with_name("questions.json").read_text(encoding="utf-8"))


def start_quiz(pool, count):
    selected = random.sample(pool, min(count, len(pool)))
    st.session_state.quiz = [dict(q, options=random.sample(q["options"], len(q["options"]))) for q in selected]
    st.session_state.responses = {}
    st.session_state.submitted = False
    st.session_state.quiz_id = st.session_state.get("quiz_id", 0) + 1


st.title("📘 學測英文單字練習")
st.caption("透過句子練習單字意思、搭配與語境判斷。")

with st.sidebar:
    st.header("練習設定")
    count = st.selectbox("題數", [5, 10, 15, len(BANK)], index=1, format_func=lambda n: f"{n} 題")
    st.caption(f"目前題庫共 {len(BANK)} 題，題目與選項會隨機排列。")
    if st.button("開始新的測驗", type="primary", use_container_width=True):
        start_quiz(BANK, count)
        st.rerun()

if "quiz" not in st.session_state:
    st.markdown("### 準備好了嗎？")
    st.write("選擇題數後，按下「開始新的測驗」。完成後可查看中文解析，並針對錯題再練習。")
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
                st.rerun()
else:
    responses = st.session_state.responses
    wrong = [q for i, q in enumerate(quiz) if responses[i] != q["answer"]]
    score = len(quiz) - len(wrong)
    st.subheader("測驗結果")
    st.metric("答對題數", f"{score} / {len(quiz)}", f"正確率 {score / len(quiz):.0%}")
    if wrong:
        if st.button(f"重練 {len(wrong)} 題錯題", type="primary"):
            start_quiz(wrong, len(wrong))
            st.rerun()
    else:
        st.success("全部答對了！")
    st.markdown("### 答案與解析")
    for i, q in enumerate(quiz):
        correct = responses[i] == q["answer"]
        with st.expander(f"{'✅' if correct else '❌'} 第 {i + 1} 題 · {q['category']}", expanded=not correct):
            st.write(q["question"])
            st.write(f"你的答案：{responses[i]}")
            st.write(f"正確答案：**{q['answer']}**")
            st.write(q["explanation"])
