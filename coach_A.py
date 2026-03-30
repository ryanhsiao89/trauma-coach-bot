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
# 💡 提示：如果是 B 檔案，可以改成 (分流B)
st.set_page_config(page_title="創傷知情 AI 實作教練 (分流A)", layout="wide")

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
                    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold
