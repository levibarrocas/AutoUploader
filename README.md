# Auto Uploader & Resizer

A lightweight Python tool that watches a folder for new screenshots, optimizes them, and automatically uploads them to the web.

## Features
- **Folder Watcher:** Detects new images instantly.
- **Smart Resize:** Shrinks large images, converts non-transparent PNGs to optimized JPGs.
- **Multi-Uploader:** Upload to Catbox, ImgChest, or use your own custom ShareX uploaders (`.sxcu`).
- **Web Gallery:** View history, copy links, and configure settings from a local web UI.

## How to Use

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Run the app:
   ```bash
   python resizer.py
   ```
3. Open `http://127.0.0.1:5252` in your web browser and complete the first-time setup.