import ctypes
import json
import logging
import os
import random
import threading
import webbrowser
import pickle
import tkinter as tk
from tkinter import messagebox

ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

logging.basicConfig(
    filename="startup_update.log",
    level=logging.DEBUG,
    format="%(asctime)s %(message)s",
    encoding="utf-8",
)

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
CLIENT_SECRETS_FILE = "client_secrets.json"
TOKEN_FILE = "token.pickle"
SUBSCRIBED_CACHE_FILE = "subscribed_cache.json"
LIKED_CH_CACHE_FILE = "liked_channels_cache.json"
MAX_VIDEOS_PER_CHANNEL = 200

MODES = [
    ("登録チャンネル",        "subscribed",     "#1a73e8"),
    ("高評価・未登録CHの動画", "liked_channels", "#e8871a"),
    ("高く評価した動画",      "liked",          "#FF0000"),
]


# ── 認証 ────────────────────────────────────────────

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


# ── キャッシュ共通 ─────────────────────────────────────

def _load_cache(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"channels": {}}


def _save_cache(path, cache):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _cache_to_videos(cache):
    videos = []
    for ch_data in cache["channels"].values():
        videos.extend(ch_data["videos"])
    random.shuffle(videos)
    return videos


# ── API ヘルパー ─────────────────────────────────────

def get_all_liked_videos(youtube, progress_cb):
    videos, next_token = [], None
    while True:
        resp = youtube.videos().list(
            part="id,snippet",
            myRating="like",
            maxResults=50,
            pageToken=next_token,
        ).execute()
        for item in resp.get("items", []):
            videos.append({
                "id": item["id"],
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"],
                "channel_id": item["snippet"]["channelId"],
                "order": len(videos),
            })
        progress_cb(f"高評価動画取得中... {len(videos)} 件")
        next_token = resp.get("nextPageToken")
        if not next_token:
            break
    return videos


def get_all_subscriptions(youtube, progress_cb):
    channels, next_token = [], None
    while True:
        resp = youtube.subscriptions().list(
            part="snippet", mine=True,
            maxResults=50, pageToken=next_token,
        ).execute()
        for item in resp.get("items", []):
            channels.append({
                "channel_id": item["snippet"]["resourceId"]["channelId"],
                "name": item["snippet"]["title"],
                "published_at": item["snippet"].get("publishedAt", ""),
            })
        progress_cb(f"登録チャンネル取得中... {len(channels)} 件")
        next_token = resp.get("nextPageToken")
        if not next_token:
            break
    return channels


def get_uploads_playlist_id(youtube, channel_id):
    resp = youtube.channels().list(
        part="contentDetails", id=channel_id,
    ).execute()
    items = resp.get("items", [])
    if not items:
        return None
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def get_random_channel_video(youtube, playlist_id):
    resp = youtube.playlistItems().list(
        part="snippet", playlistId=playlist_id, maxResults=50,
    ).execute()
    page = []
    for item in resp.get("items", []):
        s = item["snippet"]
        vid = s.get("resourceId", {}).get("videoId")
        if vid:
            page.append({"id": vid, "title": s["title"], "channel": s["channelTitle"]})
    token = resp.get("nextPageToken")

    for _ in range(2):
        if not token or random.random() < 0.5:
            break
        resp = youtube.playlistItems().list(
            part="snippet", playlistId=playlist_id,
            maxResults=50, pageToken=token,
        ).execute()
        new_page = []
        for item in resp.get("items", []):
            s = item["snippet"]
            vid = s.get("resourceId", {}).get("videoId")
            if vid:
                new_page.append({"id": vid, "title": s["title"], "channel": s["channelTitle"]})
        if new_page:
            page = new_page
        token = resp.get("nextPageToken")

    return random.choice(page) if page else None


def fetch_channel_videos(youtube, playlist_id, known_ids=None, max_videos=MAX_VIDEOS_PER_CHANNEL):
    videos = []
    next_token = None
    known_ids = known_ids or set()

    while len(videos) < max_videos:
        resp = youtube.playlistItems().list(
            part="snippet", playlistId=playlist_id,
            maxResults=50, pageToken=next_token,
        ).execute()
        hit_known = False
        for item in resp.get("items", []):
            s = item["snippet"]
            vid = s.get("resourceId", {}).get("videoId")
            if not vid:
                continue
            if vid in known_ids:
                hit_known = True
                break
            videos.append({"id": vid, "title": s["title"], "channel": s["channelTitle"]})

        next_token = resp.get("nextPageToken")
        if hit_known or not next_token or len(videos) >= max_videos:
            break

    return videos


# ── プールビルダー ────────────────────────────────────

def build_liked_pool(youtube, progress_cb):
    videos = get_all_liked_videos(youtube, progress_cb)
    random.shuffle(videos)
    return videos


# ── 登録チャンネル ────────────────────────────────────

def build_subscribed_pool(youtube, subs, progress_cb):
    cache = _load_cache(SUBSCRIBED_CACHE_FILE)
    if cache["channels"]:
        return _cache_to_videos(cache)

    total = len(subs)
    for i, ch in enumerate(subs):
        cid, name = ch["channel_id"], ch["name"]
        progress_cb(f"初回取得中... {i+1}/{total}\n{name}")
        try:
            pid = get_uploads_playlist_id(youtube, cid)
            if pid:
                cache["channels"][cid] = {"name": name, "videos": fetch_channel_videos(youtube, pid)}
        except Exception:
            continue

    _save_cache(SUBSCRIBED_CACHE_FILE, cache)
    return _cache_to_videos(cache)


def update_subscribed_cache(youtube, subs, progress_cb):
    cache = _load_cache(SUBSCRIBED_CACHE_FILE)
    total = len(subs)
    for i, ch in enumerate(subs):
        cid, name = ch["channel_id"], ch["name"]
        progress_cb(f"新着確認中... {i+1}/{total}\n{name}")
        try:
            pid = get_uploads_playlist_id(youtube, cid)
            if not pid:
                continue
            if cid in cache["channels"]:
                known_ids = {v["id"] for v in cache["channels"][cid]["videos"]}
                new_videos = fetch_channel_videos(youtube, pid, known_ids=known_ids)
                if new_videos:
                    cache["channels"][cid]["videos"] = new_videos + cache["channels"][cid]["videos"]
            else:
                cache["channels"][cid] = {"name": name, "videos": fetch_channel_videos(youtube, pid)}
        except Exception:
            continue

    _save_cache(SUBSCRIBED_CACHE_FILE, cache)
    return _cache_to_videos(cache)


# ── 高評価・未登録チャンネル ──────────────────────────────

def _get_unsubscribed_liked_channels(youtube, subs, progress_cb):
    """高評価動画から未登録チャンネルのリストを返す。"""
    all_liked = get_all_liked_videos(youtube, progress_cb)

    if not subs:
        progress_cb("登録チャンネル取得中...")
        subs = get_all_subscriptions(youtube, lambda t: progress_cb(t))

    subscribed_ids = {ch["channel_id"] for ch in subs}
    seen = set()
    channels = []
    for v in all_liked:
        cid = v.get("channel_id")
        if cid and cid not in seen and cid not in subscribed_ids:
            seen.add(cid)
            channels.append({"channel_id": cid, "name": v["channel"]})
    return channels


def build_liked_channels_pool(youtube, subs, progress_cb):
    cache = _load_cache(LIKED_CH_CACHE_FILE)
    if cache["channels"]:
        return _cache_to_videos(cache)

    # 初回: 未登録チャンネルを全件取得
    channels = _get_unsubscribed_liked_channels(youtube, subs, progress_cb)
    total = len(channels)
    for i, ch in enumerate(channels):
        cid, name = ch["channel_id"], ch["name"]
        progress_cb(f"初回取得中... {i+1}/{total}\n{name}")
        try:
            pid = get_uploads_playlist_id(youtube, cid)
            if pid:
                cache["channels"][cid] = {"name": name, "videos": fetch_channel_videos(youtube, pid)}
        except Exception:
            continue

    _save_cache(LIKED_CH_CACHE_FILE, cache)
    return _cache_to_videos(cache)


def update_liked_channels_cache(youtube, subs, progress_cb):
    """高評価・未登録CH 再読込: 新規チャンネル追加 + 既存チャンネル新着差分取得。"""
    cache = _load_cache(LIKED_CH_CACHE_FILE)
    channels = _get_unsubscribed_liked_channels(youtube, subs, progress_cb)
    total = len(channels)

    for i, ch in enumerate(channels):
        cid, name = ch["channel_id"], ch["name"]
        progress_cb(f"新着確認中... {i+1}/{total}\n{name}")
        try:
            pid = get_uploads_playlist_id(youtube, cid)
            if not pid:
                continue
            if cid in cache["channels"]:
                known_ids = {v["id"] for v in cache["channels"][cid]["videos"]}
                new_videos = fetch_channel_videos(youtube, pid, known_ids=known_ids)
                if new_videos:
                    cache["channels"][cid]["videos"] = new_videos + cache["channels"][cid]["videos"]
            else:
                cache["channels"][cid] = {"name": name, "videos": fetch_channel_videos(youtube, pid)}
        except Exception:
            continue

    _save_cache(LIKED_CH_CACHE_FILE, cache)
    return _cache_to_videos(cache)


# ── GUI ─────────────────────────────────────────────

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube ランダム動画")
        self.root.resizable(False, False)

        self.youtube = None
        self.subscriptions = []
        self.pools = {m[1]: [] for m in MODES}
        self.remaining = {m[1]: [] for m in MODES}
        self.current_mode = None
        self._building = set()

        self._center_window(334, 420)
        self._build_loading_ui()
        self.root.after(100, self._start_auth)

    def _center_window(self, w, h):
        self.root.geometry(f"{w}x{h}")
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _build_loading_ui(self):
        self._loading_frame = tk.Frame(self.root)
        self._loading_frame.pack(fill="both", expand=True)
        self._progress_label = tk.Label(
            self._loading_frame, text="認証中...",
            font=("", 11), justify="center",
        )
        self._progress_label.pack(expand=True)

    def _ui(self, fn):
        self.root.after(0, fn)

    def _start_auth(self):
        if not os.path.exists(CLIENT_SECRETS_FILE):
            messagebox.showerror("エラー", f"{CLIENT_SECRETS_FILE} が見つかりません。\nREADME.md の手順で認証情報を取得してください。")
            self.root.destroy()
            return

        def worker():
            try:
                yt = authenticate()
            except Exception as e:
                self._ui(lambda: messagebox.showerror("エラー", str(e)))
                self._ui(self.root.destroy)
                return
            self.youtube = yt
            self._ui(self._finish_auth)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_auth(self):
        self._loading_frame.destroy()
        self._build_main_ui()
        self._load_pools_from_cache()
        self._start_liked_prefetch()
        # _start_startup_update は liked_prefetch 完了後に直列で呼ばれる

    def _load_pools_from_cache(self):
        sub_cache = _load_cache(SUBSCRIBED_CACHE_FILE)
        if sub_cache["channels"]:
            vids = _cache_to_videos(sub_cache)
            self.pools["subscribed"] = vids
            self.remaining["subscribed"] = vids.copy()

        lch_cache = _load_cache(LIKED_CH_CACHE_FILE)
        if lch_cache["channels"]:
            vids = _cache_to_videos(lch_cache)
            self.pools["liked_channels"] = vids
            self.remaining["liked_channels"] = vids.copy()

    def _start_startup_update(self):
        """起動時: 新規チャンネルのみ差分取得してキャッシュ・プールを更新する"""
        sub_cache = _load_cache(SUBSCRIBED_CACHE_FILE)
        lch_cache = _load_cache(LIKED_CH_CACHE_FILE)
        has_sub = bool(sub_cache["channels"])
        has_lch = bool(lch_cache["channels"])
        if not has_sub and not has_lch:
            return

        if has_sub:
            self._building.add("subscribed")
        if has_lch:
            self._building.add("liked_channels")

        def prog(text):
            self._ui(lambda t=text: self._mode_label.config(text=t))

        def worker():
            try:
                prog("新規チャンネル確認中...")
                subs = get_all_subscriptions(self.youtube, prog)
                self.subscriptions = subs
                sub_ids = {ch["channel_id"] for ch in subs}

                if has_sub:
                    # 登録解除されたチャンネルを削除
                    removed_subs = [cid for cid in list(sub_cache["channels"]) if cid not in sub_ids]
                    for cid in removed_subs:
                        logging.info(f"登録解除CH削除: {sub_cache['channels'][cid].get('name', cid)}")
                        del sub_cache["channels"][cid]

                    new_subs = [ch for ch in subs if ch["channel_id"] not in sub_cache["channels"]]
                    logging.info(f"登録CH新規: {len(new_subs)} 件 / 削除: {len(removed_subs)} 件 (API: {len(subs)}, cache: {len(sub_cache['channels'])})")
                    for i, ch in enumerate(new_subs):
                        cid, name = ch["channel_id"], ch["name"]
                        prog(f"新規登録CH {i+1}/{len(new_subs)}: {name}")
                        try:
                            pid = get_uploads_playlist_id(self.youtube, cid)
                            if pid:
                                sub_cache["channels"][cid] = {
                                    "name": name,
                                    "videos": fetch_channel_videos(self.youtube, pid),
                                }
                                logging.info(f"登録CH追加: {name}")
                        except Exception as e:
                            logging.error(f"登録CH失敗 {name}: {e}")
                    if removed_subs or new_subs:
                        _save_cache(SUBSCRIBED_CACHE_FILE, sub_cache)
                        vids = _cache_to_videos(sub_cache)
                        self.pools["subscribed"] = vids
                        self.remaining["subscribed"] = vids.copy()
                    self._building.discard("subscribed")
                    if self.current_mode == "subscribed":
                        self._ui(lambda: self._open_next("subscribed"))

                if has_lch:
                    prog("高評価新規CHチェック中...")
                    # liked_prefetch が取得済みの動画をそのまま再利用（API 二重コール不要）
                    liked_vids = self.pools.get("liked") or get_all_liked_videos(self.youtube, prog)
                    seen, liked_chs = set(), []
                    for v in liked_vids:
                        cid = v.get("channel_id")
                        if cid and cid not in seen and cid not in sub_ids:
                            seen.add(cid)
                            liked_chs.append({"channel_id": cid, "name": v["channel"]})

                    liked_ch_ids = {ch["channel_id"] for ch in liked_chs}

                    # 高評価解除 or 登録済みになったチャンネルを削除
                    removed_lch = [cid for cid in list(lch_cache["channels"]) if cid not in liked_ch_ids]
                    for cid in removed_lch:
                        logging.info(f"高評価CH削除: {lch_cache['channels'][cid].get('name', cid)}")
                        del lch_cache["channels"][cid]

                    new_lch = [ch for ch in liked_chs if ch["channel_id"] not in lch_cache["channels"]]
                    logging.info(f"高評価未登録CH新規: {len(new_lch)} 件 / 削除: {len(removed_lch)} 件")
                    for i, ch in enumerate(new_lch):
                        cid, name = ch["channel_id"], ch["name"]
                        prog(f"新規高評価CH {i+1}/{len(new_lch)}: {name}")
                        try:
                            pid = get_uploads_playlist_id(self.youtube, cid)
                            if pid:
                                lch_cache["channels"][cid] = {
                                    "name": name,
                                    "videos": fetch_channel_videos(self.youtube, pid),
                                }
                                logging.info(f"高評価CH追加: {name}")
                        except Exception as e:
                            logging.error(f"高評価CH失敗 {name}: {e}")
                    if removed_lch or new_lch:
                        _save_cache(LIKED_CH_CACHE_FILE, lch_cache)
                        vids = _cache_to_videos(lch_cache)
                        self.pools["liked_channels"] = vids
                        self.remaining["liked_channels"] = vids.copy()
                    self._building.discard("liked_channels")
                    if self.current_mode == "liked_channels":
                        self._ui(lambda: self._open_next("liked_channels"))

            except Exception as e:
                logging.error(f"起動時更新エラー: {e}", exc_info=True)
                self._ui(lambda err=e: messagebox.showerror("起動時更新エラー", str(err)))
                self._building.discard("subscribed")
                self._building.discard("liked_channels")

            self._ui(lambda: self._mode_label.config(text="← ボタンを押して動画を開く"))

        threading.Thread(target=worker, daemon=True).start()

    def _start_liked_prefetch(self):
        self._building.add("liked")

        def worker():
            try:
                videos = get_all_liked_videos(
                    self.youtube,
                    lambda t: self._ui(lambda t=t: self._mode_label.config(text=t)),
                )
            except Exception:
                self._building.discard("liked")
                self._ui(self._start_startup_update)
                return
            self._building.discard("liked")
            random.shuffle(videos)
            self.pools["liked"] = videos
            self.remaining["liked"] = videos.copy()
            if self.current_mode == "liked":
                self._ui(lambda: self._open_next("liked"))
            # liked 完了後に直列で起動チェック開始（SSL 競合回避）
            self._ui(self._start_startup_update)

        threading.Thread(target=worker, daemon=True).start()

    # ── メイン画面 ────────────────────────────────────

    def _build_main_ui(self):
        info = tk.Frame(self.root, padx=16, pady=10)
        info.pack(fill="x")

        self._mode_label = tk.Label(
            info, text="← ボタンを押して動画を開く",
            font=("", 9), fg="#aaaaaa", anchor="w",
        )
        self._mode_label.pack(fill="x")

        title_wrap = tk.Frame(info, height=52)
        title_wrap.pack(fill="x")
        title_wrap.pack_propagate(False)

        self._title_label = tk.Label(
            title_wrap, text="", font=("", 11, "bold"),
            wraplength=302, justify="left", anchor="nw",
        )
        self._title_label.place(relwidth=1, relheight=1)

        self._channel_label = tk.Label(
            info, text="", font=("", 10), fg="#555555", anchor="w",
        )
        self._channel_label.pack(fill="x")

        self._count_label = tk.Label(
            info, text="", font=("", 9), fg="#bbbbbb", anchor="w",
        )
        self._count_label.pack(fill="x")

        tk.Frame(self.root, height=1, bg="#e0e0e0").pack(fill="x")

        # ── モードボタン列 ──
        btn_frame = tk.Frame(self.root, padx=14, pady=8)
        btn_frame.pack(fill="x")

        for label, key, color in MODES:
            tk.Button(
                btn_frame, text=label,
                font=("", 10, "bold"),
                bg=color, fg="white",
                activebackground=color, activeforeground="white",
                relief="flat", padx=10, pady=6, cursor="hand2",
                command=lambda k=key: self._on_mode(k),
            ).pack(fill="x", pady=(0, 4))

        tk.Frame(self.root, height=1, bg="#f0f0f0").pack(fill="x")

        # ── 登録チャンネル用ツール行 ──
        sub_frame = tk.Frame(self.root, padx=14, pady=5)
        sub_frame.pack(fill="x")

        tk.Label(sub_frame, text="登録チャンネル:", font=("", 8), fg="#1a73e8").pack(side="left", padx=(0, 5))
        tk.Button(sub_frame, text="全チャンネル新規動画更新", font=("", 9),
                  bg="#ddeeff", fg="#1a73e8", activebackground="#c5e0ff",
                  relief="flat", padx=8, pady=3, cursor="hand2",
                  command=self._refresh_subscribed).pack(side="left", padx=(0, 4))
        tk.Button(sub_frame, text="一覧", font=("", 9),
                  bg="#ddeeff", fg="#1a73e8", activebackground="#c5e0ff",
                  relief="flat", padx=8, pady=3, cursor="hand2",
                  command=self._show_subscriptions_list).pack(side="left")

        tk.Frame(self.root, height=1, bg="#f0f0f0").pack(fill="x")

        # ── 高評価・未登録CH用ツール行 ──
        lch_frame = tk.Frame(self.root, padx=14, pady=5)
        lch_frame.pack(fill="x")

        tk.Label(lch_frame, text="高評価・未登録CH:", font=("", 8), fg="#e8871a").pack(side="left", padx=(0, 5))
        tk.Button(lch_frame, text="全チャンネル新規動画更新", font=("", 9),
                  bg="#fdeedd", fg="#e8871a", activebackground="#fad9b5",
                  relief="flat", padx=8, pady=3, cursor="hand2",
                  command=self._refresh_liked_channels).pack(side="left", padx=(0, 4))

        tk.Frame(self.root, height=1, bg="#f0f0f0").pack(fill="x")

        # ── 高く評価した動画用ツール行 ──
        liked_frame = tk.Frame(self.root, padx=14, pady=5)
        liked_frame.pack(fill="x")

        tk.Label(liked_frame, text="高く評価した動画:", font=("", 8), fg="#FF0000").pack(side="left", padx=(0, 5))
        tk.Button(liked_frame, text="一覧", font=("", 9),
                  bg="#ffdddd", fg="#FF0000", activebackground="#ffbbbb",
                  relief="flat", padx=8, pady=3, cursor="hand2",
                  command=self._show_liked_list).pack(side="left")

    # ── モード選択 ────────────────────────────────────

    def _on_mode(self, mode_key):
        self.current_mode = mode_key
        if mode_key in self._building:
            self._title_label.config(text="新規チャンネル確認中... 完了後に自動で開きます")
            self._channel_label.config(text="")
            self._count_label.config(text="")
        elif self.pools[mode_key]:
            self._open_next(mode_key)
        else:
            self._start_pool_build(mode_key)

    def _start_pool_build(self, mode_key):
        self._building.add(mode_key)

        def prog(text):
            self._ui(lambda t=text: (
                self._title_label.config(text=t),
                self._channel_label.config(text=""),
                self._count_label.config(text=""),
            ))

        def worker():
            try:
                if mode_key == "subscribed" and not self.subscriptions:
                    existing = _load_cache(SUBSCRIBED_CACHE_FILE)
                    if not existing["channels"]:
                        self.subscriptions = get_all_subscriptions(self.youtube, prog)
                if mode_key == "liked":
                    videos = build_liked_pool(self.youtube, prog)
                elif mode_key == "liked_channels":
                    videos = build_liked_channels_pool(self.youtube, self.subscriptions, prog)
                else:
                    videos = build_subscribed_pool(self.youtube, self.subscriptions, prog)
            except Exception as e:
                self._building.discard(mode_key)
                self._ui(lambda: messagebox.showerror("エラー", str(e)))
                return
            self._building.discard(mode_key)
            if not videos:
                self._ui(lambda: messagebox.showinfo("情報", "動画が見つかりませんでした。"))
                return
            self.pools[mode_key] = videos
            self.remaining[mode_key] = videos.copy()
            self._ui(lambda k=mode_key: self._open_next(k))

        threading.Thread(target=worker, daemon=True).start()

    def _open_next(self, mode_key):
        if not self.remaining[mode_key]:
            random.shuffle(self.pools[mode_key])
            self.remaining[mode_key] = self.pools[mode_key].copy()

        video = self.remaining[mode_key].pop()
        total = len(self.pools[mode_key])
        done = total - len(self.remaining[mode_key])

        mode_name = next(lbl for lbl, k, _ in MODES if k == mode_key)
        self._mode_label.config(text=f"[{mode_name}]  {done} / {total} 件")
        self._title_label.config(text=video["title"])
        self._channel_label.config(text=video["channel"])
        self._count_label.config(text="")

        webbrowser.open(f"https://www.youtube.com/watch?v={video['id']}")

    def _run_refresh(self, mode_key, fn):
        if mode_key in self._building:
            return
        self._building.add(mode_key)

        def prog(text):
            self._ui(lambda t=text: (
                self._title_label.config(text=t),
                self._channel_label.config(text=""),
                self._count_label.config(text=""),
            ))

        def worker():
            try:
                videos = fn(prog)
            except Exception as e:
                self._building.discard(mode_key)
                self._ui(lambda: messagebox.showerror("エラー", str(e)))
                return
            self._building.discard(mode_key)
            self.pools[mode_key] = videos
            self.remaining[mode_key] = videos.copy()
            if self.current_mode == mode_key:
                self._ui(lambda: self._open_next(mode_key))

        threading.Thread(target=worker, daemon=True).start()

    def _ensure_subscriptions(self, prog):
        if not self.subscriptions:
            self.subscriptions = get_all_subscriptions(self.youtube, prog)

    def _refresh_subscribed(self):
        def fn(prog):
            self._ensure_subscriptions(prog)
            return update_subscribed_cache(self.youtube, self.subscriptions, prog)
        self._run_refresh("subscribed", fn)

    def _refresh_liked_channels(self):
        def fn(prog):
            self._ensure_subscriptions(prog)
            return update_liked_channels_cache(self.youtube, self.subscriptions, prog)
        self._run_refresh("liked_channels", fn)

    def _show_subscriptions_list(self):
        if not self.subscriptions:
            if "subscribed" in self._building:
                messagebox.showinfo("情報", "読み込み中です。しばらくお待ちください。")
                return
            self._building.add("_subs_list")
            self._title_label.config(text="登録チャンネル取得中...")
            self._channel_label.config(text="")

            def worker():
                try:
                    self.subscriptions = get_all_subscriptions(
                        self.youtube,
                        lambda t: self._ui(lambda t=t: self._title_label.config(text=t)),
                    )
                except Exception as e:
                    self._building.discard("_subs_list")
                    self._ui(lambda: messagebox.showerror("エラー", str(e)))
                    return
                self._building.discard("_subs_list")
                self._ui(self._show_subscriptions_list)

            threading.Thread(target=worker, daemon=True).start()
            return

        win = tk.Toplevel(self.root)
        win.title(f"登録チャンネル一覧 (新しい順) — {len(self.subscriptions)} 件")
        win.geometry("360x500")

        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")

        listbox = tk.Listbox(
            frame, font=("", 10), selectmode="single",
            yscrollcommand=scrollbar.set, activestyle="none",
            cursor="hand2",
        )
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=listbox.yview)

        sorted_subs = sorted(self.subscriptions, key=lambda c: c.get("published_at", ""), reverse=True)
        for i, ch in enumerate(sorted_subs, 1):
            listbox.insert("end", f"{i}. {ch['name']}")

        def on_select(event):
            sel = listbox.curselection()
            if not sel:
                return
            ch = sorted_subs[sel[0]]
            webbrowser.open(f"https://www.youtube.com/channel/{ch['channel_id']}")

        listbox.bind("<<ListboxSelect>>", on_select)

    def _show_liked_list(self):
        if not self.pools.get("liked"):
            messagebox.showinfo("情報", "取得中です。完了後にもう一度押してください。")
            return

        videos = self.pools["liked"]
        sorted_videos = sorted(videos, key=lambda v: v.get("order", 0))

        win = tk.Toplevel(self.root)
        win.title(f"高く評価した動画 — {len(sorted_videos)} 件")
        win.geometry("480x520")

        search_frame = tk.Frame(win, padx=8, pady=6)
        search_frame.pack(fill="x")
        tk.Label(search_frame, text="検索:", font=("", 9)).pack(side="left", padx=(0, 4))
        search_var = tk.StringVar()
        search_entry = tk.Entry(search_frame, textvariable=search_var, font=("", 10))
        search_entry.pack(side="left", fill="x", expand=True)

        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")

        listbox = tk.Listbox(
            frame, font=("", 9), selectmode="single",
            yscrollcommand=scrollbar.set, activestyle="none",
            cursor="hand2",
        )
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=listbox.yview)

        filtered = list(sorted_videos)

        def _refresh_list(*_):
            nonlocal filtered
            q = search_var.get().lower()
            filtered = [v for v in sorted_videos if q in v["title"].lower() or q in v["channel"].lower()]
            listbox.delete(0, "end")
            for i, v in enumerate(filtered, 1):
                listbox.insert("end", f"{i}. {v['title']}  [{v['channel']}]")

        _refresh_list()
        search_var.trace_add("write", _refresh_list)

        def on_select(event):
            sel = listbox.curselection()
            if not sel:
                return
            v = filtered[sel[0]]
            webbrowser.open(f"https://www.youtube.com/watch?v={v['id']}")

        listbox.bind("<<ListboxSelect>>", on_select)



def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
