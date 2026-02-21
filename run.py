import os
import webbrowser
import threading
import uvicorn

port = int(os.environ.get("PORT", 8000))
host = os.environ.get("HOST", "127.0.0.1")

def open_browser():
    webbrowser.open(f"http://127.0.0.1:{port}")

if __name__ == "__main__":
    if host == "127.0.0.1":
        threading.Timer(1.5, open_browser).start()
    uvicorn.run("api:app", host=host, port=port, reload=(host == "127.0.0.1"))
