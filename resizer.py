import os
import sys
import time
import json
import re
import requests
import pyperclip
import subprocess
import sqlite3
import threading
from flask import Flask, jsonify, render_template, request, send_from_directory, redirect, url_for
from PIL import Image
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import numpy as np

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
DB_FILE = os.path.join(BASE_DIR, "history.db")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
THUMB_SIZE = (120, 120)
ALPHA_THRESHOLD=30

app = Flask(__name__)

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                loaded = json.load(f)
        except: pass
        else:
            if not loaded.get("watch_folders"):
                print("WARNING: 'watch_folders' is empty in settings.")
            if not loaded.get("output_folder"):
                print("WARNING: 'output_folder' is empty in settings.")
            if not loaded.get("catbox_userhash"):
                print("WARNING: 'catbox_userhash' is empty in settings.")
            if not loaded.get("imgchest_api_key"):
                print("WARNING: 'imgchest_api_key' is empty in settings.")
            return loaded

    print(f"Settings file not found. Creating empty template at {SETTINGS_FILE}...")
    default_settings = {"watch_folders": [], "output_folder": "", "catbox_userhash": "", "imgchest_api_key": "", "uploaders": [], "upload_sequence": ["catbox", "imgchest"], "max_width": 2000, "max_height": 2000, "jpg_quality": 95}
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(default_settings, f, indent=4)
    return default_settings

settings = load_settings()

def save_settings():
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS history
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      path TEXT,
                      url TEXT,
                      timestamp REAL)''')
        # Migrate existing JSON to SQLite if the database is empty
        if os.path.exists(HISTORY_FILE):
            c.execute("SELECT COUNT(*) FROM history")
            if c.fetchone()[0] == 0:
                try:
                    with open(HISTORY_FILE, 'r') as f:
                        data = json.load(f)
                        for item in reversed(data): # Insert oldest first to maintain timeline
                            c.execute("INSERT INTO history (path, url, timestamp) VALUES (?, ?, ?)",
                                      (item.get('path'), item.get('url'), item.get('timestamp', time.time())))
                except: pass
        conn.commit()

def save_to_history(img_path, url):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO history (path, url, timestamp) VALUES (?, ?, ?)", (img_path, url, time.time()))
        conn.commit()

def is_configured():
    return bool(settings.get("watch_folders") and settings.get("output_folder"))

@app.route('/')
def index():
    if not is_configured():
        return redirect(url_for('setup_page'))
    return render_template('index.html')

@app.route('/setup')
def setup_page():
    if is_configured() and not request.args.get('force'):
        return redirect(url_for('index'))
    return render_template('setup.html')

@app.route('/settings')
def settings_page():
    return render_template('settings.html')

@app.route('/api/history')
def api_history():
    start = request.args.get('start', type=float)
    end = request.args.get('end', type=float)
    page = request.args.get('page', 1, type=int)
    limit = 200
    offset = (page - 1) * limit

    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        query = "SELECT id, path, url, timestamp FROM history"
        params = []
        conditions = []
        
        if start is not None:
            conditions.append("timestamp >= ?")
            params.append(start)
        if end is not None:
            conditions.append("timestamp <= ?")
            params.append(end)
            
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
            
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        c.execute(query, params)
        return jsonify([dict(row) for row in c.fetchall()])

@app.route('/api/settings', methods=['GET'])
def get_settings():
    return jsonify(settings)

@app.route('/api/settings', methods=['POST'])
def update_settings():
    global settings
    settings = request.json
    save_settings()
    update_watchers()
    return jsonify({"status": "ok"})

@app.route('/api/clear', methods=['POST'])
def clear_history():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM history")
        conn.commit()
    return jsonify({"status": "ok"})

@app.route('/api/delete/<int:item_id>', methods=['POST'])
def delete_item(item_id):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM history WHERE id = ?", (item_id,))
        conn.commit()
    return jsonify({"status": "ok"})

@app.route('/api/test_uploader', methods=['POST'])
def test_uploader():
    idx = request.json.get('index')
    uploaders = settings.get("uploaders", [])
    if idx is None or idx < 0 or idx >= len(uploaders):
        return jsonify({"success": False, "error": "Invalid uploader index."})
        
    uploader = uploaders[idx]
    
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT path FROM history ORDER BY timestamp DESC LIMIT 1")
        row = c.fetchone()
        
    if not row or not os.path.exists(row[0]):
        return jsonify({"success": False, "error": "No local image found in history to test with."})
        
    handler = ImageHandler()
    url = handler.upload_via_sharex(row[0], uploader)
    
    if url:
        return jsonify({"success": True, "url": url})
    return jsonify({"success": False, "error": "Upload failed. Check console for details."})

@app.route('/images/<path:filename>')
def serve_image(filename):
    output_dir = settings.get("output_folder", "")
    return send_from_directory(output_dir, filename)

# --- MODIFIED HANDLER ---
class ImageHandler(FileSystemEventHandler):
    def __init__(self):
        super().__init__()

    def on_created(self, event):
        if not event.is_directory:
            threading.Thread(target=self.process_image, args=(event.src_path,), daemon=True).start()

    def process_image(self, file_path):
        # (Same logic as your previous script for waiting/resizing)
        if not self.wait_for_file(file_path): return
        
        # Mock Processing/Upload Logic
        output_path = self.run_pipeline(file_path)
        if output_path:
            url = self.upload(output_path)
            if url:
                pyperclip.copy(url)
                save_to_history(output_path, url)
                self.notify("Upload Complete", url)

    def wait_for_file(self, path, timeout=10):
        start = time.time()
        while time.time() - start < timeout:
            try:
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    with open(path, 'rb') as f:
                        with Image.open(f) as img:
                            img.verify()
                    return True
            except:
                time.sleep(0.5)
        return False

    def run_pipeline(self, file_path):
        try:
            with Image.open(file_path) as img:
                # 1. Determine if transparency exists
                is_transparent = False
                if img.mode == 'RGBA':
                    # Convert alpha channel to numpy array for fast analysis
                    alpha = np.array(img.getchannel('A'))
                    # If any pixel is more transparent than our threshold, keep it as PNG
                    if np.any(alpha < ALPHA_THRESHOLD):
                        is_transparent = True
                
                # 2. Set Extension and Path
                ext = ".png" if is_transparent else ".jpg"
                base_name = os.path.splitext(os.path.basename(file_path))[0]
                output_dir = settings.get("output_folder", "")
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)
                output_path = os.path.join(output_dir, base_name + ext)

                # 3. Resize
                max_w = settings.get("max_width", 2000)
                max_h = settings.get("max_height", 2000)
                img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
                
                # 4. Save with format-specific optimizations
                if is_transparent:
                    img.save(output_path, "PNG", compress_level=6)
                else:
                    # Convert to RGB to discard alpha channel for JPG saving
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    jpg_q = settings.get("jpg_quality", 95)
                    img.save(output_path, "JPEG", quality=jpg_q, optimize=True)

                print(f"Processed: {os.path.basename(file_path)} -> {ext}")
                return output_path
                
        except Exception as e:
            print(f"Error processing {file_path}: {e}")
            return None

    def upload(self, file_path):
        sequence = settings.get("upload_sequence", ["catbox", "imgchest"])
        
        for dest in sequence:
            if dest == "catbox":
                print("Trying Catbox...")
                url = self.upload_to_catbox(file_path)
                if url: return url
            elif dest == "imgchest":
                print("Trying ImgChest...")
                url = self.upload_to_imgchest(file_path)
                if url: return url
            elif dest.startswith("sharex:"):
                idx_or_url = dest.split("sharex:", 1)[1]
                uploader = next((u for u in settings.get("uploaders", []) if u.get("Name", u.get("RequestURL")) == idx_or_url), None)
                if uploader:
                    print(f"Trying custom uploader: {uploader.get('Name', uploader.get('RequestURL'))}")
                    url = self.upload_via_sharex(file_path, uploader)
                    if url: return url

        return None

    def upload_via_sharex(self, file_path, config):
        try:
            method = config.get("RequestMethod", "POST")
            url = config.get("RequestURL")
            if not url: return None
            
            headers = config.get("Headers", {})
            # Map null values in Arguments to empty strings
            data = {k: ("" if v is None else v) for k, v in config.get("Arguments", {}).items()}
            file_form_name = config.get("FileFormName", "fileToUpload")
            
            with open(file_path, 'rb') as f:
                r = requests.request(method, url, headers=headers, data=data, files={file_form_name: f}, timeout=15)
                
                if r.status_code in [200, 201]:
                    response_text = r.text.strip()
                    url_pattern = config.get("URL")
                    
                    # If config has a specific ShareX URL parsing pattern like $json:data.link$
                    if url_pattern:
                        matches = re.findall(r'\$json:([^\$]+)\$', url_pattern) or re.findall(r'\{json:([^\}]+)\}', url_pattern)
                        if matches:
                            resp_json = r.json()
                            result_url = url_pattern
                            for match in matches:
                                parts = match.replace('[', '.').replace(']', '').split('.')
                                val = resp_json
                                for p in parts:
                                    if p and isinstance(val, dict): val = val.get(p)
                                    elif p and isinstance(val, list) and p.isdigit(): val = val[int(p)]
                                if val is not None:
                                    result_url = result_url.replace(f"$json:{match}$", str(val)).replace(f"{{json:{match}}}", str(val))
                            if result_url.startswith("http"): return result_url
                    
                    # Fallback to pure text response if the host returns just a URL
                    if response_text.startswith("http"):
                        return response_text
        except Exception as e:
            print(f"ShareX uploader error: {e}")
        return None

    def upload_to_catbox(self, file_path):
        try:
            userhash = settings.get("catbox_userhash", "")
            with open(file_path, 'rb') as f:
                r = requests.post("https://catbox.moe/user/api.php", 
                               data={'reqtype': 'fileupload', 'userhash': userhash}, 
                               files={'fileToUpload': f}, timeout=15)
                # Catbox returns the URL directly as text
                if r.status_code == 200 and "https://" in r.text:
                    return r.text.strip()
        except Exception as e:
            print(f"Catbox error: {e}")
        return None

    def upload_to_imgchest(self, file_path):
        try:
            api_key = settings.get("imgchest_api_key", "")
            headers = {"Authorization": f"Bearer {api_key}"}
            with open(file_path, 'rb') as f:
                r = requests.post("https://api.imgchest.com/v1/post", headers=headers,
                               files={'images[]': (os.path.basename(file_path), f)}, timeout=15)
                
                if r.status_code == 201 or r.status_code == 200:
                    data = r.json()
                    # ImgChest returns a complex JSON; we want the first image link
                    return data.get('data', {}).get('images', [])[0].get('link')
        except Exception as e:
            print(f"ImgChest error: {e}")
        return None

    def notify(self, title, msg):
        subprocess.run(["notify-send", title, msg])

observer = Observer()

def update_watchers():
    observer.unschedule_all()
    for folder in settings.get("watch_folders", []):
        if os.path.exists(folder):
            observer.schedule(ImageHandler(), folder)

if __name__ == "__main__":
    output_dir = settings.get("output_folder", "")
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    init_db()
    
    update_watchers()
    observer.start()
    
    try:
        print("Starting Web UI on http://127.0.0.1:5252")
        app.run(host='127.0.0.1', port=5252, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()