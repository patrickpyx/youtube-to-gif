import streamlit as st
import yt_dlp
import os
import tempfile
import subprocess
import time
import shutil

st.set_page_config(page_title="YouTube 轉 GIF 工具 (穩定版)", page_icon="🎞️", layout="wide")

st.title("🎞️ YouTube 影片轉 GIF 工具 (無損核心版)")

# 設置暫存目錄
if 'temp_dir' not in st.session_state:
    st.session_state['temp_dir'] = tempfile.mkdtemp()
temp_dir = st.session_state['temp_dir']

# 取得 FFmpeg 路徑
# 在本機 Windows 使用 ffmpeg/bin/ffmpeg.exe
# 在 Streamlit Cloud (Linux) 則直接使用系統安裝的 "ffmpeg"
LOCAL_FFMPEG = os.path.join(os.getcwd(), "ffmpeg", "bin", "ffmpeg.exe")
if os.path.exists(LOCAL_FFMPEG):
    FFMPEG_BINARY = LOCAL_FFMPEG
else:
    # 嘗試在系統路徑中尋找 ffmpeg (適用於 Streamlit Cloud / Linux)
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        FFMPEG_BINARY = system_ffmpeg
    else:
        FFMPEG_BINARY = "ffmpeg" # 預設值

# 啟動時檢查並顯示除錯資訊
st.sidebar.markdown("### 🔧 系統檢查")
if shutil.which("ffmpeg"):
    st.sidebar.success(f"FFmpeg found: `{shutil.which('ffmpeg')}`")
    try:
        ver_output = subprocess.check_output([FFMPEG_BINARY, "-version"], text=True, stderr=subprocess.STDOUT)
        st.sidebar.text(f"Version: {ver_output.splitlines()[0]}")
    except Exception as e:
        st.sidebar.error(f"Check version failed: {e}")
else:
    st.sidebar.error("⚠️ FFmpeg NOT found in system path!")
    if os.path.exists(LOCAL_FFMPEG):
        st.sidebar.info(f"Using local binary: {LOCAL_FFMPEG}")
    else:
        st.sidebar.warning("Please check packages.txt")



def process_youtube_to_gif(url, start_time, end_time, width, fps, output_gif):
    duration = end_time - start_time
    
    # 建立下載暫存檔
    temp_mp4 = os.path.join(temp_dir, "download_clip.mp4")
    if os.path.exists(temp_mp4):
        os.remove(temp_mp4)
        
    # 步驟 1: 使用 yt-dlp 獲取影片真實連結 (不做下載動作，避免內部轉檔錯誤)
    st.text(f"Step 1: 正在解析影片連結並進行精確擷取 ({start_time}s ~ {end_time}s)...")
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]',
        'noplaylist': True,
        'quiet': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # 取得最佳影片與音訊連結
            video_url = None
            audio_url = None
            
            # 情況 A: 影片與音訊分開 (Adaptive format)
            if 'requested_formats' in info:
                for f in info['requested_formats']:
                    if f['vcodec'] != 'none':
                        video_url = f['url']
                    if f['acodec'] != 'none':
                        audio_url = f['url']
            
            # 情況 B: 單一檔案 (已被合併或原始就是單檔)
            if not video_url:
                video_url = info['url']
                audio_url = info['url'] # 音訊來源同一個
            
            # 使用 FFmpeg 直接下載並剪輯 (比 yt-dlp 內建更穩定)
            # 指令邏輯: input video -> input audio -> trim -> map -> output
            # 注意: 網路連結作為 input 必須放在 -ss 之後才能精準 seek
            
            # 安全檢查
            if not video_url:
                raise Exception("無法取得影片連結")
                
            # 取得 HTTP Header 以繞過 403 Forbidden
            # 取得 HTTP Header 以繞過 403 Forbidden
            # 格式化為 FFmpeg 可接受的字串
            ffmpeg_headers = ""
            if 'http_headers' in info:
                headers_list = []
                for k, v in info['http_headers'].items():
                    # 排除可能造成問題的 header
                    if k.lower() not in ['host', 'content-length', 'connection']:
                        headers_list.append(f"{k}: {v}")
                ffmpeg_headers = "\n".join(headers_list) # Linux 環境改用 \n 分隔嘗試
            
            st.text("Step 1.5: 正在進行雲端直接串流剪輯 (Injecting Headers)...")
            
            # 準備 FFmpeg 指令，針對每個 input 都要加上 headers
            dl_cmd = [
                FFMPEG_BINARY, "-y"
            ]
            
            # Input 1: Video
            if ffmpeg_headers:
                dl_cmd.extend(["-headers", ffmpeg_headers])
            dl_cmd.extend(["-ss", str(start_time), "-t", str(end_time - start_time), "-i", video_url])
            
            # Input 2: Audio
            if ffmpeg_headers:
                dl_cmd.extend(["-headers", ffmpeg_headers])
            dl_cmd.extend(["-ss", str(start_time), "-t", str(end_time - start_time), "-i", audio_url])
            
            # Output settings
            dl_cmd.extend([
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac",
                temp_mp4
            ])
            
            subprocess.run(dl_cmd, check=True, capture_output=True, text=True)

    except subprocess.CalledProcessError as e:
        st.error("FFmpeg 下載/剪輯失敗")
        st.error(f"錯誤代碼: {e.returncode}")
        st.code(e.stderr)
        raise e
    except Exception as e:
        st.error(f"解析或下載過程中發生問題：{str(e)}")
        st.code(str(e))
        raise e

    if not os.path.exists(temp_mp4):
        raise Exception("下載失敗，請確認網址是否正確。")

    # 步驟 2: 使用 FFmpeg 轉換為高品質 GIF (使用 Palette 調色盤技術)
    st.text("Step 2: 正在生成高品質 GIF...")
    
    # FFmpeg 高品質 GIF 指令：生成調色盤 -> 套用調色盤
    # 1. 生成調色盤
    palette_path = os.path.join(temp_dir, "palette.png")
    palette_cmd = [
        FFMPEG_BINARY, "-y", "-i", temp_mp4,
        "-vf", f"fps={fps},scale={width}:-1:flags=lanczos,palettegen",
        palette_path
    ]
    try:
        subprocess.run(palette_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        st.error("生成調色盤失敗 (Step 2.1)")
        st.error(f"錯誤代碼: {e.returncode}")
        st.code(e.stderr) # 顯示詳細錯誤
        raise e
    
    # 2. 套用調色盤生成 GIF
    gif_cmd = [
        FFMPEG_BINARY, "-y", "-i", temp_mp4, "-i", palette_path,
        "-lavfi", f"fps={fps},scale={width}:-1:flags=lanczos [x]; [x][1:v] paletteuse=dither=sierra2_4a",
        output_gif
    ]
    try:
        subprocess.run(gif_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        st.error("生成 GIF 失敗 (Step 2.2)")
        st.error(f"錯誤代碼: {e.returncode}")
        st.code(e.stderr) # 顯示詳細錯誤
        raise e
    
    return output_gif

# UI 介面
url = st.text_input("第一步：貼上 YouTube 網址", placeholder="https://www.youtube.com/watch?v=...")

if url:
    st.markdown("---")
    st.subheader("⚙️ 第二步：參數設定")
    col1, col2 = st.columns(2)
    
    with col1:
        start_time = st.number_input("開始時間 (秒)", min_value=0.0, value=0.0, step=0.1)
        end_time = st.number_input("結束時間 (秒)", min_value=0.1, value=5.0, step=0.1)
    with col2:
        gif_width = st.select_slider("解析度 (寬度)", options=[240, 320, 480, 640], value=480)
        gif_fps = st.slider("幀率 (FPS)", min_value=5, max_value=30, value=12)

    if st.button("🚀 第三步：開始轉換", type="primary"):
        if end_time <= start_time:
            st.error("結束時間必須大於開始時間！")
        else:
            try:
                with st.spinner("影片處理中，請稍候... (高品質轉檔較耗時)"):
                    gif_path = os.path.join(temp_dir, "output.gif")
                    process_youtube_to_gif(url, start_time, end_time, gif_width, gif_fps, gif_path)
                    
                    filesize = os.path.getsize(gif_path) / (1024 * 1024)
                    st.success(f"✨ 轉換完成！檔案大小：{filesize:.2f} MB")
                    st.image(gif_path)
                    
                    with open(gif_path, "rb") as f:
                        st.download_button("💾 下載 GIF 檔案", f, file_name="youtube_clip.gif", mime="image/gif")
            except Exception as e:
                st.error(f"轉換失敗：{str(e)}")
else:
    st.info("請輸入網址開始。")
