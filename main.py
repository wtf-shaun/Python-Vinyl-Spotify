import base64
import hashlib
import json
import math
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
import tkinter as tk
import tkinter.font as tkfont
from PIL import Image, ImageDraw, ImageTk, ImageFilter

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ============================================================
# CONFIG
# ============================================================

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()

# Spotify requires this exact URI to be registered in your app.
# 127.0.0.1 is a loopback address and is suitable for a desktop app.
REDIRECT_URI = "http://127.0.0.1:8888/callback"

SCOPES = "user-read-currently-playing user-read-playback-state"

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

POLL_MS = 3000
VINYL_FRAME_MS = 16
VINYL_FPS = 60
PROGRESS_TICK_MS = 250


# ============================================================
# PKCE / SPOTIFY AUTH
# ============================================================

def make_pkce():
    verifier = secrets.token_urlsafe(64)
    verifier = verifier[:128]

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    return verifier, challenge


class CallbackHandler(BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)

        CallbackHandler.result = {
            "code": params.get("code", [None])[0],
            "state": params.get("state", [None])[0],
            "error": params.get("error", [None])[0],
        }

        page = """
        <!doctype html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Spotify Login</title>
            <style>
                body {
                    background:#111;
                    color:#fff;
                    font-family:Arial,sans-serif;
                    text-align:center;
                    padding-top:80px;
                }
                h1 { color:#1ed760; }
            </style>
        </head>
        <body>
            <h1>Spotify connected!</h1>
            <p>You can close this browser tab and return to the app.</p>
        </body>
        </html>
        """

        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class SpotifyClient:
    def __init__(self):
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = 0

        self.verifier = None
        self.state = None

        self.server = None
        self.server_thread = None

    def _start_callback_server(self):
        CallbackHandler.result = {}

        self.server = HTTPServer(("127.0.0.1", 8888), CallbackHandler)
        self.server.timeout = 1

        def run():
            deadline = time.time() + 180

            while time.time() < deadline and not CallbackHandler.result:
                self.server.handle_request()

            try:
                self.server.server_close()
            except Exception:
                pass

        self.server_thread = threading.Thread(target=run, daemon=True)
        self.server_thread.start()

    def login(self):
        if not CLIENT_ID:
            raise RuntimeError(
                "SPOTIFY_CLIENT_ID is missing. Put your Spotify Client ID "
                "in the .env file."
            )

        self.verifier, challenge = make_pkce()
        self.state = secrets.token_urlsafe(32)

        self._start_callback_server()

        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "scope": SCOPES,
            "redirect_uri": REDIRECT_URI,
            "state": self.state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }

        url = AUTH_URL + "?" + urllib.parse.urlencode(params)
        webbrowser.open(url)

        # Wait without freezing the Tkinter UI.
        while self.server_thread.is_alive():
            if CallbackHandler.result:
                break
            time.sleep(0.1)

        result = CallbackHandler.result

        if not result:
            raise RuntimeError("Spotify login timed out.")

        if result.get("error"):
            raise RuntimeError("Spotify authorization failed: " + result["error"])

        if result.get("state") != self.state:
            raise RuntimeError("Invalid OAuth state.")

        code = result.get("code")
        if not code:
            raise RuntimeError("Spotify did not return an authorization code.")

        response = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "code_verifier": self.verifier,
            },
            timeout=15,
        )

        if not response.ok:
            raise RuntimeError(
                f"Token request failed ({response.status_code}): {response.text}"
            )

        data = response.json()

        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token")
        self.token_expires_at = time.time() + data.get("expires_in", 3600) - 60

    def refresh(self):
        if not self.refresh_token:
            return False

        response = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": CLIENT_ID,
            },
            timeout=15,
        )

        if not response.ok:
            return False

        data = response.json()
        self.access_token = data["access_token"]

        # Spotify may omit a new refresh token. Keep the old one.
        if data.get("refresh_token"):
            self.refresh_token = data["refresh_token"]

        self.token_expires_at = time.time() + data.get("expires_in", 3600) - 60
        return True

    def ensure_token(self):
        if not self.access_token:
            return False

        if time.time() >= self.token_expires_at:
            return self.refresh()

        return True

    def api_get(self, endpoint):
        if not self.ensure_token():
            raise RuntimeError("Not authenticated.")

        response = requests.get(
            API_BASE + endpoint,
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=15,
        )

        if response.status_code == 401:
            if self.refresh():
                response = requests.get(
                    API_BASE + endpoint,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    timeout=15,
                )

        if response.status_code == 204:
            return None

        if not response.ok:
            raise RuntimeError(
                f"Spotify API error ({response.status_code}): {response.text}"
            )

        return response.json()

    def currently_playing(self):
        return self.api_get("/me/player/currently-playing")

    def queue(self):
        return self.api_get("/me/player/queue")


# ============================================================
# VINYL GENERATOR
# ============================================================

def crop_square(image):
    image = image.convert("RGB")
    w, h = image.size
    side = min(w, h)

    left = (w - side) // 2
    top = (h - side) // 2

    return image.crop((left, top, left + side, top + side))


def circle_mask(size):
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size - 1, size - 1), fill=255)
    return mask


def extract_ambient_palette(image, count=3):
    """Extract a few vivid colors from album artwork for the vinyl glow."""
    sample = crop_square(image).resize((40, 40), Image.Resampling.BILINEAR)
    colors = sample.getcolors(sample.width * sample.height)

    ranked = sorted(
        colors,
        key=lambda entry: entry[0] * (max(entry[1]) - min(entry[1]) + 24),
        reverse=True,
    )

    palette = []
    for _, color in ranked:
        if max(color) < 35:
            continue
        if all(sum(abs(color[index] - chosen[index]) for index in range(3)) > 55
               for chosen in palette):
            palette.append(color)
        if len(palette) == count:
            break

    while len(palette) < count:
        palette.append((30, 215, 96))

    return palette


def blend_color(first, second, amount):
    amount = max(0.0, min(1.0, amount))
    return "#%02x%02x%02x" % tuple(
        int(first[index] + (second[index] - first[index]) * amount)
        for index in range(3)
    )


def make_vinyl_base(cover, size=430):
    cover = crop_square(cover).resize((size, size), Image.Resampling.LANCZOS)

    record = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(record)

    # Shadow
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.ellipse((12, 18, size - 2, size + 8), fill=(0, 0, 0, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    record.alpha_composite(shadow)

    # Record
    draw.ellipse((4, 4, size - 4, size - 4), fill=(18, 18, 20, 255))

    # Grooves
    center = size // 2
    groove_stop = max(int(size * 0.30), 8)
    for r in range(size // 2 - 8, groove_stop, -7):
        draw.ellipse(
            (center-r, center-r, center+r, center+r),
            outline=(40, 40, 43, 150),
            width=1,
        )

    # Slight highlight
    draw.arc(
        (10, 10, size - 10, size - 10),
        start=200,
        end=330,
        fill=(85, 85, 90, 80),
        width=2,
    )

    # Circular album label / artwork
    label_size = int(size * 0.57)
    cover = cover.resize((label_size, label_size), Image.Resampling.LANCZOS)

    label_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    label_layer.paste(
        cover,
        (
            center - label_size // 2,
            center - label_size // 2,
        ),
        circle_mask(label_size),
    )

    record.alpha_composite(label_layer)

    # Center label
    draw = ImageDraw.Draw(record)
    label_r = int(size * 0.07)
    draw.ellipse(
        (
            center-label_r,
            center-label_r,
            center+label_r,
            center+label_r,
        ),
        fill=(25, 25, 27, 255),
    )

    hole_r = 5
    draw.ellipse(
        (
            center-hole_r,
            center-hole_r,
            center+hole_r,
            center+hole_r,
        ),
        fill=(210, 210, 210, 255),
    )

    return record


def make_vinyl_frames(cover, size=430, frames=72):
    base = make_vinyl_base(cover, size)
    result = []

    for i in range(frames):
        angle = -(360 / frames) * i
        rotated = base.rotate(
            angle,
            resample=Image.Resampling.BICUBIC,
            expand=False,
        )
        result.append(rotated)

    return result


# ============================================================
# UI THEME
# ============================================================

BG = "#0b0b0d"
CARD_BG = "#111114"
BORDER = "#1c1c20"
ACCENT = "#1ed760"
ACCENT_HOVER = "#26e768"
ACCENT_DISABLED = "#1a5c33"
ACCENT_TEXT = "#06210f"
TEXT_PRIMARY = "#ffffff"
TEXT_SECONDARY = "#bdbdc4"
TEXT_MUTED = "#77777f"
TEXT_FAINT = "#55555c"

STATUS_COLORS = {
    "idle": "#55555c",
    "info": "#f5c451",
    "success": "#1ed760",
    "error": "#ff5c5c",
}


def pick_font_family(root):
    """Pick the best-looking available font for the current OS."""
    families = set(tkfont.families(root))
    for candidate in (
        "Segoe UI", "SF Pro Text", "Helvetica Neue",
        "Ubuntu", "Noto Sans", "Cantarell", "Arial",
    ):
        if candidate in families:
            return candidate
    return "TkDefaultFont"


def center_window(root, width, height):
    root.update_idletasks()
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 3)
    root.geometry(f"{width}x{height}+{x}+{y}")


def place_bottom_right(root, width, height, margin=24, taskbar_allowance=56):
    """Tuck the window into the bottom-right corner, like a widget."""
    root.update_idletasks()
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    x = max(0, screen_w - width - margin)
    y = max(0, screen_h - height - margin - taskbar_allowance)
    root.geometry(f"{width}x{height}+{x}+{y}")


def rounded_rect_points(x1, y1, x2, y2, r):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    return [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]


class RoundedButton(tk.Canvas):
    """A pill-shaped, hover-aware button drawn on a Canvas.

    Automatically widens/narrows to fit whatever text is set on it, so
    longer state labels (e.g. "Connected") never get clipped.
    """

    def __init__(self, parent, text, command, *, width=88, height=28,
                 radius=14, bg, fill, fill_hover, fill_disabled,
                 fg, fg_disabled, font, h_padding=20, max_width=180):
        self.font = font
        self.h_padding = h_padding
        self.min_width = width
        self.max_width = max_width
        self.h = height
        self.radius = radius
        self.text = text

        computed_width = self._measure_width(text)

        super().__init__(
            parent, width=computed_width, height=height, bg=bg,
            highlightthickness=0, cursor="hand2",
        )
        self.command = command
        self.w = computed_width
        self.fill = fill
        self.fill_hover = fill_hover
        self.fill_disabled = fill_disabled
        self.fg = fg
        self.fg_disabled = fg_disabled
        self.enabled = True
        self._hover = False

        self._render()

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _measure_width(self, text):
        needed = tkfont.Font(font=self.font).measure(text) + self.h_padding
        return max(self.min_width, min(needed, self.max_width))

    def _render(self):
        self.delete("all")

        if not self.enabled:
            fill = self.fill_disabled
            fg = self.fg_disabled
        elif self._hover:
            fill = self.fill_hover
            fg = self.fg
        else:
            fill = self.fill
            fg = self.fg

        points = rounded_rect_points(1, 1, self.w - 1, self.h - 1, self.radius)
        self.create_polygon(points, smooth=True, fill=fill, outline="")
        self.create_text(
            self.w // 2, self.h // 2,
            text=self.text, fill=fg, font=self.font,
        )

    def _on_enter(self, _event):
        if self.enabled:
            self._hover = True
            self._render()

    def _on_leave(self, _event):
        self._hover = False
        self._render()

    def _on_click(self, _event):
        if self.enabled and self.command:
            self.command()

    def set_text(self, text):
        self.text = text
        new_width = self._measure_width(text)
        if new_width != self.w:
            self.w = new_width
            self.config(width=new_width)
        self._render()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.config(cursor="hand2" if enabled else "arrow")
        self._render()


def draw_rounded_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    return canvas.create_polygon(
        rounded_rect_points(x1, y1, x2, y2, r), smooth=True, **kwargs
    )


# ============================================================
# TKINTER APP
# ============================================================

class SpotifyVinylApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Spotify Vinyl")

        # Make it behave like a desktop widget rather than a normal window:
        # no title bar/border, always on top, no taskbar/alt-tab entry.
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            # Windows-only: extra hint to keep it out of the taskbar.
            self.root.attributes("-toolwindow", True)
        except tk.TclError:
            pass

        self.root.configure(bg=BG)
        try:
            self.root.configure(highlightthickness=1, highlightbackground=BORDER)
        except tk.TclError:
            pass

        self.root.minsize(260, 380)
        place_bottom_right(self.root, 340, 500)

        self._drag_x = 0
        self._drag_y = 0

        self.font_family = pick_font_family(self.root)

        self.spotify = SpotifyClient()

        self.frames = []
        self.frame_index = 0
        self.animation_job = None
        self.animation_started_at = None
        self.progress_job = None
        self.animation_running = True
        self.ambient_colors = [(30, 215, 96), (20, 120, 220), (160, 40, 180)]
        self.ambient_ids = []
        self.turntable_ids = []
        self.track_progress_ms = 0
        self.track_duration_ms = 0
        self.track_started_at = None
        self.track_is_playing = False
        self.cover_cache = {}
        self.palette_cache = {}
        self.photo = None
        self.last_track_id = None
        self.last_error = None

        self.title_var = tk.StringVar(value="Not connected")
        self.artist_var = tk.StringVar(value="Connect your Spotify account")
        self.album_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Ready")
        self.progress_var = tk.StringVar(value="0:00 / 0:00")
        self.device_var = tk.StringVar(value="")
        self.next_var = tk.StringVar(value="Next up: queue is empty")

        self._build_ui()
        self._show_placeholder()
        self.set_status("Ready", "idle")
        self.progress_job = self.root.after(PROGRESS_TICK_MS, self.tick_progress)

    def _build_ui(self):
        f = self.font_family

        # Compact header: small wordmark + a small round connect button
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=14, pady=(12, 8))
        header.grid_columnconfigure(0, weight=1)

        wordmark = tk.Frame(header, bg=BG)
        wordmark.grid(row=0, column=0, sticky="w")

        tk.Label(
            wordmark, text="SPOTIFY", font=(f, 9, "bold"),
            fg=ACCENT, bg=BG,
        ).pack(side="left")

        tk.Label(
            wordmark, text="VINYL", font=(f, 9, "bold"),
            fg=TEXT_PRIMARY, bg=BG,
        ).pack(side="left", padx=(4, 0))

        self.close_button = tk.Label(
            header, text="✕", font=(f, 10), fg=TEXT_MUTED, bg=BG, cursor="hand2",
        )
        self.close_button.grid(row=0, column=3, sticky="e")
        self.close_button.bind("<Button-1>", lambda e: self.root.destroy())
        self.close_button.bind(
            "<Enter>", lambda e: self.close_button.config(fg=TEXT_PRIMARY)
        )
        self.close_button.bind(
            "<Leave>", lambda e: self.close_button.config(fg=TEXT_MUTED)
        )

        self.login_button = RoundedButton(
            header,
            text="Connect",
            command=self.start_login,
            width=88, height=28, radius=14,
            bg=BG, fill=ACCENT, fill_hover=ACCENT_HOVER,
            fill_disabled=ACCENT_DISABLED,
            fg=ACCENT_TEXT, fg_disabled="#bfe8cf",
            font=(f, 9, "bold"),
        )
        self.login_button.grid(row=0, column=1, padx=(0, 8), sticky="e")

        self.refresh_button = RoundedButton(
            header,
            text="Refresh",
            command=self.refresh_now,
            width=72, height=28, radius=14,
            bg=BG, fill="#202024", fill_hover="#2b2b31",
            fill_disabled="#17171a",
            fg=TEXT_PRIMARY, fg_disabled=TEXT_MUTED,
            font=(f, 8, "bold"), h_padding=16,
        )
        self.refresh_button.grid(row=0, column=2, padx=(0, 6), sticky="e")

        # Thin accent divider under the header
        divider = tk.Frame(self.root, bg=BORDER, height=1)
        divider.pack(fill="x", padx=14)

        # Everything below is one narrow column: vinyl on top, info below
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=14, pady=(10, 12))

        self.canvas = tk.Canvas(
            main, bg=BG, highlightthickness=0, width=220, height=220,
        )
        self.canvas.pack()

        controls = tk.Frame(main, bg=BG)
        controls.pack(pady=(2, 0))

        self.animation_button = RoundedButton(
            controls,
            text="Pause vinyl",
            command=self.toggle_animation,
            width=94, height=24, radius=12,
            bg=BG, fill="#18181c", fill_hover="#27272d",
            fill_disabled="#151518",
            fg=TEXT_SECONDARY, fg_disabled=TEXT_MUTED,
            font=(f, 8), h_padding=14,
        )
        self.animation_button.pack()

        # No title bar means no OS drag handle — make the header and the
        # vinyl area draggable so the widget can still be repositioned.
        for widget in (header, wordmark, main, self.canvas):
            widget.bind("<ButtonPress-1>", self._start_move)
            widget.bind("<B1-Motion>", self._do_move)

        tk.Label(
            main, textvariable=self.title_var, font=(f, 13, "bold"),
            fg=TEXT_PRIMARY, bg=BG, wraplength=260, justify="center",
        ).pack(pady=(12, 0))

        tk.Label(
            main, textvariable=self.artist_var, font=(f, 10),
            fg=TEXT_SECONDARY, bg=BG, wraplength=260, justify="center",
        ).pack(pady=(3, 0))

        tk.Label(
            main, textvariable=self.album_var, font=(f, 8),
            fg=TEXT_MUTED, bg=BG, wraplength=260, justify="center",
        ).pack(pady=(2, 10))

        tk.Label(
            main, textvariable=self.next_var, font=(f, 8),
            fg="#85858d", bg=BG, wraplength=300, justify="center",
        ).pack(pady=(0, 8))

        tk.Label(
            main, textvariable=self.progress_var, font=(f, 8),
            fg="#9999a1", bg=BG,
        ).pack()

        self.progress_canvas = tk.Canvas(
            main, height=5, bg=BG, highlightthickness=0,
        )
        self.progress_canvas.pack(fill="x", pady=(6, 10))

        tk.Label(
            main, textvariable=self.device_var, font=(f, 8),
            fg="#66666d", bg=BG, wraplength=260, justify="center",
        ).pack()

        # Status row: coloured dot + text, pinned to the bottom
        status_row = tk.Frame(main, bg=BG)
        status_row.pack(side="bottom", pady=(10, 0))

        self.status_dot = tk.Canvas(
            status_row, width=8, height=8, bg=BG, highlightthickness=0,
        )
        self.status_dot.pack(side="left", pady=(2, 0))
        self.status_dot_id = self.status_dot.create_oval(
            0, 0, 8, 8, fill=STATUS_COLORS["idle"], outline="",
        )

        tk.Label(
            status_row, textvariable=self.status_var, font=(f, 8),
            fg=TEXT_FAINT, bg=BG, wraplength=180, justify="left",
        ).pack(side="left", padx=(6, 0))

    def set_status(self, text, kind="idle"):
        self.status_var.set(text)
        color = STATUS_COLORS.get(kind, STATUS_COLORS["idle"])
        self.status_dot.itemconfig(self.status_dot_id, fill=color)

    def _start_move(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _do_move(self, event):
        x = self.root.winfo_pointerx() - self._drag_x
        y = self.root.winfo_pointery() - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def _show_placeholder(self):
        self.canvas.delete("all")

        w = max(self.canvas.winfo_width(), 220)
        h = max(self.canvas.winfo_height(), 220)
        cx, cy = w // 2, h // 2
        r = min(w, h) // 2 - 14

        # Soft drop shadow
        self.canvas.create_oval(
            cx-r+4, cy-r+6, cx+r+4, cy+r+6,
            fill="#000000", outline="", stipple="gray25",
        )

        self.canvas.create_oval(
            cx-r, cy-r, cx+r, cy+r,
            fill="#17171a",
            outline="#28282d",
            width=2,
        )

        label_r = int(r * 0.42)
        groove_count = 6
        if r > label_r:
            step = (r - label_r) / (groove_count + 1)
            for i in range(1, groove_count + 1):
                rr = r - step * i
                self.canvas.create_oval(
                    cx-rr, cy-rr, cx+rr, cy+rr,
                    outline="#252529",
                )

        self.canvas.create_oval(
            cx-label_r, cy-label_r, cx+label_r, cy+label_r,
            fill="#29292d",
            outline="",
        )

        self.canvas.create_text(
            cx, cy, text="♪",
            fill=ACCENT,
            font=(self.font_family, max(20, int(r * 0.6)), "bold"),
        )

    def start_login(self):
        self.login_button.set_enabled(False)
        self.login_button.set_text("Opening…")
        self.set_status("Waiting for Spotify authorization…", "info")

        def worker():
            try:
                self.spotify.login()
                self.root.after(0, self.login_success)
            except Exception as exc:
                self.root.after(0, lambda: self.login_failed(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def login_success(self):
        self.login_button.set_text("Connected")
        self.login_button.set_enabled(False)
        self.set_status("Connected. Looking for your current track…", "info")
        self.poll()

    def refresh_now(self):
        if not self.spotify.access_token:
            self.set_status("Connect Spotify before refreshing.", "info")
            return

        self.set_status("Refreshing playback…", "info")
        self.poll(immediate=True)

    def login_failed(self, message):
        self.login_button.set_enabled(True)
        self.login_button.set_text("Connect")
        self.set_status("Login error: " + message, "error")

    def poll(self, immediate=False):
        if not self.spotify.access_token:
            return

        def worker():
            try:
                data = self.spotify.currently_playing()
                queue_data = None
                queue_error = None
                try:
                    queue_data = self.spotify.queue()
                except Exception as exc:
                    queue_error = str(exc)
                self.root.after(
                    0,
                    lambda: self.update_playback(data, queue_data, queue_error),
                )
            except Exception as exc:
                self.root.after(0, lambda: self.set_status("API error: " + str(exc), "error"))

        threading.Thread(target=worker, daemon=True).start()

        if not immediate:
            self.root.after(POLL_MS, self.poll)

    def update_playback(self, data, queue_data, queue_error=None):
        self.update_track(data)
        self.update_queue(queue_data, queue_error)

    def update_queue(self, data, error=None):
        if error:
            self.next_var.set("Next up: queue unavailable")
            return

        queued_items = (data or {}).get("queue", [])
        if not queued_items:
            self.next_var.set("Next up: queue is empty")
            return

        next_item = queued_items[0]
        if next_item.get("type") == "track":
            artists = ", ".join(
                artist.get("name", "Unknown artist")
                for artist in next_item.get("artists", [])
            )
            self.next_var.set(
                f"Next up: {next_item.get('name', 'Unknown track')}"
                f" • {artists or 'Unknown artist'}"
            )
        else:
            self.next_var.set("Next up: non-music content")

    def update_track(self, data):
        if not data or not data.get("item"):
            self.set_status("Nothing is currently playing.", "idle")
            self.device_var.set("")
            return

        item = data["item"]

        if item.get("type") != "track":
            self.set_status("Currently playing content is not a music track.", "idle")
            return

        track_id = item.get("id")
        artists = ", ".join(a["name"] for a in item["artists"])
        album = item["album"]["name"]

        self.title_var.set(item["name"])
        self.artist_var.set(artists)
        self.album_var.set(album)

        progress = data.get("progress_ms") or 0
        duration = item.get("duration_ms") or 1
        self.track_progress_ms = progress
        self.track_duration_ms = duration
        self.track_is_playing = bool(data.get("is_playing"))
        self.track_started_at = time.perf_counter()

        self.progress_var.set(
            f"{self.ms_to_time(progress)} / {self.ms_to_time(duration)}"
        )

        device = data.get("device")
        if device:
            state = "Playing" if data.get("is_playing") else "Paused"
            self.device_var.set(f"{state} • {device.get('name', 'Spotify device')}")

        if track_id != self.last_track_id:
            self.last_track_id = track_id
            self.set_status("Loading album artwork…", "info")
            self.load_artwork(item)

        self.draw_progress(progress / duration)

    def load_artwork(self, item):
        images = item.get("album", {}).get("images", [])

        if not images:
            self.set_status("No album artwork available.", "idle")
            return

        image_url = images[0]["url"]

        if image_url in self.cover_cache:
            self.frames = self.cover_cache[image_url]
            self.ambient_colors = self.palette_cache.get(
                image_url, self.ambient_colors,
            )
            self.frame_index = 0
            self.set_status("Now playing", "success")
            self.start_animation()
            return

        def worker():
            try:
                response = requests.get(image_url, timeout=15)
                response.raise_for_status()

                from io import BytesIO
                image = Image.open(BytesIO(response.content)).convert("RGB")

                frames = make_vinyl_frames(image, size=200, frames=120)
                palette = extract_ambient_palette(image)

                self.cover_cache[image_url] = frames
                self.palette_cache[image_url] = palette

                self.root.after(
                    0,
                    lambda: self.artwork_ready(frames, palette)
                )
            except Exception as exc:
                self.root.after(
                    0,
                    lambda: self.set_status(
                        "Artwork error: " + str(exc), "error"
                    )
                )

        threading.Thread(target=worker, daemon=True).start()

    def artwork_ready(self, frames, palette=None):
        self.frames = frames
        if palette:
            self.ambient_colors = palette
        self.frame_index = 0
        self.set_status("Now playing", "success")
        self.start_animation()

    def start_animation(self):
        if self.animation_job:
            self.root.after_cancel(self.animation_job)
        self.animation_started_at = time.perf_counter()
        self.animation_job = None
        self.animate_vinyl()

    def toggle_animation(self):
        self.animation_running = not self.animation_running
        if self.animation_running:
            self.animation_button.set_text("Pause vinyl")
            self.start_animation()
        else:
            self.animation_button.set_text("Play vinyl")
            if self.animation_job:
                self.root.after_cancel(self.animation_job)
                self.animation_job = None

    def animate_vinyl(self):
        if not self.frames or not self.animation_running:
            return

        if self.animation_started_at is None:
            self.animation_started_at = time.perf_counter()

        elapsed = time.perf_counter() - self.animation_started_at
        self.frame_index = int(elapsed * VINYL_FPS) % len(self.frames)
        image = self.frames[self.frame_index]
        self.photo = ImageTk.PhotoImage(image)

        self.draw_ambient()
        self.draw_turntable()
        self.canvas.delete("vinyl")

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()

        if w <= 1:
            w = 220
        if h <= 1:
            h = 220

        self.canvas.create_image(
            w // 2,
            h // 2,
            image=self.photo,
            tags="vinyl",
        )

        self.animation_job = self.root.after(VINYL_FRAME_MS, self.animate_vinyl)

    def draw_turntable(self):
        """Draw the white platter and tonearm behind the rotating record."""
        width = max(self.canvas.winfo_width(), 220)
        height = max(self.canvas.winfo_height(), 220)
        center_x, center_y = width // 2, height // 2
        deck_left = center_x - 108
        deck_top = center_y - 108
        deck_right = center_x + 108
        deck_bottom = center_y + 108

        if not self.turntable_ids:
            self.turntable_ids.append(
                self.canvas.create_polygon(
                    rounded_rect_points(
                        deck_left, deck_top, deck_right, deck_bottom, 18,
                    ),
                    smooth=True, fill="#f4f4f1", outline="#ffffff",
                    width=2, tags="turntable",
                )
            )
            self.turntable_ids.append(
                self.canvas.create_oval(
                    center_x - 103, center_y - 103,
                    center_x + 103, center_y + 103,
                    outline="#d7d7d3", width=2, tags="turntable",
                )
            )
            self.turntable_ids.append(
                self.canvas.create_line(
                    center_x + 88, center_y - 82,
                    center_x + 88, center_y - 28,
                    fill="#ffffff", width=5, capstyle="round",
                    tags="turntable",
                )
            )
            self.turntable_ids.append(
                self.canvas.create_line(
                    center_x + 88, center_y - 28,
                    center_x + 58, center_y - 2,
                    fill="#ffffff", width=4, capstyle="round",
                    tags="turntable",
                )
            )
            self.turntable_ids.append(
                self.canvas.create_oval(
                    center_x + 81, center_y - 89,
                    center_x + 95, center_y - 75,
                    fill="#ffffff", outline="#cfcfcb", width=1,
                    tags="turntable",
                )
            )

        self.canvas.tag_lower("turntable")
        self.canvas.tag_raise("turntable", "ambient")

    def draw_ambient(self):
        width = max(self.canvas.winfo_width(), 220)
        height = max(self.canvas.winfo_height(), 220)
        center_x, center_y = width // 2, height // 2
        pulse = (time.perf_counter() * 0.9) % (len(self.ambient_colors))
        first_index = int(pulse) % len(self.ambient_colors)
        next_index = (first_index + 1) % len(self.ambient_colors)
        color_amount = pulse - int(pulse)
        color = self.ambient_colors[first_index]
        next_color = self.ambient_colors[next_index]
        current = tuple(
            int(color[index] + (next_color[index] - color[index]) * color_amount)
            for index in range(3)
        )
        breathe = (time.perf_counter() * 1.8) % (2 * math.pi)
        intensity = 0.78 + 0.12 * ((1 + math.sin(breathe)) / 2)

        colors = [
            blend_color(current, (11, 11, 13), 0.82),
            blend_color(current, (11, 11, 13), 0.68),
            blend_color(current, (11, 11, 13), 0.54),
        ]
        radii = [int(min(width, height) * factor * intensity)
                 for factor in (0.54, 0.47, 0.40)]

        if not self.ambient_ids:
            for radius, fill in zip(radii, colors):
                self.ambient_ids.append(
                    self.canvas.create_oval(
                        center_x - radius, center_y - radius,
                        center_x + radius, center_y + radius,
                        fill=fill, outline="", tags="ambient",
                    )
                )
        else:
            for item_id, radius, fill in zip(self.ambient_ids, radii, colors):
                self.canvas.coords(
                    item_id,
                    center_x - radius, center_y - radius,
                    center_x + radius, center_y + radius,
                )
                self.canvas.itemconfig(item_id, fill=fill)

    def tick_progress(self):
        if self.track_duration_ms:
            elapsed_ms = 0
            if self.track_is_playing and self.track_started_at is not None:
                elapsed_ms = (time.perf_counter() - self.track_started_at) * 1000

            progress = min(
                self.track_duration_ms,
                self.track_progress_ms + elapsed_ms,
            )
            self.progress_var.set(
                f"{self.ms_to_time(progress)} / "
                f"{self.ms_to_time(self.track_duration_ms)}"
            )
            self.draw_progress(progress / self.track_duration_ms)

        self.progress_job = self.root.after(PROGRESS_TICK_MS, self.tick_progress)

    def draw_progress(self, ratio):
        ratio = max(0, min(1, ratio))

        self.progress_canvas.delete("all")

        width = max(self.progress_canvas.winfo_width(), 10)
        height = 6

        draw_rounded_rect(
            self.progress_canvas, 0, 0, width, height, height / 2,
            fill="#29292e", outline="",
        )

        fill_width = max(height, width * ratio) if ratio > 0 else 0
        if fill_width > 0:
            draw_rounded_rect(
                self.progress_canvas, 0, 0, fill_width, height, height / 2,
                fill=ACCENT, outline="",
            )

    @staticmethod
    def ms_to_time(ms):
        total_seconds = max(0, int(ms / 1000))
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}:{seconds:02d}"


def main():
    if not CLIENT_ID:
        print(
            "\nSpotify Client ID is not configured.\n"
            "Create a .env file from .env.example and add your Client ID.\n"
        )

    root = tk.Tk()
    app = SpotifyVinylApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
