import os
import random
import webbrowser
import pickle
import tkinter as tk
from tkinter import messagebox

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
CLIENT_SECRETS_FILE = "client_secrets.json"
TOKEN_FILE = "token.pickle"


def authenticate():
    creds = None

    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return build("youtube", "v3", credentials=creds)


def get_liked_videos(youtube, progress_label, root):
    videos = []
    next_page_token = None

    while True:
        request = youtube.videos().list(
            part="id,snippet",
            myRating="like",
            maxResults=50,
            pageToken=next_page_token,
        )
        response = request.execute()

        for item in response.get("items", []):
            videos.append({
                "id": item["id"],
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"],
            })

        next_page_token = response.get("nextPageToken")
        progress_label.config(text=f"取得中... {len(videos)} 件")
        root.update()

        if not next_page_token:
            break

    return videos


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube ランダム高評価動画")
        self.root.resizable(False, False)

        self.videos = []
        self.remaining = []

        self._center_window(420, 220)

        self.progress_label = tk.Label(root, text="読み込み中...", font=("", 11))
        self.progress_label.pack(pady=30)

        self.root.after(100, self.load)

    def _center_window(self, w, h):
        self.root.geometry(f"{w}x{h}")
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def load(self):
        if not os.path.exists(CLIENT_SECRETS_FILE):
            messagebox.showerror(
                "エラー",
                f"{CLIENT_SECRETS_FILE} が見つかりません。\nREADME.md の手順で認証情報を取得してください。"
            )
            self.root.destroy()
            return

        try:
            youtube = authenticate()
            self.videos = get_liked_videos(youtube, self.progress_label, self.root)
        except Exception as e:
            messagebox.showerror("エラー", str(e))
            self.root.destroy()
            return

        if not self.videos:
            messagebox.showinfo("情報", "高く評価した動画が見つかりませんでした。")
            self.root.destroy()
            return

        self.remaining = self.videos.copy()
        self.progress_label.destroy()
        self._build_ui()

    def _build_ui(self):
        frame = tk.Frame(self.root, padx=20, pady=15)
        frame.pack(fill="both", expand=True)

        self.title_label = tk.Label(
            frame, text="", font=("", 11, "bold"),
            wraplength=380, justify="left", anchor="w"
        )
        self.title_label.pack(fill="x", pady=(0, 4))

        self.channel_label = tk.Label(
            frame, text="", font=("", 10), fg="#555555",
            anchor="w"
        )
        self.channel_label.pack(fill="x")

        self.count_label = tk.Label(
            frame, text="", font=("", 9), fg="#999999",
            anchor="w"
        )
        self.count_label.pack(fill="x", pady=(2, 12))

        self.open_btn = tk.Button(
            frame,
            text="ランダムで開く",
            font=("", 12, "bold"),
            bg="#FF0000", fg="white",
            activebackground="#CC0000", activeforeground="white",
            relief="flat", padx=20, pady=8,
            cursor="hand2",
            command=self.open_random,
        )
        self.open_btn.pack()

        self.open_random()

    def open_random(self):
        if not self.remaining:
            self.remaining = self.videos.copy()

        video = random.choice(self.remaining)
        self.remaining.remove(video)

        url = f"https://www.youtube.com/watch?v={video['id']}"
        opened = len(self.videos) - len(self.remaining)

        self.title_label.config(text=video["title"])
        self.channel_label.config(text=video["channel"])
        self.count_label.config(text=f"{opened} / {len(self.videos)} 件目")

        webbrowser.open(url)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
