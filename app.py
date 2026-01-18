import streamlit as st
import os
import pandas as pd
from datetime import datetime
from pypdf import PdfReader
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- 1. 系統設定 ---
st.set_page_config(page_title="創傷知情 AI 實作教練 (專業版)", layout="wide")

if "history" not in st.session_state: st.session_state.history = []
if "loaded_text" not in st.session_state: st.session_state.loaded_text = ""
if "user_nickname" not in st.session_state: st.session_state.user_nickname = ""

# --- 2. 登入區 ---
if not st.session_state.user_nickname:
    st.title("🧠 創傷知情 AI 實作教練")
    st.markdown("### 老師您好，我是您的 AI 導師。請輸入您的暱稱開始諮詢。")
    nickname_input = st.text_input("請輸入您的暱稱：", placeholder="例如：兆祺心理師...")
    if st.button("🚀 進入教練室"):
        if nickname_input.strip():
            st.session_state.user_nickname = nickname_input
            st.rerun()
        else:
            st.error("❌ 暱稱不能為空！")
    st.stop()

# --- 3. 側邊欄設定 (新增年級選項) ---
st.sidebar.title(f"👤 導師: {st.session_state.user_nickname}")
st.sidebar.markdown("---")

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    api_key = st.sidebar.text_input("請輸入您的 Gemini API Key", type="password")

valid_model_name = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        if available_models:
            valid_model_name = st.sidebar.selectbox("🤖 選擇 AI 模型", available_models)
    except: pass

# --- 重點：補上年級選項 ---
student_grade = st.sidebar.selectbox("🎯 諮詢對象年級", ["國小", "國中", "高中"])
lang = st.sidebar.selectbox("🌐 選擇對話語言", ["繁體中文", "粵語", "English"])

# --- 4. 自動讀取雙教材 ---
FILES = [
    "創傷知情文本Creating Trauma informed Strength based Classroom_compressed.pdf",
    "Assigning AI_Seven Apperoaches for Students with prompts.pdf"
]

if not st.session_state.loaded_text:
    combined_text = ""
    with st.spinner("📚 正在載入創傷知情專業文本..."):
        for filename in FILES:
            if os.path.exists(filename):
                try:
                    reader = PdfReader(filename)
                    for page in reader.pages:
                        text = page.extract_text()
                        if text: combined_text += text + "\n"
                except: st.error(f"讀取 {filename} 失敗")
        st.session_state.loaded_text = combined_text

# --- 5. 教練邏輯主畫面 ---
st.title("💬 實作策略諮詢區")

if st.session_state.loaded_text and api_key and valid_model_name:
    model = genai.GenerativeModel(
        model_name=valid_model_name,
        safety_settings={
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
    )

    if len(st.session_state.history) == 0:
        # 設定包含「年級差異化」的系統角色
        sys_prompt = f"""
        Role: You are a "Trauma-Informed Implementation Coach" specialized in {student_grade} education.
        Core Knowledge: {st.session_state.loaded_text[:25000]}
        
        Instruction:
        1. Context Awareness: The user is dealing with {student_grade} students. 
           - For 國小: Focus more on sensory regulation, bottom-up strategies, and simple, consistent routines.
           - For 國中/高中: Focus more on autonomy, respect, identifying 'triggers' related to social status, and helping them self-regulate.
        2. Process: Validate -> Socratic Questioning (identify 4F) -> Co-create strategy (Strength-based).
        3. Never give the answer immediately; lead the teacher to find the strength in the student.
        4. Language: {lang}.
        """
        st.session_state.chat_session = model.start_chat(history=[
            {"role": "user", "parts": [sys_prompt]},
            {"role": "model", "parts": [f"你好 {st.session_state.user_nickname} 老師，我知道您目前在處理 {student_grade} 的班級。在落實創傷知情實踐時，有沒有哪個個案或情境讓您感到特別挑戰？"]}
        ])
        st.session_state.history.append({"role": "student", "content": f"你好 {st.session_state.user_nickname} 老師，我知道您目前在處理 {student_grade} 的班級。在落實創傷知情實踐時，有沒有哪個個案或情境讓您感到特別挑戰？"})

    for msg in st.session_state.history:
        with st.chat_message("assistant" if msg["role"] == "student" else "user"):
            st.write(msg["content"])

    if user_in := st.chat_input("請描述您的實作瓶頸..."):
        st.session_state.history.append({"role": "teacher", "content": user_in})
        resp = st.session_state.chat_session.send_message(user_in)
        st.session_state.history.append({"role": "student", "content": resp.text})
        st.rerun()

# --- 6. 紀錄下載功能 ---
st.sidebar.markdown("---")
if st.session_state.history:
    st.sidebar.subheader("💾 紀錄保存")
    df = pd.DataFrame(st.session_state.history)
    df['grade_context'] = student_grade
    csv = df.to_csv(index=False).encode('utf-8-sig')
    st.sidebar.download_button(
        label="📥 下載諮詢紀錄 (CSV)",
        data=csv,
        file_name=f"實作諮詢_{student_grade}_{st.session_state.user_nickname}.csv",
        mime="text/csv"
    )
