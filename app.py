import streamlit as st
import os
import glob
import pandas as pd
from datetime import datetime, timedelta
from pypdf import PdfReader
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time

# --- 1. 系統設定 ---
st.set_page_config(page_title="創傷知情 AI 實作教練 (研究版)", layout="wide")

# --- Google Sheets 背景自動上傳函式 (Auto-Save 版) ---
def auto_save_to_google_sheets(user_id, chat_history, grade, lang):
    """每次對話更新時，自動在背景覆寫/更新該次對話紀錄"""
    if not chat_history:
        return False
        
    try:
        # 1. 連線與設定
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = dict(st.secrets["gcp_service_account"])
        if "private_key" in creds_dict:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        # 2. 開啟試算表
        target_sheet_name = "2025創傷知情研習數據" 
        sheet = client.open(target_sheet_name)
        
        # 取得或自動建立 'Coach' 分頁
        try:
            worksheet = sheet.worksheet("Coach")
        except gspread.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title="Coach", rows="1000", cols="10")
            worksheet.append_row(["登入時間", "登出時間", "學員編號", "使用分鐘數", "累積使用次數", "完整對話紀錄"])
        
        # 3. 準備資料
        tw_fix = timedelta(hours=8)
        start_t = st.session_state.get('start_time', datetime.now())
        login_str = (start_t + tw_fix).strftime("%Y-%m-%d %H:%M:%S")
        end_t = datetime.now()
        logout_str = (end_t + tw_fix).strftime("%Y-%m-%d %H:%M:%S") # 視為最後更新時間
        duration_mins = round((end_t - start_t).total_seconds() / 60, 2)
        
        # 4. 整理對話內容
        context_info = f"諮詢對象年級: {grade} / 使用語言: {lang}"
        full_conversation = f"【設定參數】：{context_info}\n\n"
        for msg in chat_history:
            role = msg.get("role", "Unknown")
            content = ""
            if "parts" in msg:
                content = msg["parts"][0] if isinstance(msg["parts"], list) else str(msg["parts"])
            elif "content" in msg:
                content = msg["content"]
            full_conversation += f"[{role}]: {content}\n"

        # 5. 尋找並更新，或新增一筆
        records = worksheet.get_all_records()
        row_to_update = None
        col_logins = worksheet.col_values(1) # 第一欄：登入時間
        col_ids = worksheet.col_values(3)    # 第三欄：學員編號
        
        for i in range(1, len(col_logins)): # 跳過標題列
            if i < len(col_ids) and col_logins[i] == login_str and str(col_ids[i]) == str(user_id):
                row_to_update = i + 1 # Gspread 索引從 1 開始
                break
                
        # 計算累積次數
        login_count = col_ids.count(str(user_id))
        if row_to_update is None:
            login_count += 1 # 新增一筆
            
        data_row = [login_str, logout_str, user_id, duration_mins, login_count, full_conversation]
        
        if row_to_update:
            # 更新既有列 (A:F)
            cell_range = f'A{row_to_update}:F{row_to_update}'
            worksheet.update(cell_range, [data_row])
        else:
            # 新增一列
            worksheet.append_row(data_row)
            
        return True
    except Exception as e:
        print(f"背景上傳失敗: {e}") # 背景報錯不干擾使用者
        return False

# --- API 輪替與防呆發送機制 (Fallback Mechanism) ---
def send_message_safely(text):
    """
    發送訊息，若失敗則自動切換至下一把 API Key 重試
    """
    time.sleep(1) # [防呆] 強制減速 1 秒
    
    # 取得目前的對話歷史
    gemini_history = []
    for msg in st.session_state.history:
        g_role = "model" if msg["role"] == "assistant" else "user"
        gemini_history.append({"role": g_role, "parts": [msg["content"]]})
        
    api_keys = st.session_state.api_keys_list
    total_keys = len(api_keys)
    
    # 開始輪替嘗試
    for i in range(total_keys):
        current_key_index = (st.session_state.current_key_index + i) % total_keys
        active_key = api_keys[current_key_index]
        
        try:
            # 使用當前的 Key 初始化模型
            genai.configure(api_key=active_key)
            model = genai.GenerativeModel(
                model_name=st.session_state.valid_model_name,
                safety_settings={
                    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                }
            )
            
            # 使用目前的歷史紀錄建立 session
            chat_session = model.start_chat(history=gemini_history)
            response = chat_session.send_message(text)
            
            # 成功則紀錄使用的 key index 並回傳
            st.session_state.current_key_index = current_key_index
            return response.text
            
        except Exception as e:
            error_msg = str(e).lower()
            st.toast(f"⚠️ Key {current_key_index + 1} 發生狀況，嘗試切換...", icon="🔄")
            
            if i == total_keys - 1:
                if "429" in error_msg or "quota" in error_msg:
                    st.warning("🐌 哎呀！您輸入的速度太快，或是目前所有 API 額度都耗盡了。請稍等 1 分鐘後再試喔！")
                    return None
                else:
                    raise e

# 初始化 Session State
if "history" not in st.session_state: st.session_state.history = []
if "loaded_text" not in st.session_state: st.session_state.loaded_text = ""
if "user_nickname" not in st.session_state: st.session_state.user_nickname = ""
if "start_time" not in st.session_state: st.session_state.start_time = datetime.now()
if "chat_session_initialized" not in st.session_state: st.session_state.chat_session_initialized = False

# 多重 API Key 記憶機制
if "raw_api_key_input" not in st.session_state: st.session_state.raw_api_key_input = ""
if "api_keys_list" not in st.session_state: st.session_state.api_keys_list = []
if "current_key_index" not in st.session_state: st.session_state.current_key_index = 0
if "valid_model_name" not in st.session_state: st.session_state.valid_model_name = "gemini-1.5-pro-latest"

# --- 2. 登入區 ---
if not st.session_state.user_nickname:
    st.title("🧠 創傷知情 AI 實作教練")
    st.info("請輸入您的研究編號 (ID) 以開始諮詢。")
    
    nickname_input = st.text_input("請輸入您的編號：", placeholder="例如：001, 002...") 
    
    if st.button("🚀 進入教練室"):
        if nickname_input.strip():
            st.session_state.user_nickname = nickname_input
            st.session_state.start_time = datetime.now()
            st.rerun()
        else:
            st.error("❌ 編號不能為空！")
    st.stop()

# --- 3. 側邊欄設定 ---
st.sidebar.title(f"👤 學員: {st.session_state.user_nickname}")
st.sidebar.markdown("*(系統已開啟自動存檔功能)*")
st.sidebar.markdown("---")

# 返回首頁按鈕
if st.session_state.chat_session_initialized:
    st.sidebar.markdown("### 🏠 導覽")
    if st.sidebar.button("返回首頁 / 換個主題", type="secondary"):
        st.session_state.history = []
        st.session_state.chat_session_initialized = False
        st.session_state.start_time = datetime.now() 
        st.rerun()

# 下載個人紀錄區
st.sidebar.markdown("---")
if st.session_state.history:
    st.sidebar.subheader("💾 個人備份")
    df = pd.DataFrame(st.session_state.history)
    df['nickname'] = st.session_state.user_nickname
    df['grade'] = st.session_state.get("current_grade", "N/A")
    df['lang'] = st.session_state.get("current_lang", "N/A")
    df['time'] = (datetime.now() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    
    csv = df.to_csv(index=False).encode('utf-8-sig')
    
    st.sidebar.download_button(
        label="📥 下載對話紀錄 (CSV)",
        data=csv,
        file_name=f"Coach紀錄_{st.session_state.user_nickname}.csv",
        mime="text/csv",
        help="點擊下載這份對話紀錄到您的電腦中保存"
    )

st.sidebar.markdown("---")
st.sidebar.warning("🔑 請輸入您的 Gemini API Key (可輸入多組)")
st.sidebar.markdown("<small>提示：輸入多組 Key 請用半形逗號 `,` 隔開，可防當機</small>", unsafe_allow_html=True)

input_key = st.sidebar.text_input("在此貼上您的 API Key", type="password", value=st.session_state.raw_api_key_input)

if input_key:
    st.session_state.raw_api_key_input = input_key
    st.session_state.api_keys_list = [k.strip() for k in input_key.split(",") if k.strip()]

if not st.session_state.api_keys_list:
    st.info("💡 提示：請先在側邊欄輸入至少一組 API Key，否則系統無法運作。")
    st.stop() 

# 模型偵測
if st.session_state.api_keys_list:
    try:
        genai.configure(api_key=st.session_state.api_keys_list[0])
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        if available_models:
            st.session_state.valid_model_name = st.sidebar.selectbox("🤖 AI 模型", available_models)
    except: 
        st.sidebar.error("❌ 第一把 API Key 無效，請檢查。")

# 選項設定
student_grade = st.sidebar.selectbox("🎯 諮詢對象年級", ["國小", "國中", "高中"])
lang = st.sidebar.selectbox("🌐 選擇對話語言", ["繁體中文", "粵語", "English"])
st.session_state.current_grade = student_grade
st.session_state.current_lang = lang

st.sidebar.caption(f"🛡️ 目前備妥 {len(st.session_state.api_keys_list)} 把 API Key 輪替中")

# --- 4. 自動讀取教材 ---
if not st.session_state.loaded_text:
    combined_text = ""
    pdf_files = glob.glob("*.pdf")
    if pdf_files:
        with st.spinner(f"📚 正在內化 {len(pdf_files)} 份專業教材..."):
            try:
                for filename in pdf_files:
                    reader = PdfReader(filename)
                    for page in reader.pages:
                        text = page.extract_text()
                        if text: combined_text += text + "\n"
                st.session_state.loaded_text = combined_text
            except Exception as e:
                st.error(f"教材讀取失敗: {e}")
    else:
        st.warning("⚠️ 倉庫中找不到 PDF 檔案。")

# --- 5. 教練對話邏輯 (Mollick Coach Prompt) ---
st.title("💬 實作策略諮詢區")

if st.session_state.loaded_text and st.session_state.api_keys_list and st.session_state.valid_model_name:

    if not st.session_state.chat_session_initialized:
        # 核心：Mollick 教練模式 Prompt
        sys_prompt = f"""
        Role: You are a "Trauma-Informed Implementation Coach" (Mollick's Coach Persona).
        Target Audience: A teacher working with {student_grade} students.
        Language: {lang}.
        
        Knowledge Base (Context Only): {st.session_state.loaded_text[:30000]}
        
        ### CRITICAL INSTRUCTIONS (MUST FOLLOW):
        1. **NO DIRECT ANSWERS:** Do NOT give solutions, advice, or lecture the teacher. Do NOT summarize the PDF text.
        2. **Reflective Partner:** Your goal is to help the teacher find their own strength and solutions.
        3. **Socratic Questioning:** Always respond with a validating statement followed by ONE or TWO open-ended questions.
        4. **Metacognition:** Ask questions like "What do you think is driving this behavior?", "What have you tried that worked before?", or "How does this make you feel?".
        5. **Use Theory as a Map:** Use the knowledge base (Trauma-Informed Care, 4F response) only to *frame* your questions, never to *teach* the content.
        
        Start the conversation by welcoming the teacher and asking what specific challenge they are facing today.
        """
        
        welcome_msg = f"你好 {st.session_state.user_nickname} 老師，我是您的 AI 實作教練。我不會直接給您標準答案，但我會陪著您一起整理思緒，找出適合您班級的策略。\n\n目前在 {student_grade} 現場，有沒有哪位學生的狀況最近讓您感到比較卡關？"
        
        st.session_state.history = [{"role": "user", "content": sys_prompt}, {"role": "assistant", "content": welcome_msg}]
        st.session_state.chat_session_initialized = True
        auto_save_to_google_sheets(st.session_state.user_nickname, st.session_state.history, st.session_state.current_grade, st.session_state.current_lang)

    for msg in st.session_state.history:
        role = "assistant" if msg["role"] == "assistant" else "user"
        if "Role: You are a" not in msg["content"]:
            with st.chat_message(role):
                st.write(msg["content"])

    if user_in := st.chat_input("描述您的挑戰（例如：學生突然大叫、拒絕合作...）"):
        st.session_state.history.append({"role": "user", "content": user_in})
        with st.chat_message("user"):
            st.write(user_in)
            
        with st.spinner("⏳ 教練正在整理思緒 (為防超速，請稍候)..."):
            try:
                # 使用自動輪替機制的安全發送函式
                resp_text = send_message_safely(user_in)
                
                if resp_text: 
                    st.session_state.history.append({"role": "assistant", "content": resp_text})
                    # 背景自動存檔
                    auto_save_to_google_sheets(
                        st.session_state.user_nickname, 
                        st.session_state.history, 
                        st.session_state.current_grade, 
                        st.session_state.current_lang
                    )
                    st.rerun()
            except Exception as e:
                st.error(f"❌ 發生嚴重錯誤: {e}")
