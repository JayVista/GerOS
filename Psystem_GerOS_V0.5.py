"""
GerOS V0.5.2 — Python Desktop System
架构：事件总线 + 独立模块，各组件零耦合，通过 EventBus 通信
"""
# ================================================================
# 0. 基础依赖
# ================================================================
import os, sys, time, json, threading, subprocess, ctypes, random, tempfile, webbrowser, copy, re, math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable, Any
from collections import deque, OrderedDict
import urllib.request
import urllib.parse
import urllib.error
import http.client
import http.cookiejar
import socket
import hashlib
import ssl
import gzip
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext, simpledialog
from PIL import Image, ImageTk, ImageDraw
import psutil

os_name = os.name


def app_dir() -> str:
    """获取应用程序根目录（可写），兼容 PyInstaller 打包和直接运行。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def resource_path(relative_path: str) -> str:
    """获取资源文件的绝对路径，兼容 PyInstaller 单文件打包。
    优先 sys._MEIPASS（只读临时目录，存放打包资源），回退到 app_dir()。"""
    try:
        base = sys._MEIPASS
    except AttributeError:
        base = app_dir()
    return os.path.join(base, relative_path)

# ================================================================
# 1. 事件总线 —— 所有模块通过它通信，彼此不感知对方
# ================================================================
class EventBus:
    """发布/订阅模式的事件总线。模块间解耦的核心。"""
    def __init__(self):
        self._handlers: dict[str, list[Callable]] = {}

    def on(self, event: str, handler: Callable):
        if event not in self._handlers:
            self._handlers[event] = []
        self._handlers[event].append(handler)

    def off(self, event: str, handler: Callable):
        if event in self._handlers:
            self._handlers[event] = [h for h in self._handlers[event] if h is not handler]

    def emit(self, event: str, *args, **kwargs):
        for handler in self._handlers.get(event, []):
            try:
                handler(*args, **kwargs)
            except Exception as e:
                print(f"[EventBus] 事件 '{event}' 处理异常: {e}")


# ================================================================
# 2. 配色/字体管理器
# ================================================================
class Palette:
    """统一配色，暗色/亮色两套。"""
    DARK = {
        "bg":           "#1a1a1a",
        "bg_panel":     "#252525",
        "bg_toolbar":   "#2d2d2d",
        "bg_hover":     "#3a3a3a",
        "bg_input":     "#333333",
        "fg":           "#e0e0e0",
        "fg_dim":       "#999999",
        "fg_muted":     "#666666",
        "accent":       "#0a84ff",
        "accent_hover": "#409cff",
        "danger":       "#ff453a",
        "warning":      "#ff9f0a",
        "success":      "#30d158",
        "border":       "#3a3a3a",
        "menu_bg":      "#1c1c1e",
        "menu_fg":      "#e0e0e0",
        "sidebar_bg":   "#202020",
        "titlebar_bg":  "#2a2a2a",
        "titlebar_fg":  "#999999",
        "dock_bg":      None,
        "overlay":      "black",
    }
    LIGHT = {
        "bg":           "#f2f2f7",
        "bg_panel":     "#ffffff",
        "bg_toolbar":   "#e8e8ed",
        "bg_hover":     "#d1d1d6",
        "bg_input":     "#e5e5ea",
        "fg":           "#1c1c1e",
        "fg_dim":       "#636366",
        "fg_muted":     "#aeaeb2",
        "accent":       "#007aff",
        "accent_hover": "#0051d4",
        "danger":       "#ff3b30",
        "warning":      "#ff9500",
        "success":      "#34c759",
        "border":       "#c6c6c8",
        "menu_bg":      "#e8e8ed",
        "menu_fg":      "#1c1c1e",
        "sidebar_bg":   "#e0e0e5",
        "titlebar_bg":  "#d1d1d6",
        "titlebar_fg":  "#636366",
        "dock_bg":      None,
        "overlay":      "white",
    }

    def __init__(self, mode="dark"):
        self.mode = mode

    def get(self, key: str) -> str:
        return (self.DARK if self.mode == "dark" else self.LIGHT).get(key, "#000")

    def toggle(self) -> str:
        self.mode = "light" if self.mode == "dark" else "dark"
        return self.mode


def font(name: str = "", size: int = 11, bold: bool = False) -> tuple:
    """跨平台字体选择。"""
    family = name or ("Microsoft YaHei UI" if os_name == "nt" else "Arial")
    weight = "bold" if bold else "normal"
    return (family, size, weight)


def icon_font(size: int = 11) -> tuple:
    return ("Segoe UI Emoji" if os_name == "nt" else "Arial", size)


# ================================================================
# 3. 声音管理器
# ================================================================
class Sound:
    """异步播放 WAV/音频，不阻塞主线程。"""
    @staticmethod
    def play(filepath: str, wait: bool = False):
        def _run():
            try:
                fpath = resource_path(filepath)
                if not os.path.exists(fpath):
                    return
                if os_name == "nt":
                    import winsound
                    flag = winsound.SND_FILENAME | (0 if wait else winsound.SND_ASYNC)
                    winsound.PlaySound(fpath, flag)
                else:
                    subprocess.Popen(
                        ["aplay", fpath],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
            except Exception:
                pass
        if wait:
            _run()
        else:
            threading.Thread(target=_run, daemon=True).start()


# ================================================================
# 4. 通知中心
# ================================================================
class Toast:
    """屏幕右上角的横幅通知队列。"""
    W = 320; H = 72; GAP = 8; MAX = 4

    def __init__(self, root: tk.Tk, theme: Palette):
        self.root = root
        self._palette = theme
        self._active: list[tk.Frame] = []

    def show(self, title: str, msg: str, dur: int = 3000):
        self.root.after(0, lambda: self._push(title, msg, dur))

    def _push(self, title: str, msg: str, dur: int):
        bg = self._palette.get("bg_panel")
        fg = self._palette.get("fg")
        frame = tk.Frame(self.root, bg=bg, highlightbackground=self._palette.get("border"),
                          highlightthickness=1)
        x = self.root.winfo_width() - self.W - 20
        y = 40 + len(self._active) * (self.H + self.GAP)
        frame.place(x=x, y=y, width=self.W, height=self.H)

        tk.Label(frame, text=title, bg=bg, fg=fg, font=font(bold=True, size=10),
                 anchor="w").pack(fill="x", padx=12, pady=(8, 1))
        tk.Label(frame, text=msg, bg=bg, fg=self._palette.get("fg_dim"), font=font(size=9),
                 anchor="w", wraplength=self.W - 24).pack(fill="x", padx=12)

        self._active.append(frame)
        self.root.after(dur, lambda: self._dismiss(frame))

    def _dismiss(self, frame):
        if frame in self._active:
            self._active.remove(frame)
        try: frame.destroy()
        except Exception: pass
        self._relayout()

    def _relayout(self):
        for i, f in enumerate(self._active):
            try:
                x = self.root.winfo_width() - self.W - 20
                y = 40 + i * (self.H + self.GAP)
                f.place(x=x, y=y)
            except Exception: pass


# ================================================================
# 5. 窗口组件 —— 独立的自绘窗口，只通过 EventBus 对外通信
# ================================================================
class Window:
    """独立窗口：拖拽、四边+四角缩放、最大化/最小化/关闭。状态变化通过 EventBus 事件发布。"""
    MIN_W = 300; MIN_H = 180

    def __init__(self, app_id: str, title: str, build_content: callable,
                 width: int, height: int, bus: EventBus, theme: Palette):
        self.app_id = app_id
        self.title = title
        self._bus = bus
        self._palette = theme
        self._w = width
        self._h = height
        self._maximized = False
        self._minimized = False
        self._closed = False
        self._geo = (100, 100, width, height)  # restore geometry
        self._active = False
        self._canvas: tk.Canvas | None = None
        self._cid: int | None = None

        # 拖拽/缩放状态
        self._drag = False
        self._resize = False
        self._rdir = ""
        self._dox = 0; self._doy = 0; self._sx = 0; self._sy = 0; self._sw = 0; self._sh = 0
        self._rs_fid1 = None; self._rs_fid2 = None  # 根窗口缩放绑定 ID

        self._frame = self._build(build_content)
        self._bind()
        self._apply_theme()
        self.center()

    # ----- 构建 -----
    def _build(self, build_content: callable) -> tk.Frame:
        bd = self._palette.get("border")
        bg = self._palette.get("bg")
        frame = tk.Frame(bg=bd, width=self._w, height=self._h, highlightthickness=0)
        frame.pack_propagate(False)
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        # 标题栏
        self._titlebar = tk.Frame(frame, bg=self._palette.get("titlebar_bg"), height=36)
        self._titlebar.grid(row=0, column=0, sticky="ew", pady=(0, 0))
        self._titlebar.pack_propagate(False)
        self._titlebar.bind("<Double-Button-1>", lambda e: self.toggle_maximize())

        # 红绿灯按钮
        btns = tk.Frame(self._titlebar, bg=self._palette.get("titlebar_bg"))
        btns.pack(side="left", padx=10, pady=8)

        self._btn_close = self._make_btn(btns, "\u2715", self._palette.get("danger"), "#cc372e", self.close)
        self._btn_close.pack(side="left", padx=3)
        self._btn_min = self._make_btn(btns, "\u2212", self._palette.get("warning"), "#cc8008", self.minimize)
        self._btn_min.pack(side="left", padx=3)
        self._btn_max = self._make_btn(btns, "\u25A2", self._palette.get("success"), "#26a647", self.toggle_maximize)
        self._btn_max.pack(side="left", padx=3)

        self._title_label = tk.Label(self._titlebar, text=self.title, bg=self._palette.get("titlebar_bg"),
                                      fg=self._palette.get("titlebar_fg"), font=font(bold=True, size=12),
                                      cursor="fleur")
        self._title_label.place(relx=0.5, rely=0.5, anchor="center")

        # 内容容器
        container = tk.Frame(frame, bg=bg)
        container.grid(row=1, column=0, sticky="nsew", padx=1, pady=(0, 1))
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        build_content(container)

        # 缩放控件
        self._grips = {}
        GS = 12  # 把手统一尺寸
        for tag, cur, rx, ry, an in [
            ("se", "size_nw_se",  1.0, 1.0, "se"),
            ("ne", "size_ne_sw",  1.0, 0.0, "ne"),
            ("nw", "size_nw_se",  0.0, 0.0, "nw"),
            ("sw", "size_ne_sw",  0.0, 1.0, "sw"),
            ("n",  "size_ns",     0.5, 0.0, "n"),
            ("s",  "size_ns",     0.5, 1.0, "s"),
            ("w",  "size_we",     0.0, 0.5, "w"),
            ("e",  "size_we",     1.0, 0.5, "e"),
        ]:
            f = tk.Frame(frame, bg=bd, cursor=cur)
            if tag in ("n", "s"):
                f.place(relx=rx, rely=ry, anchor=an, relwidth=1.0, height=GS)
            elif tag in ("w", "e"):
                f.place(relx=rx, rely=ry, anchor=an, relheight=1.0, width=GS)
            else:
                # 角把手
                f.place(relx=rx, rely=ry, anchor=an, width=GS, height=GS)
            self._grips[tag] = f

        return frame

    def _make_btn(self, parent, text, bg, hover_bg, cmd):
        btn = tk.Label(parent, text=text, bg=bg, fg="white", font=("Arial", 10, "bold"),
                        width=2, height=1, cursor="hand2", relief="flat")
        btn.bind("<Button-1>", lambda e: (cmd(), "break")[1])
        btn.bind("<Enter>", lambda e: btn.config(bg=hover_bg))
        btn.bind("<Leave>", lambda e: btn.config(bg=bg))
        return btn

    def _apply_theme(self):
        t = self._palette
        self._frame.config(bg=t.get("border"))
        self._titlebar.config(bg=t.get("titlebar_bg"))
        self._title_label.config(bg=t.get("titlebar_bg"), fg=t.get("titlebar_fg"))
        for g in self._grips.values():
            try: g.config(bg=t.get("border"))
            except Exception: pass

    # ----- 事件绑定 -----
    def _bind(self):
        f = self._frame
        f.bind("<Button-1>", lambda e: self._bus.emit("window:focus", self.app_id))

        self._titlebar.bind("<Button-1>", self._on_drag_start)
        self._titlebar.bind("<B1-Motion>", self._on_drag)
        self._title_label.bind("<Button-1>", self._on_drag_start)
        self._title_label.bind("<B1-Motion>", self._on_drag)

        for tag, grip in self._grips.items():
            grip.bind("<Button-1>", lambda e, d=tag: self._on_resize_start(e, d))
            # B1-Motion / ButtonRelease-1 由 _on_resize_start 通过 bind_all 全局绑定

    # ----- 放置在 Canvas -----
    def place_on(self, canvas: tk.Canvas):
        self._canvas = canvas
        canvas.update_idletasks()
        self._cid = canvas.create_window(100, 100, window=self._frame, anchor="nw", tags=f"win")
        canvas.tag_bind(self._cid, "<Button-1>", lambda e: self._bus.emit("window:focus", self.app_id))

    def destroy(self):
        self._closed = True
        if self._canvas and self._cid is not None:
            try: self._canvas.delete(self._cid)
            except Exception: pass
        try: self._frame.destroy()
        except Exception: pass
        self._canvas = None; self._cid = None

    # ----- 拖拽 -----
    def _on_drag_start(self, ev):
        if self._maximized or self._closed or not self._canvas: return
        self._drag = True
        self._dox, self._doy = ev.x_root, ev.y_root
        c = self._canvas.coords(self._cid)
        self._sx, self._sy = c[0], c[1]
        self._bus.emit("window:focus", self.app_id)

    def _on_drag(self, ev):
        if not self._drag or self._maximized or self._closed or not self._canvas: return
        nx = self._sx + (ev.x_root - self._dox)
        ny = max(23, self._sy + (ev.y_root - self._doy))
        self._canvas.coords(self._cid, nx, ny)
        self._geo = (nx, ny, self._w, self._h)

    # ----- 缩放 -----
    def _on_resize_start(self, ev, direction):
        if self._maximized or self._closed or not self._canvas: return
        self._resize = True; self._rdir = direction
        c = self._canvas.coords(self._cid)
        self._sx, self._sy = c[0], c[1]
        self._sw, self._sh = self._frame.winfo_width(), self._frame.winfo_height()
        self._dox, self._doy = ev.x_root, ev.y_root
        self._bus.emit("window:focus", self.app_id)
        # bind_all 是应用级全局绑定，无论鼠标在哪个控件上都能捕获
        self._rs_fid1 = self._frame.bind_all("<B1-Motion>", self._on_resize, add="+")
        self._rs_fid2 = self._frame.bind_all("<ButtonRelease-1>", self._on_resize_end, add="+")

    def _on_resize(self, ev):
        if not self._resize or self._maximized or self._closed or not self._canvas: return
        dx = ev.x_root - self._dox
        dy = ev.y_root - self._doy
        nx, ny = self._sx, self._sy
        nw, nh = self._sw, self._sh
        d = self._rdir

        if "e" in d: nw = max(self.MIN_W, self._sw + dx)
        if "s" in d: nh = max(self.MIN_H, self._sh + dy)
        if "w" in d:
            nw = max(self.MIN_W, self._sw - dx)
            nx = self._sx + (self._sw - nw)
        if "n" in d:
            nh = max(self.MIN_H, self._sh - dy)
            ny = max(23, self._sy + (self._sh - nh))

        self._canvas.coords(self._cid, nx, ny)
        self._frame.config(width=nw, height=nh)
        self._w, self._h = nw, nh
        self._geo = (nx, ny, nw, nh)

    def _on_resize_end(self, ev):
        self._resize = False; self._rdir = ""
        try:
            self._frame.unbind_all(self._rs_fid1)
            self._frame.unbind_all(self._rs_fid2)
        except Exception:
            pass

    # ----- 状态操作 -----
    @property
    def active(self) -> bool: return self._active
    @property
    def minimized(self) -> bool: return self._minimized
    @property
    def maximized(self) -> bool: return self._maximized
    @property
    def closed(self) -> bool: return self._closed
    @property
    def width(self) -> int: return self._w
    @property
    def height(self) -> int: return self._h

    def activate(self):
        self._active = True
        self._frame.config(bg=self._palette.get("accent"))
        for g in self._grips.values():
            try: g.config(bg=self._palette.get("accent"))
            except Exception: pass
        if self._canvas and self._cid is not None:
            self._canvas.tag_raise(self._cid)

    def deactivate(self):
        self._active = False
        bd = self._palette.get("border")
        self._frame.config(bg=bd)
        for g in self._grips.values():
            try: g.config(bg=bd)
            except Exception: pass

    def close(self):
        if self._closed: return
        self._closed = True
        self._resize = False
        try:
            self._frame.unbind_all(self._rs_fid1)
            self._frame.unbind_all(self._rs_fid2)
        except Exception:
            pass
        self._bus.emit("window:close", self.app_id)

    def minimize(self):
        if self._canvas and self._cid is not None:
            self._canvas.itemconfigure(self._cid, state="hidden")
        self._minimized = True
        self._bus.emit("window:minimize", self.app_id)

    def restore(self):
        if self._canvas and self._cid is not None:
            self._canvas.itemconfigure(self._cid, state="normal")
        self._minimized = False
        self._bus.emit("window:focus", self.app_id)

    def toggle_maximize(self):
        if self._maximized: self._unmaximize()
        else: self._do_maximize()

    def _do_maximize(self):
        c = self._canvas.coords(self._cid)
        self._geo = (c[0], c[1], self._w, self._h)
        pw = self._canvas.winfo_width()
        ph = self._canvas.winfo_height()
        self._canvas.coords(self._cid, 0, 23)
        self._frame.config(width=pw, height=ph - 23 - 65)
        self._w, self._h = pw, ph - 23 - 65
        self._maximized = True
        self._btn_max.config(text="\u29C9")
        self._title_label.config(cursor="")

    def _unmaximize(self):
        x, y, w, h = self._geo
        self._canvas.coords(self._cid, x, y)
        self._frame.config(width=w, height=h)
        self._w, self._h = w, h
        self._maximized = False
        self._btn_max.config(text="\u25A2")
        self._title_label.config(cursor="fleur")

    def set_title(self, title: str):
        self.title = title
        self._title_label.config(text=title)

    def center(self):
        if not self._canvas: return
        self._canvas.update_idletasks()
        pw = self._canvas.winfo_width() or 1280
        ph = self._canvas.winfo_height() or 720
        x = max(50, (pw - self._w) // 2)
        y = max(30, (ph - self._h - 80) // 2)
        self._canvas.coords(self._cid, x, y)
        self._geo = (x, y, self._w, self._h)

    def center_offset(self, offset: int):
        """居中后附加级联偏移。"""
        if not self._canvas: return
        self._canvas.update_idletasks()
        pw = self._canvas.winfo_width() or 1280
        ph = self._canvas.winfo_height() or 720
        x = max(50, (pw - self._w) // 2) + offset
        y = max(30, (ph - self._h - 80) // 2) + offset
        # 防止偏移导致超出边界
        x = min(x, pw - self._w - 50)
        y = min(y, ph - self._h - 100)
        self._canvas.coords(self._cid, x, y)
        self._geo = (x, y, self._w, self._h)

    def refresh_palette(self):
        self._apply_theme()
        if self._active:
            self._frame.config(bg=self._palette.get("accent"))
            for g in self._grips.values():
                try: g.config(bg=self._palette.get("accent"))
                except Exception: pass


# ================================================================
# 6. 窗口管理器 —— 通过 EventBus 管理窗口生命周期
# ================================================================
class WindowManager:
    """管理所有窗口的创建、激活、关闭、最小化。不持有任何应用逻辑。"""
    def __init__(self, canvas: tk.Canvas, bus: EventBus, theme: Palette):
        self._canvas = canvas
        self._bus = bus
        self._palette = theme
        self._windows: dict[str, Window] = {}
        self._active_id: str | None = None
        self._app_counts: dict[str, int] = {}
        self._open_count = 0

        self._bus.on("window:focus", self._on_focus)
        self._bus.on("window:close", self._on_close)
        self._bus.on("window:minimize", lambda app_id: self._bus.emit("dock:refresh"))

    def open(self, app_id: str, title: str, build_content: callable,
             width: int = 800, height: int = 500) -> Window:
        """创建并展示一个窗口。"""
        if app_id in self._windows and not self._windows[app_id].closed:
            self.focus(app_id)
            return self._windows[app_id]

        win = Window(app_id, title, build_content, width, height, self._bus, self._palette)
        win.place_on(self._canvas)
        self._windows[app_id] = win
        self._app_counts[app_id] = self._app_counts.get(app_id, 0) + 1
        self._focus_win(win)
        # 级联偏移，避免窗口完全重叠
        self._open_count += 1
        offset = (self._open_count * 28) % 280
        win.center_offset(offset)
        self._bus.emit("dock:refresh")
        return win

    def focus(self, app_id: str):
        if app_id in self._windows and not self._windows[app_id].closed:
            self._focus_win(self._windows[app_id])

    def _focus_win(self, win: Window):
        if self._active_id and self._active_id in self._windows:
            old = self._windows[self._active_id]
            if old is not win:
                old.deactivate()
        self._active_id = win.app_id
        if not win.minimized:
            win.activate()

    def _on_focus(self, app_id: str):
        self.focus(app_id)

    def _on_close(self, app_id: str):
        if app_id in self._windows:
            self._windows[app_id].destroy()
            del self._windows[app_id]
        if app_id in self._app_counts:
            self._app_counts[app_id] = max(0, self._app_counts[app_id] - 1)
        if self._active_id == app_id:
            self._active_id = None
            # 激活最后一个窗口
            if self._windows:
                last = list(self._windows.values())[-1]
                self._focus_win(last)
        self._bus.emit("dock:refresh")

    def close_active(self):
        if self._active_id:
            self._bus.emit("window:close", self._active_id)

    def close_all(self):
        for win in list(self._windows.values()):
            win.destroy()
        self._windows.clear()
        self._app_counts.clear()
        self._active_id = None
        self._bus.emit("dock:refresh")

    def minimize_active(self):
        if self._active_id and self._active_id in self._windows:
            self._windows[self._active_id].minimize()

    def toggle_maximize_active(self):
        if self._active_id and self._active_id in self._windows:
            self._windows[self._active_id].toggle_maximize()

    def has_app(self, app_id: str) -> bool:
        return app_id in self._windows and not self._windows[app_id].closed

    def is_app_running(self, app_id: str) -> bool:
        return self.has_app(app_id)

    def get_minimized(self, app_id: str) -> bool:
        if app_id in self._windows:
            return self._windows[app_id].minimized
        return False

    def restore_app(self, app_id: str):
        if app_id in self._windows and self._windows[app_id].minimized:
            self._windows[app_id].restore()

    def show_all_minimized(self):
        for win in self._windows.values():
            if win.minimized:
                win.restore()

    def refresh_all_palettes(self):
        for win in self._windows.values():
            win.refresh_palette()


# ================================================================
# 7. Dock —— 底部栏，通过 EventBus 刷新
# ================================================================
class Dock:
    """iPadOS 风格底部悬浮 Dock —— PIL 绘制真正圆角胶囊，抗锯齿平滑"""
    def __init__(self, root: tk.Tk, bus: EventBus, theme: Palette):
        self._root = root
        self._bus = bus
        self._palette = theme
        self._icons: list = []
        self._pill_photo = None  # 缓存生成的胶囊图片

        # 精确尺寸 Canvas — 用 place 定位，无全宽容器
        self._canvas = tk.Canvas(root, highlightthickness=0, bd=0,
                                  takefocus=0, highlightbackground="#1a1a1a",
                                  highlightcolor="#1a1a1a")
        self._inner = tk.Frame(self._canvas, bg=None)

        self._bus.on("dock:refresh", lambda: self.refresh())

    def _draw_pill(self):
        """PIL 绘制真正圆角胶囊（抗锯齿）并放置 Canvas"""
        self._root.update_idletasks()

        inner_w = self._inner.winfo_reqwidth()
        if inner_w < 10:
            self._root.after(100, self._draw_pill)
            return

        PAD_X, PILL_H = 18, 56
        pw = inner_w + PAD_X * 2
        RADIUS = PILL_H // 2  # 正半圆两端

        canvas_w = pw + 4
        canvas_h = PILL_H + 12

        cw = self._root.winfo_width()
        ch = self._root.winfo_height()
        cx = (cw - canvas_w) // 2
        cy = ch - canvas_h - 10

        self._canvas.place(x=cx, y=cy, width=canvas_w, height=canvas_h)

        t = self._palette
        is_dark = t.mode == "dark"
        fill_color = (44, 44, 46) if is_dark else (246, 246, 246)
        border_color = (74, 74, 78) if is_dark else (205, 205, 208)
        shadow_color = (0, 0, 0, 40) if is_dark else (0, 0, 0, 18)

        # 2x 超采样抗锯齿
        scale = 2
        iw, ih = canvas_w * scale, canvas_h * scale
        img = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        bd = 2 * scale       # 边框宽度
        margin = 2 * scale   # 上下边距
        pad = 1 * scale      # 左侧额外留白（修正文字偏移）

        # 阴影层
        draw.rounded_rectangle(
            (pad + 2 * scale, margin + 2 * scale,
             iw - pad - 2 * scale, ih - margin - 2 * scale),
            radius=RADIUS * scale, fill=shadow_color
        )
        # 边框层
        draw.rounded_rectangle(
            (pad, margin, iw - pad, ih - margin),
            radius=RADIUS * scale, fill=border_color
        )
        # 填充层
        draw.rounded_rectangle(
            (pad + bd, margin + bd, iw - pad - bd, ih - margin - bd),
            radius=(RADIUS - 1) * scale, fill=fill_color
        )

        # 缩回 1x 并显示
        img = img.resize((canvas_w, canvas_h), Image.LANCZOS)
        self._pill_photo = ImageTk.PhotoImage(img)

        self._canvas.delete("all")
        self._canvas.create_image(0, 0, image=self._pill_photo, anchor="nw", tags="pill_bg")
        self._canvas.create_window(canvas_w // 2, canvas_h // 2, window=self._inner,
                                    anchor="c", tags="dock_center")

    def add_icon(self, icon: str, name: str, app_id: str, on_click: Callable):
        cont = tk.Frame(self._inner, bg=None)
        cont.pack(side="left", padx=6, pady=6)

        lbl = tk.Label(cont, text=icon, bg=None, font=icon_font(28), cursor="hand2")
        lbl.pack()

        dot = tk.Frame(cont, width=4, height=4, bg=self._palette.get("success"))
        dot.place(relx=0.5, rely=1.0, y=3, anchor="s")
        dot.place_forget()

        tt = None

        def enter(e):
            lbl.config(font=icon_font(34))
            nonlocal tt
            x = lbl.winfo_rootx() + lbl.winfo_width() // 2
            y = lbl.winfo_rooty() - 20
            tt = tk.Toplevel(lbl)
            tt.overrideredirect(True)
            tk.Label(tt, text=name, bg=self._palette.get("bg_panel"), fg=self._palette.get("fg"),
                     font=font(size=9), padx=8, pady=2).pack()
            tt.update_idletasks()
            tt.geometry(f"+{x - tt.winfo_width() // 2}+{y}")

        def leave(e):
            lbl.config(font=icon_font(28))
            if tt:
                try: tt.destroy()
                except Exception: pass

        def click(e):
            on_click()

        for w in (lbl,):
            w.bind("<Enter>", enter)
            w.bind("<Leave>", leave)
            w.bind("<Button-1>", click)

        self._icons.append((cont, dot, app_id))
        self._draw_pill()
        self._root.after_idle(self._draw_pill)

    def _adjust(self):
        self._draw_pill()

    def adjust(self):
        self._root.after(50, self._draw_pill)

    def refresh(self, bus=None):
        pass

    def set_running(self, app_id: str, running: bool):
        for cont, dot, aid in self._icons:
            if aid == app_id:
                if running:
                    dot.place(relx=0.5, rely=1.0, y=3, anchor="s")
                else:
                    dot.place_forget()

    def refresh_palette(self):
        self._draw_pill()


# ================================================================
# 8. 菜单栏
# ================================================================
class MenuBar:
    """顶部菜单栏 + 窗口控制 + 实时时钟 + 音量控制。"""
    def __init__(self, root: tk.Tk, bus: EventBus, theme: Palette, system):
        self._root = root
        self._bus = bus
        self._palette = theme
        self._system = system
        self._vol_open = False
        self._cal_open = False
        self._cal_display_year = None   # 日历导航用
        self._cal_display_month = None

        self._frame = tk.Frame(root, bg=theme.get("menu_bg"), height=23)
        self._frame.pack(side="top", fill="x")
        self._frame.pack_propagate(False)

        self._menus = []
        self._btns = []
        self._build_menus()

        # 时钟（最右侧，可点击）
        self._clock = tk.Label(self._frame, text="", bg=theme.get("menu_bg"),
                                fg=theme.get("menu_fg"), font=font(size=9),
                                cursor="hand2")
        self._clock.pack(side="right", padx=(0, 10))
        self._clock.bind("<Button-1>", self._cal_click)

        # 音量按钮（时钟左侧）
        self._vol_label = tk.Label(self._frame, text="", bg=theme.get("menu_bg"),
                                    fg=theme.get("menu_fg"), font=font(size=9),
                                    cursor="hand2")
        self._vol_label.pack(side="right", padx=2)
        self._vol_label.bind("<Button-1>", self._vol_click)
        self._vol_label.bind("<MouseWheel>", self._vol_wheel)
        self._update_vol_label()

        # 音量下拉面板（预创建，默认隐藏）
        t = theme
        self._vol_panel = tk.Frame(self._root, bg=t.get("bg_panel"), bd=2,
                                    relief="ridge",
                                    highlightbackground=t.get("border"),
                                    highlightthickness=1)
        self._vol_slider = tk.Scale(self._vol_panel, from_=100, to=0,
                                     orient="vertical", length=120, showvalue=True,
                                     command=self._on_slider,
                                     bg=t.get("bg_panel"), fg=t.get("fg"),
                                     troughcolor=t.get("bg"),
                                     activebackground=t.get("accent"),
                                     highlightthickness=0, bd=0,
                                     font=font(size=9))
        self._vol_slider.pack(padx=8, pady=(8, 6))
        # 全局点击关闭不再初始化时绑定，改为打开面板时动态绑定

        # 日历/时间下拉面板（预创建，默认隐藏）
        self._build_cal_panel()

        self._tick()

    def _build_menus(self):
        def m(label, items):
            mb = tk.Menubutton(self._frame, text=label, bg=self._palette.get("menu_bg"),
                                fg=self._palette.get("menu_fg"), font=font(size=10),
                                relief="flat", padx=6)
            mb.pack(side="left")
            menu = tk.Menu(mb, tearoff=0, bg=self._palette.get("bg_panel"),
                           fg=self._palette.get("fg"), activebackground=self._palette.get("accent"),
                           activeforeground="white", font=font(size=10))
            for txt, cmd in items:
                if txt == "-": menu.add_separator()
                else: menu.add_command(label=txt, command=cmd)
            mb.config(menu=menu)
            self._menus.append(menu)
            self._btns.append(mb)

        S = self._system
        m("Ger系统", [
            ("关于本机",   S.show_about),
            ("系统信息",   S.show_sysinfo),
            ("-", None),
            ("显示所有窗口", S.show_all_minimized),
            ("锁定屏幕",   S.lock),
            ("-", None),
            ("切换色调",   S.toggle_palette),
            ("下一张壁纸  →", S.next_wallpaper),
            ("-", None),
            ("更换壁纸...", S.change_wallpaper),
            ("保存为主题 (.ite)", S.save_theme_dialog),
            ("加载主题 (.ite)", S.load_theme_dialog),
            ("主题管理...", S.manage_themes),
            ("-", None),
            ("关闭系统",   S.shutdown),
        ])
        m("文件", [
            ("新建文件夹", S.new_folder),
            ("新建文本文档", S.new_txt),
            ("-", None),
            ("打开文件",   S.open_file_dialog),
            ("关闭窗口",   S.close_active),
        ])
        m("编辑", [
            ("撤销", lambda: None),
            ("-", None),
            ("剪切", lambda: None),
            ("复制", lambda: None),
            ("粘贴", lambda: None),
        ])
        # 查看菜单 — 动态显示 进入/退出 全屏
        self._view_mb = tk.Menubutton(self._frame, text="查看",
                                       bg=self._palette.get("menu_bg"),
                                       fg=self._palette.get("menu_fg"),
                                       font=font(size=10), relief="flat", padx=6)
        self._view_mb.pack(side="left")
        self._view_menu = tk.Menu(self._view_mb, tearoff=0,
                                   bg=self._palette.get("bg_panel"),
                                   fg=self._palette.get("fg"),
                                   activebackground=self._palette.get("accent"),
                                   activeforeground="white", font=font(size=10))
        self._view_mb.config(menu=self._view_menu)
        self._menus.append(self._view_menu)
        self._btns.append(self._view_mb)
        self._build_view_menu()
        m("前往", [
            ("文件管理",   S.open_app("finder")),
            ("应用程序",   S.open_app("apps")),
            ("下载",       S.open_app("downloads")),
            ("-", None),
            ("系统设置",   S.open_app("settings")),
        ])
        m("帮助", [
            ("快捷键",     S.show_shortcuts),
            ("使用帮助",   lambda: messagebox.showinfo("帮助", "GerOS V0.5.2 模拟桌面系统\nEsc 锁定屏幕")),
        ])

    def _build_view_menu(self):
        """根据当前全屏状态重建查看菜单。"""
        self._view_menu.delete(0, "end")
        if self._system._root.attributes("-fullscreen"):
            self._view_menu.add_command(label="退出全屏",
                                         command=self._system.toggle_fullscreen)
        else:
            self._view_menu.add_command(label="进入全屏",
                                         command=self._system.toggle_fullscreen)

    # ---------- 音量控制 ----------
    def _get_vol(self):
        """读取系统主音量 0–100"""
        if os_name != "nt":
            return 50
        try:
            vol = ctypes.c_uint32()
            ctypes.windll.winmm.waveOutGetVolume(0, ctypes.byref(vol), ctypes.sizeof(vol))
            return int((vol.value & 0xFFFF) / 65535 * 100)
        except Exception as e:
            print(f"[Volume] 读取音量失败: {e}")
            return 50

    def _set_vol(self, pct):
        """设置系统主音量 0–100"""
        if os_name != "nt":
            return
        try:
            v = max(0, min(100, int(pct)))
            raw = int(v / 100 * 65535)
            val = (raw & 0xFFFF) | ((raw & 0xFFFF) << 16)
            ctypes.windll.winmm.waveOutSetVolume(0, ctypes.c_uint32(val))
        except Exception as e:
            print(f"[Volume] 设置音量失败: {e}")

    def _update_vol_label(self, *_):
        """刷新音量按钮文字"""
        try:
            v = self._get_vol()
            if v == 0:
                icon = "\U0001f507"  # 🔇
            elif v < 33:
                icon = "\U0001f508"  # 🔈
            elif v < 66:
                icon = "\U0001f509"  # 🔉
            else:
                icon = "\U0001f50a"  # 🔊
            self._vol_label.config(text=f" {icon} {v}% ")
        except Exception as e:
            print(f"[Volume] 更新标签失败: {e}")

    def _vol_click(self, event):
        """单击图标：在下方显示/隐藏音量面板"""
        if self._vol_open:
            self._vol_hide()
            return "break"
        # 关闭日历面板（如果打开）
        if self._cal_open:
            self._cal_hide()
        # 定位到音量图标正下方
        x = self._vol_label.winfo_rootx() - self._root.winfo_rootx() - 10
        y = (self._frame.winfo_rooty() + self._frame.winfo_height()
             - self._root.winfo_rooty())
        self._vol_panel.place(x=x, y=y)
        self._vol_panel.lift()  # 提升到最顶层，避免被桌面 Canvas 遮挡
        self._vol_slider.set(self._get_vol())
        self._vol_open = True
        # 延迟绑定全局点击，避免当前 click 触发了 _on_root_click
        self._root.after(80, self._bind_outside_click)
        return "break"  # 阻止事件冒泡到 root

    def _bind_outside_click(self):
        """仅在面板打开时绑定全局点击关闭"""
        if self._vol_open:
            self._root.bind("<Button-1>", self._on_root_click, add="+")

    def _vol_hide(self):
        self._vol_panel.place_forget()
        self._vol_open = False
        # 解绑全局点击，避免残留
        try:
            self._root.unbind("<Button-1>")
        except Exception:
            pass

    def _on_root_click(self, event):
        """点击面板外时关闭面板"""
        if not self._vol_open:
            return
        w = event.widget
        # 判断点击是否在面板区域或音量标签上
        if w is self._vol_panel:
            return
        if w is self._vol_label:
            return
        try:
            panel_widgets = {self._vol_panel} | set(self._vol_panel.winfo_children())
            if w in panel_widgets:
                return
        except Exception:
            pass
        self._vol_hide()

    def _on_slider(self, val):
        self._set_vol(float(val))
        self._update_vol_label()

    def _vol_wheel(self, event):
        """滚轮调节音量"""
        delta = 2 if event.delta > 0 else -2
        v = self._get_vol() + delta
        self._set_vol(max(0, min(100, v)))
        self._update_vol_label()

    # ---------- 时钟 & 日历 ----------
    def _build_cal_panel(self):
        """预创建日历下拉面板"""
        t = self._palette
        self._cal_panel = tk.Frame(self._root, bg=t.get("bg_panel"), bd=2,
                                    relief="ridge",
                                    highlightbackground=t.get("border"),
                                    highlightthickness=1)
        # ---- 导航栏：◀ 年/月 ▶ ----
        nav = tk.Frame(self._cal_panel, bg=t.get("bg_panel"))
        nav.pack(fill="x", padx=6, pady=(6, 0))
        self._cal_prev = tk.Label(nav, text="◀", bg=t.get("bg_panel"),
                                   fg=t.get("accent"), font=font(size=9), cursor="hand2")
        self._cal_prev.pack(side="left", padx=2)
        self._cal_prev.bind("<Button-1>", self._cal_prev_month)
        self._cal_month_label = tk.Label(nav, text="", bg=t.get("bg_panel"),
                                          fg=t.get("fg"), font=font(bold=True, size=10))
        self._cal_month_label.pack(side="left", expand=True)
        self._cal_next = tk.Label(nav, text="▶", bg=t.get("bg_panel"),
                                   fg=t.get("accent"), font=font(size=9), cursor="hand2")
        self._cal_next.pack(side="right", padx=2)
        self._cal_next.bind("<Button-1>", self._cal_next_month)

        # ---- 星期标题 ----
        header = tk.Frame(self._cal_panel, bg=t.get("bg_panel"))
        header.pack(fill="x", padx=6, pady=(4, 0))
        days = ["一", "二", "三", "四", "五", "六", "日"]
        for d in days:
            lbl = tk.Label(header, text=d, bg=t.get("bg_panel"),
                           fg=t.get("fg_dim"), font=font(size=8), width=3)
            lbl.pack(side="left")

        # ---- 日期网格（6行×7列）----
        self._cal_grid = tk.Frame(self._cal_panel, bg=t.get("bg_panel"))
        self._cal_grid.pack(fill="both", padx=6, pady=(2, 0))
        self._cal_cells = []  # 42个 Label（6行×7列）
        for r in range(6):
            row_frame = tk.Frame(self._cal_grid, bg=t.get("bg_panel"))
            row_frame.pack(fill="x")
            for c in range(7):
                lbl = tk.Label(row_frame, text="", bg=t.get("bg_panel"),
                               fg=t.get("fg"), font=font(size=9), width=3)
                lbl.pack(side="left")
                self._cal_cells.append(lbl)

        # ---- 底部：大时间 + 完整日期 ----
        footer = tk.Frame(self._cal_panel, bg=t.get("bg_panel"))
        footer.pack(fill="x", padx=10, pady=(6, 8))
        self._cal_big_time = tk.Label(footer, text="", bg=t.get("bg_panel"),
                                       fg=t.get("fg"), font=font(bold=True, size=20))
        self._cal_big_time.pack(anchor="w")
        self._cal_full_date = tk.Label(footer, text="", bg=t.get("bg_panel"),
                                        fg=t.get("fg_dim"), font=font(size=10))
        self._cal_full_date.pack(anchor="w")

    def _refresh_cal(self):
        """刷新日历面板：月份、日期格、大时间、完整日期"""
        now = datetime.now()
        y = self._cal_display_year if self._cal_display_year else now.year
        m = self._cal_display_month if self._cal_display_month else now.month

        # 导航标题
        self._cal_month_label.config(text=f"{y}年{m}月")

        # 计算当月日期
        import calendar as cal_mod
        first_weekday = datetime(y, m, 1).weekday()  # 0=星期一
        days_in_month = cal_mod.monthrange(y, m)[1]

        # 填充日期格
        day = 1
        for i in range(42):
            lbl = self._cal_cells[i]
            if i < first_weekday or day > days_in_month:
                lbl.config(text="", bg=self._palette.get("bg_panel"), fg=self._palette.get("fg"))
            else:
                is_today = (day == now.day and m == now.month and y == now.year)
                if is_today:
                    lbl.config(text=str(day), bg=self._palette.get("accent"),
                               fg="white", font=font(bold=True, size=9))
                else:
                    lbl.config(text=str(day), bg=self._palette.get("bg_panel"),
                               fg=self._palette.get("fg"), font=font(size=9))
                day += 1

        # 底部大时间 + 完整日期
        self._cal_big_time.config(text=now.strftime("%H:%M:%S"))
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        self._cal_full_date.config(text=f"{now.year}年{now.month}月{now.day}日 {weekdays[now.weekday()]}")

    def _cal_prev_month(self, event=None):
        if self._cal_display_month == 1:
            self._cal_display_month = 12
            self._cal_display_year -= 1
        else:
            self._cal_display_month -= 1
        self._refresh_cal()
        return "break"

    def _cal_next_month(self, event=None):
        if self._cal_display_month == 12:
            self._cal_display_month = 1
            self._cal_display_year += 1
        else:
            self._cal_display_month += 1
        self._refresh_cal()
        return "break"

    def _cal_click(self, event):
        """单击时钟：显示/隐藏日历面板"""
        if self._cal_open:
            self._cal_hide()
            # 同时关闭音量面板
            if self._vol_open:
                self._vol_hide()
            return "break"
        # 先关闭音量面板
        if self._vol_open:
            self._vol_hide()
        # 定位到时钟正下方（右对齐）
        x = (self._clock.winfo_rootx() + self._clock.winfo_width()
             - self._root.winfo_rootx() - 220)
        y = (self._frame.winfo_rooty() + self._frame.winfo_height()
             - self._root.winfo_rooty())
        # 重置导航到当前月
        now = datetime.now()
        self._cal_display_year = now.year
        self._cal_display_month = now.month
        self._refresh_cal()
        self._cal_panel.place(x=x, y=y)
        self._cal_panel.lift()
        self._cal_open = True
        self._root.after(80, self._bind_cal_outside)
        return "break"

    def _bind_cal_outside(self):
        if self._cal_open:
            self._root.bind("<Button-1>", self._on_cal_outside, add="+")

    def _cal_hide(self):
        self._cal_panel.place_forget()
        self._cal_open = False
        try:
            self._root.unbind("<Button-1>")
        except Exception:
            pass

    def _on_cal_outside(self, event):
        """点击日历面板外时关闭"""
        if not self._cal_open:
            return
        w = event.widget
        if w is self._cal_panel:
            return
        if w is self._clock:
            return
        try:
            panel_set = {self._cal_panel} | set(self._cal_panel.winfo_children())
            # 递归收集所有子孙控件
            for child in self._cal_panel.winfo_children():
                try:
                    panel_set |= set(child.winfo_children())
                except Exception:
                    pass
            if w in panel_set:
                return
        except Exception:
            pass
        self._cal_hide()

    def _tick(self):
        now = datetime.now()
        self._clock.config(text=f"  {now.strftime('%m月%d日')}  {now.strftime('%H:%M')}  ")
        # 日历面板打开时也实时更新时间
        if self._cal_open:
            self._cal_big_time.config(text=now.strftime("%H:%M:%S"))
        self._root.after(1000, self._tick)

    def refresh_palette(self):
        t = self._palette
        self._frame.config(bg=t.get("menu_bg"))
        self._clock.config(bg=t.get("menu_bg"), fg=t.get("menu_fg"))
        self._vol_label.config(bg=t.get("menu_bg"), fg=t.get("menu_fg"))
        for b in self._btns:
            b.config(bg=t.get("menu_bg"), fg=t.get("menu_fg"))
        for m in self._menus:
            m.config(bg=t.get("bg_panel"), fg=t.get("fg"))
        self._build_view_menu()
        # 日历面板主题
        if hasattr(self, '_cal_panel'):
            self._cal_panel.config(bg=t.get("bg_panel"))
            self._cal_month_label.config(bg=t.get("bg_panel"), fg=t.get("fg"))
            self._cal_big_time.config(bg=t.get("bg_panel"), fg=t.get("fg"))
            self._cal_full_date.config(bg=t.get("bg_panel"), fg=t.get("fg_dim"))
            self._refresh_cal()


# ================================================================
# 9. 锁屏
# ================================================================
class LockScreen:
    """现代风格锁屏：Canvas原生色带渐变 + 大时钟 + 磨砂登录按钮"""
    def __init__(self, root: tk.Tk, theme: Palette, bus: EventBus):
        self._root = root
        self._palette = theme
        self._bus = bus
        self._over = None
        self._job = None

    def lock(self):
        if self._over and self._over.winfo_exists():
            return
        self._over = tk.Canvas(self._root, bg="#08080e", highlightthickness=0,
                                takefocus=0, highlightbackground="#08080e",
                                highlightcolor="#08080e")
        self._over.place(x=0, y=0, relwidth=1, relheight=1)

        self._root.update_idletasks()
        lw = self._over.winfo_width() or 1280
        lh = self._over.winfo_height() or 720

        # ---------- Canvas 原生色带渐变（极速，无PIL卡顿）----------
        # 用 25 条色带模拟垂直深色渐变，瞬间完成
        bands = 25
        band_h = lh // bands + 1
        for i in range(bands):
            t = i / (bands - 1)
            r = int(8 + 8 * t)
            g = int(8 + 6 * t)
            b = int(14 + 14 * t)
            color = f"#{r:02x}{g:02x}{b:02x}"
            self._over.create_rectangle(0, i * band_h, lw, i * band_h + band_h,
                                         fill=color, outline="", tags="ls_bg")

        # ---------- 中心内容区 ----------
        mid_y = lh // 2 - 40

        # GerOS 小标识
        self._over.create_text(lw // 2, mid_y - 135,
                                text="GerOS", fill="#4a4a60",
                                font=font(size=11, bold=True), tags="ls_g")

        # 大时钟
        now = datetime.now()
        self._t_id = self._over.create_text(lw // 2, mid_y - 50,
                                             text=now.strftime("%H:%M"),
                                             fill="#e8e8f0",
                                             font=font(size=66, bold=True), tags="ls_g")
        # 钟表阴影
        self._over.create_text(lw // 2 + 1, mid_y - 49,
                                text=now.strftime("%H:%M"),
                                fill="#000000",
                                font=font(size=66, bold=True), tags="ls_shadow")
        self._over.tag_lower("ls_shadow")

        # 日期
        wd = self._wd(now)
        self._d_id = self._over.create_text(lw // 2, mid_y + 10,
                                             text=f"{now.strftime('%Y年%m月%d日')}  {wd}",
                                             fill="#8888a0",
                                             font=font(size=13), tags="ls_g")

        # 锁图标
        self._over.create_text(lw // 2, mid_y + 60,
                                text="\U0001f512", fill="#6a6a80",
                                font=icon_font(28), tags="ls_g")

        # 提示文字
        self._over.create_text(lw // 2, mid_y + 105,
                                text="按 Esc 或点击下方按钮解锁",
                                fill="#4a4a58", font=font(size=10), tags="ls_g")

        # ---------- 自定义圆角登录按钮（预渲染两态，itemconfig切换）----------
        btn_w, btn_h = 200, 40
        btn_x, btn_y = (lw - btn_w) // 2, mid_y + 145
        self._btn_rect = (btn_x, btn_y, btn_w, btn_h)
        self._make_btn_images(btn_w, btn_h)
        cx, cy = btn_x + btn_w // 2, btn_y + btn_h // 2
        self._btn_img_id = self._over.create_image(cx, cy,
                                    image=self._btn_normal, tags=("ls_btn",))
        self._btn_txt_id = self._over.create_text(cx, cy,
                                    text="登  录", fill="#d0d0d8",
                                    font=font(size=13, bold=True), tags=("ls_btn",))

        # 用统一的 ls_btn 标签绑定所有交互（不再单独处理 ls_txt）
        self._over.tag_bind("ls_btn", "<Enter>", lambda e: self._btn_hover())
        self._over.tag_bind("ls_btn", "<Leave>", lambda e: self._btn_leave())
        self._over.tag_bind("ls_btn", "<Button-1>", lambda e: self.unlock())

        self._root.bind("<Escape>", lambda e: self.unlock())
        self._tick_lock()

    def _make_btn_images(self, bw, bh):
        """预渲染 normal / hover 两种按钮图片，存储在实例属性上"""
        s = 2
        # normal
        img = Image.new("RGBA", (bw * s, bh * s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, bw * s - 1, bh * s - 1),
                             radius=bh * s // 2, fill=(80, 85, 100))
        d.rounded_rectangle((1 * s, 1 * s, (bw - 1) * s, (bh - 1) * s),
                             radius=(bh // 2 - 1) * s, fill=(60, 65, 80))
        img = img.resize((bw, bh), Image.LANCZOS)
        self._btn_normal = ImageTk.PhotoImage(img)
        # hover
        img = Image.new("RGBA", (bw * s, bh * s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, bw * s - 1, bh * s - 1),
                             radius=bh * s // 2, fill=(120, 160, 255))
        d.rounded_rectangle((1 * s, 1 * s, (bw - 1) * s, (bh - 1) * s),
                             radius=(bh // 2 - 1) * s, fill=(90, 130, 240))
        img = img.resize((bw, bh), Image.LANCZOS)
        self._btn_hover = ImageTk.PhotoImage(img)

    def _btn_hover(self):
        try:
            self._over.itemconfig(self._btn_img_id, image=self._btn_hover)
            self._over.itemconfig(self._btn_txt_id, fill="#ffffff")
        except Exception:
            pass

    def _btn_leave(self):
        try:
            self._over.itemconfig(self._btn_img_id, image=self._btn_normal)
            self._over.itemconfig(self._btn_txt_id, fill="#d0d0d8")
        except Exception:
            pass

    def _tick_lock(self):
        if not self._over or not self._over.winfo_exists():
            return
        now = datetime.now()
        try:
            self._over.itemconfig(self._t_id, text=now.strftime("%H:%M"))
            self._over.itemconfig(self._d_id,
                text=f"{now.strftime('%Y年%m月%d日')}  {self._wd(now)}")
        except Exception:
            pass
        self._job = self._root.after(1000, self._tick_lock)

    def _wd(self, now):
        m = {"Monday":"星期一","Tuesday":"星期二","Wednesday":"星期三",
             "Thursday":"星期四","Friday":"星期五","Saturday":"星期六","Sunday":"星期日"}
        return m.get(now.strftime("%A"), now.strftime("%A"))

    def unlock(self):
        if self._job:
            self._root.after_cancel(self._job)
            self._job = None
        if self._over:
            try:
                self._over.destroy()
            except Exception:
                pass
            self._over = None
        try:
            self._root.unbind("<Escape>")
        except Exception:
            pass


# ================================================================
# 10. 桌面
# ================================================================
class Desktop:
    """壁纸画布 + 桌面图标。"""
    def __init__(self, root: tk.Tk, bus: EventBus, theme: Palette, system, theme_manager=None):
        self._root = root
        self._bus = bus
        self._palette = theme
        self._system = system
        self._tman = theme_manager
        self._orig_img = None
        self._photo = None
        self._last_cw = 0
        self._last_ch = 0

        self._canvas = tk.Canvas(root, highlightthickness=0, bd=0, bg="#1a1a1a",
                                  takefocus=0, highlightbackground="#1a1a1a",
                                  highlightcolor="#1a1a1a")
        self._canvas.pack(fill="both", expand=True)
        self._load_wallpaper()

        # 桌面图标容器
        self._icon_frame = tk.Frame(self._canvas, bg=None)
        self._canvas.create_window(50, 50, window=self._icon_frame, anchor="nw", tags="desktop")

        # 右键菜单
        self._ctx = tk.Menu(root, tearoff=0, bg=theme.get("bg_panel"), fg=theme.get("fg"),
                             font=font(size=10))
        self._ctx.add_command(label="新建文件夹", command=system.new_folder)
        self._ctx.add_command(label="新建文本文档", command=system.new_txt)
        self._ctx.add_separator()
        self._ctx.add_command(label="下一张壁纸  →", command=system.next_wallpaper)
        self._ctx.add_command(label="更换壁纸...", command=system.change_wallpaper)
        self._ctx.add_separator()
        self._ctx.add_command(label="保存为主题 (.ite)", command=system.save_theme_dialog)
        self._ctx.add_command(label="加载主题 (.ite)", command=system.load_theme_dialog)
        self._ctx.add_command(label="主题管理...", command=system.manage_themes)
        self._ctx.add_separator()
        self._ctx.add_command(label="锁定屏幕", command=system.lock)
        self._ctx.add_separator()
        self._ctx.add_command(label="清空回收站", command=system.clear_bin)

        self._canvas.bind("<Button-3>", lambda e: self._ctx.post(e.x_root, e.y_root))

    def _get_canvas_size(self):
        """获取画布实际尺寸，未渲染时返回默认值"""
        w, h = self._canvas.winfo_width(), self._canvas.winfo_height()
        if w < 10:
            self._root.update_idletasks()
            w, h = self._canvas.winfo_width(), self._canvas.winfo_height()
        if w < 10:
            w, h = 1280, 720
        return w, h

    def _render_wallpaper(self):
        """以 cover 模式将图片缩放填充到画布"""
        if self._orig_img is None:
            return
        cw, ch = self._get_canvas_size()

        # 尺寸未变则跳过，避免拖拽时反复渲染
        if cw == self._last_cw and ch == self._last_ch:
            return
        self._last_cw, self._last_ch = cw, ch

        iw, ih = self._orig_img.size

        # cover 模式：按较大比例缩放，然后居中裁剪
        scale = max(cw / iw, ch / ih)
        new_w, new_h = int(iw * scale), int(ih * scale)
        resized = self._orig_img.resize((new_w, new_h), Image.LANCZOS)

        left = (new_w - cw) // 2
        top = (new_h - ch) // 2
        cropped = resized.crop((left, top, left + cw, top + ch))

        self._photo = ImageTk.PhotoImage(cropped)
        self._canvas.delete("wp")
        self._canvas.create_image(0, 0, image=self._photo, anchor="nw", tags="wp")
        self._canvas.tag_lower("wp")

    def _load_wallpaper(self):
        try:
            # 优先使用主题管理器的当前图片
            if self._tman is not None:
                cur = self._tman.get_current_image()
                if cur and os.path.exists(cur):
                    self._orig_img = Image.open(cur)
                    self._render_wallpaper()
                    return
            sp = resource_path("system.png")
            if os.path.exists(sp):
                self._orig_img = Image.open(sp)
                self._render_wallpaper()
            else:
                self._paint_gradient()
        except Exception:
            self._paint_gradient()

    def apply_wallpaper_path(self, path: str):
        """直接应用指定路径的壁纸图片。"""
        try:
            self._orig_img = Image.open(path)
            self._last_cw, self._last_ch = 0, 0
            self._render_wallpaper()
            return True
        except Exception as e:
            messagebox.showerror("错误", f"无法加载壁纸: {e}")
            return False

    def next_wallpaper(self):
        """切换到当前主题的下一张壁纸。"""
        if self._tman is None:
            messagebox.showinfo("提示", "请先加载或选择一个主题")
            return
        path = self._tman.next_image()
        if path:
            self.apply_wallpaper_path(path)

    def prev_wallpaper(self):
        """切换到当前主题的上一张壁纸。"""
        if self._tman is None:
            messagebox.showinfo("提示", "请先加载或选择一个主题")
            return
        path = self._tman.prev_image()
        if path:
            self.apply_wallpaper_path(path)

    def resize_wallpaper(self):
        """窗口大小改变时重新渲染壁纸"""
        if self._orig_img:
            self._render_wallpaper()
        else:
            self._canvas.delete("wp")
            self._paint_gradient()

    def _paint_gradient(self):
        cw, ch = self._get_canvas_size()
        for i in range(0, ch, 4):
            r = min(255, 20 + i // 8)
            g = min(255, 40 + i // 6)
            b = min(255, 80 + i // 4)
            self._canvas.create_rectangle(0, i, cw, i + 4,
                                          fill=f"#{r:02x}{g:02x}{b:02x}",
                                          outline="", tags="wp")

    def add_icon(self, icon: str, name: str, cmd: Callable):
        f = tk.Frame(self._icon_frame, bg=None, cursor="hand2")
        f.pack(pady=12)
        il = tk.Label(f, text=icon, bg=None, font=icon_font(36))
        il.pack()
        nl = tk.Label(f, text=name, bg="#000", fg="white", font=font(size=9), padx=6, pady=2)
        nl.pack()
        for w in (il, nl, f):
            w.bind("<Enter>", lambda e: nl.config(bg=self._palette.get("accent")))
            w.bind("<Leave>", lambda e: nl.config(bg="#000"))
            w.bind("<Button-1>", lambda e, c=cmd: c())

    def canvas(self) -> tk.Canvas:
        return self._canvas

    def change_wallpaper_file(self, path: str):
        try:
            self._orig_img = Image.open(path)
            self._last_cw, self._last_ch = 0, 0   # 重置缓存，强制渲染
            self._render_wallpaper()
        except Exception as e:
            messagebox.showerror("错误", f"无法加载壁纸: {e}")


# ================================================================
# 11. 主题系统
# ================================================================
class ThemeManager:
    """主题系统：管理内置主题、外部 .ite 文件及用户自建主题。

    .ite 文件格式：ZIP 压缩包，内含 theme.json 清单 + 图片文件。
    """

    _ITE_VERSION = 1

    def __init__(self, bus: EventBus):
        self._bus = bus
        self._themes: dict[str, dict] = {}  # theme_id -> {name, images, idx, source, ...}
        self._current_theme_id: str | None = None
        self._builtin_root = self._find_builtin_root()
        # 启动后选择默认内置主题，优先 Areo
        self._builtin_themes = self.scan_builtin_themes()
        default = None
        # 优先查找 Areo 主题
        for t in self._builtin_themes:
            if t["name"].endswith("Areo") or t["id"].endswith("Areo"):
                default = t
                break
        if default is None and self._builtin_themes:
            default = self._builtin_themes[0]
        if default:
            self.set_theme(default)

    # ---------- 路径查找 ----------
    def _find_builtin_root(self) -> str:
        """查找 Ger壁纸推荐 文件夹。"""
        # 打包模式下从 _MEIPASS 查找
        try:
            mp = os.path.join(sys._MEIPASS, "Ger壁纸推荐")
            if os.path.isdir(mp):
                return mp
        except AttributeError:
            pass
        # 开发模式：从脚本所在目录查找
        base = os.path.abspath(".")
        cand = [
            os.path.join(base, "Ger壁纸推荐"),
        ]
        for p in cand:
            if os.path.isdir(p):
                return os.path.abspath(p)
        return ""

    # ---------- 内置主题扫描 ----------
    def scan_builtin_themes(self) -> list[dict]:
        """扫描 Ger壁纸推荐 文件夹，每个叶子目录为一个内置主题。"""
        themes = []
        root = self._builtin_root
        if not root or not os.path.isdir(root):
            return themes
        for dirpath, dirnames, filenames in os.walk(root):
            if dirnames:
                continue  # 只取叶子目录
            exts = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp')
            imgs = sorted(f for f in filenames if f.lower().endswith(exts))
            if not imgs:
                continue
            rel = os.path.relpath(dirpath, root)
            tid = f"builtin:{rel}"
            theme = {
                "id": tid,
                "name": rel.replace(os.sep, " → "),
                "images": [os.path.join(dirpath, img) for img in imgs],
                "current_index": 0,
                "source": "builtin",
                "path": dirpath,
            }
            self._themes[tid] = theme
            themes.append(theme)
        return themes

    # ---------- .ite 文件加载 ----------
    def load_ite_file(self, path: str) -> dict | None:
        """加载 .ite 文件（ZIP 格式），返回主题 dict 或 None。"""
        import zipfile, tempfile, shutil
        try:
            with zipfile.ZipFile(path, 'r') as zf:
                # 读取清单
                manifest = json.loads(zf.read("theme.json").decode("utf-8"))
                # 解压图片到临时目录
                tmp = tempfile.mkdtemp(prefix="ger_theme_")
                img_files = []
                for name in zf.namelist():
                    if name == "theme.json":
                        continue
                    out_path = os.path.join(tmp, os.path.basename(name))
                    with zf.open(name) as src, open(out_path, 'wb') as dst:
                        dst.write(src.read())
                    img_files.append(out_path)
                img_files.sort()
                tid = f"ite:{os.path.basename(path)}"
                theme = {
                    "id": tid,
                    "name": manifest.get("name", os.path.splitext(os.path.basename(path))[0]),
                    "images": img_files,
                    "current_index": 0,
                    "source": "ite",
                    "path": path,
                    "temp_dir": tmp,
                }
                self._themes[tid] = theme
                return theme
        except Exception as e:
            messagebox.showerror("主题错误", f"无法加载主题文件:\n{e}")
            return None

    # ---------- .ite 文件保存 ----------
    @staticmethod
    def save_ite_file(name: str, image_paths: list[str], save_path: str):
        """保存为 .ite 主题文件。"""
        import zipfile
        manifest = {"name": name, "version": ThemeManager._ITE_VERSION}
        with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("theme.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for i, img_path in enumerate(image_paths):
                ext = os.path.splitext(img_path)[1] or ".jpg"
                zf.write(img_path, f"{i:04d}{ext}")

    # ---------- 主题管理 ----------
    def set_theme(self, theme: dict):
        """切换到一个主题。"""
        self._current_theme_id = theme["id"]
        if theme["id"] not in self._themes:
            self._themes[theme["id"]] = theme

    def get_current_image(self) -> str | None:
        """返回当前主题的当前图片路径。"""
        if not self._current_theme_id:
            return None
        t = self._themes.get(self._current_theme_id)
        if not t or not t["images"]:
            return None
        idx = t.get("current_index", 0)
        return t["images"][idx]

    def next_image(self) -> str | None:
        """切换到下一张，返回新图片路径。"""
        if not self._current_theme_id:
            return None
        t = self._themes.get(self._current_theme_id)
        if not t or not t["images"]:
            return None
        t["current_index"] = (t.get("current_index", 0) + 1) % len(t["images"])
        return t["images"][t["current_index"]]

    def prev_image(self) -> str | None:
        """切换到上一张，返回新图片路径。"""
        if not self._current_theme_id:
            return None
        t = self._themes.get(self._current_theme_id)
        if not t or not t["images"]:
            return None
        t["current_index"] = (t.get("current_index", 0) - 1) % len(t["images"])
        return t["images"][t["current_index"]]

    def get_current_theme_info(self) -> dict | None:
        """返回当前主题信息。"""
        if not self._current_theme_id:
            return None
        return self._themes.get(self._current_theme_id)

    def get_all_themes(self) -> list[dict]:
        """返回所有已加载的主题列表。"""
        return list(self._themes.values())

    def create_theme_from_images(self, name: str, image_paths: list[str]) -> dict:
        """从图片列表创建一个临时主题。"""
        tid = f"custom:{name}_{int(time.time())}"
        theme = {
            "id": tid,
            "name": name,
            "images": list(image_paths),
            "current_index": 0,
            "source": "custom",
            "path": "",
        }
        self._themes[tid] = theme
        return theme

    def cleanup(self):
        """清理 .ite 解压产生的临时目录。"""
        import shutil
        for t in self._themes.values():
            td = t.get("temp_dir")
            if td and os.path.isdir(td):
                try:
                    shutil.rmtree(td, ignore_errors=True)
                except Exception:
                    pass


# ================================================================
# 12. 应用基类
# ================================================================
class App:
    """所有应用的基类。子类实现 _build() 返回 tk.Frame。"""
    def __init__(self, app_id: str, title: str, width: int, height: int,
                 bus: EventBus, theme: Palette):
        self.app_id = app_id
        self.title = title
        self.width = width
        self.height = height
        self._bus = bus
        self._palette = theme

    def _build(self) -> tk.Frame:
        """子类覆写：返回内容 Frame。"""
        raise NotImplementedError

    def build(self, parent: tk.Frame = None) -> tk.Frame:
        """构建并返回内容 Frame。"""
        content = tk.Frame(parent, bg=self._palette.get("bg"))
        content.pack(fill="both", expand=True)
        self._fill(content)
        return content

    def _fill(self, parent: tk.Frame):
        """子类覆写：填充内容到 parent。"""
        raise NotImplementedError


# ================================================================
# 12. 文件管理器（全新实现）
# ================================================================
class FileExplorerApp(App):
    def __init__(self, bus, theme):
        super().__init__("finder", "文件管理", 920, 560, bus, theme)
        self._path = os.getcwd()
        self._history: list[str] = [self._path]
        self._hpos = 0
        self._tree: ttk.Treeview | None = None
        self._path_lbl: tk.Label | None = None

    def _fill(self, parent: tk.Frame):
        t = self._palette
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(1, weight=1)

        # 侧边栏
        sb = tk.Frame(parent, bg=t.get("sidebar_bg"), width=170)
        sb.grid(row=0, column=0, rowspan=2, sticky="nsew")
        sb.grid_propagate(False)

        tk.Label(sb, text="  位置", bg=t.get("sidebar_bg"), fg=t.get("fg_dim"),
                 font=font(bold=True, size=10), anchor="w").pack(fill="x", padx=10, pady=(10, 6))

        locs = [
            ("\U0001f4c2 主目录", Path.home()),
            ("\U0001f4c1 桌面",   Path.home() / "Desktop"),
            ("\U0001f4e5 下载",   Path.home() / "Downloads"),
            ("\U0001f4bb 文档",   Path.home() / "Documents"),
            ("\U0001f4be 此电脑", Path.home().parent),
        ]
        for label, pth in locs:
            btn = tk.Button(sb, text=label, bg=t.get("sidebar_bg"), fg=t.get("fg"),
                            font=font(size=10), relief="flat", anchor="w", padx=8, pady=5,
                            command=lambda pp=str(pth): self._nav(pp))
            btn.pack(fill="x", padx=3, pady=1)

        # 工具栏
        tb = tk.Frame(parent, bg=t.get("bg_toolbar"), height=40)
        tb.grid(row=0, column=1, sticky="ew")
        tb.grid_propagate(False)
        tb.grid_columnconfigure(2, weight=1)

        for txt, cmd, col in [
            ("\u2b05 返回", self._back, 0),
            ("\u2b06 上级", self._up, 1),
        ]:
            b = tk.Label(tb, text=txt, bg=t.get("bg_toolbar"), fg=t.get("fg"),
                          font=font(size=11), cursor="hand2", padx=8)
            b.bind("<Button-1>", lambda e, c=cmd: c())
            b.bind("<Enter>", lambda e, bb=b: bb.config(bg=t.get("bg_hover")))
            b.bind("<Leave>", lambda e, bb=b: bb.config(bg=t.get("bg_toolbar")))
            b.grid(row=0, column=col, padx=3)

        self._path_lbl = tk.Label(tb, text=self._path, bg=t.get("bg_input"), fg=t.get("fg"),
                                   font=font(size=10), anchor="w", padx=8)
        self._path_lbl.grid(row=0, column=2, sticky="ew", padx=8, pady=5)

        tk.Button(tb, text="\U0001f504 刷新", bg=t.get("accent"), fg="white",
                  font=font(size=9), relief="flat", cursor="hand2",
                  command=self._load).grid(row=0, column=3, padx=8, pady=4)

        # Treeview 文件区域
        mf = tk.Frame(parent, bg=t.get("bg"))
        mf.grid(row=1, column=1, sticky="nsew")
        mf.grid_rowconfigure(0, weight=1)
        mf.grid_columnconfigure(0, weight=1)

        cols = ("name", "type", "size", "date")
        self._tree = ttk.Treeview(mf, columns=cols, show="headings", selectmode="browse")
        self._tree.heading("name", text="名称")
        self._tree.heading("type", text="类型")
        self._tree.heading("size", text="大小")
        self._tree.heading("date", text="修改时间")
        self._tree.column("name", width=400)
        self._tree.column("type", width=90)
        self._tree.column("size", width=100)
        self._tree.column("date", width=160)

        sty = ttk.Style()
        sty.configure("F.Treeview", background=t.get("bg"), foreground=t.get("fg"),
                       fieldbackground=t.get("bg"), rowheight=30, font=font(size=10))
        sty.configure("F.Treeview.Heading", background=t.get("bg_toolbar"),
                       foreground=t.get("fg"), font=font(bold=True, size=10))
        sty.map("F.Treeview", background=[("selected", t.get("accent"))])
        self._tree.config(style="F.Treeview")

        sb2 = ttk.Scrollbar(mf, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb2.set)
        self._tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        sb2.grid(row=0, column=1, sticky="ns", pady=8)

        self._tree.bind("<Double-1>", self._on_open)
        self._tree.bind("<Button-3>", self._on_ctx)

        self._load()

    def _nav(self, p: str):
        if os.path.isdir(p):
            self._path = p
            self._history = self._history[:self._hpos + 1]
            self._history.append(p)
            self._hpos = len(self._history) - 1
            self._load()

    def _up(self):
        parent = os.path.dirname(self._path)
        if parent and parent != self._path:
            self._nav(parent)

    def _back(self):
        if self._hpos > 0:
            self._hpos -= 1
            self._path = self._history[self._hpos]
            self._load()

    def _load(self):
        if not self._tree:
            return
        tv = self._tree
        for item in tv.get_children():
            tv.delete(item)

        try:
            entries = sorted(os.listdir(self._path),
                             key=lambda x: (not os.path.isdir(os.path.join(self._path, x)), x.lower()))
        except Exception as e:
            tv.insert("", "end", values=(f"\u26a0  {e}", "", "", ""))
            return

        for name in entries:
            fp = os.path.join(self._path, name)
            is_dir = os.path.isdir(fp)
            icon = "\U0001f4c1" if is_dir else "\U0001f4c4"
            ftype = "文件夹" if is_dir else os.path.splitext(name)[1].upper() or "文件"
            try:
                sz = self._fmtsize(os.path.getsize(fp)) if not is_dir else "-"
            except Exception:
                sz = "-"
            try:
                dt = datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M")
            except Exception:
                dt = "-"
            tv.insert("", "end", values=(f"{icon}  {name}", ftype, sz, dt))

        if self._path_lbl:
            self._path_lbl.config(text=self._path)

    def _fmtsize(self, s):
        for u in ["B", "KB", "MB", "GB"]:
            if s < 1024: return f"{s:.1f} {u}"
            s /= 1024
        return f"{s:.1f} TB"

    def _on_open(self, ev):
        sel = self._tree.selection()
        if not sel: return
        vals = self._tree.item(sel[0], "values")
        name = vals[0].split("  ", 1)[-1] if vals[0] else ""
        fp = os.path.join(self._path, name)
        if os.path.isdir(fp):
            self._nav(fp)
        else:
            self._bus.emit("app:open", "imageviewer", fp)

    def _on_ctx(self, ev):
        menu = tk.Menu(self._tree, tearoff=0, bg=self._palette.get("bg_panel"),
                        fg=self._palette.get("fg"), font=font(size=10))
        menu.add_command(label="刷新", command=self._load)
        menu.add_command(label="新建文件夹", command=lambda: self._bus.emit("app:new_folder"))
        menu.post(ev.x_root, ev.y_root)


# ================================================================
# 13. 终端
# ================================================================
class TerminalApp(App):
    def __init__(self, bus, theme):
        super().__init__("terminal", "终端", 750, 460, bus, theme)
        self._hist: list[str] = []
        self._hpos = -1
        self._prompt_pos = "1.0"

    def _fill(self, parent: tk.Frame):
        parent.config(bg="#0d1117")
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        self._text = tk.Text(parent, bg="#0d1117", fg="#58a6ff", font=("Consolas", 11),
                              insertbackground="#58a6ff", relief="flat", bd=0)
        self._text.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        sb = ttk.Scrollbar(parent, command=self._text.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=6)
        self._text.config(yscrollcommand=sb.set)

        self._text.insert("end", "GerOS Terminal V0.5.2\n输入 help 获取帮助\n\n")
        self._prompt()

        self._text.bind("<Return>", self._on_enter)
        self._text.bind("<Up>", lambda e: self._hist_nav(-1))
        self._text.bind("<Down>", lambda e: self._hist_nav(1))
        self._text.focus_set()

    def _prompt(self):
        self._text.insert("end", f"{os.getcwd()}> ")
        self._prompt_pos = self._text.index("insert")
        self._text.see("end")

    def _input(self) -> str:
        return self._text.get(self._prompt_pos, "end-1c")

    def _set_input(self, txt):
        self._text.delete(self._prompt_pos, "end")
        self._text.insert("end", txt)

    def _hist_nav(self, d):
        if not self._hist: return "break"
        self._hpos = max(0, min(len(self._hist) - 1, self._hpos + d))
        self._set_input(self._hist[self._hpos])
        return "break"

    def _on_enter(self, ev):
        cmd = self._input().strip()
        self._text.insert("end", "\n")
        if cmd:
            self._hist.append(cmd); self._hpos = len(self._hist)
            self._exec(cmd)
        self._prompt()
        return "break"

    def _exec(self, cmd):
        parts = cmd.split(); c = parts[0].lower(); a = parts[1:]
        out = ""
        try:
            if c == "help":
                out = "ls/dir  cd  pwd  echo  cls/clear  sysinfo  calc  tree  ver  open  exit"
            elif c in ("ls", "dir"):
                out = "\n".join(os.listdir(os.getcwd())) or "(空)"
            elif c == "cd":
                if a and os.path.isdir(a[0]):
                    os.chdir(a[0])
                else:
                    out = "cd <路径>"
            elif c == "pwd":
                out = os.getcwd()
            elif c == "echo":
                out = " ".join(a)
            elif c in ("cls", "clear"):
                self._text.delete("1.0", "end"); return
            elif c == "sysinfo":
                out = (f"CPU: {psutil.cpu_count()}核\n"
                       f"RAM: {psutil.virtual_memory().total>>30}GB "
                       f"({psutil.virtual_memory().percent}%已用)\n"
                       f"磁盘: {psutil.disk_usage(os.sep).total>>30}GB")
            elif c == "calc":
                out = str(eval(" ".join(a))) if a else "calc <表达式>"
            elif c == "tree":
                out = self._tree(os.getcwd(), 0)
            elif c == "ver":
                out = "GerOS V0.5.2"
            elif c == "open":
                if a:
                    path = os.path.abspath(a[0])
                    self._bus.emit("app:open", "imageviewer", path) if os.path.isfile(path) else None
                else: out = "open <文件>"
            elif c == "exit":
                self._bus.emit("window:close", "terminal")
                return
            else:
                out = f"'{c}' 不是有效命令"
        except Exception as e:
            out = f"错误: {e}"
        self._text.insert("end", out + "\n")

    def _tree(self, path, depth, mx=3):
        if depth > mx: return ""
        try:
            entries = sorted(os.listdir(path))
        except Exception: return "  " * depth + "[!]\n"
        r = ""
        for e in entries[:20]:
            fp = os.path.join(path, e)
            r += "  " * depth + ("\U0001f4c1 " if os.path.isdir(fp) else "\U0001f4c4 ") + e + "\n"
            if os.path.isdir(fp):
                r += self._tree(fp, depth + 1, mx)
        return r


# ================================================================
# 14. 计算器
# ================================================================
class CalculatorApp(App):
    def __init__(self, bus, theme):
        super().__init__("calculator", "计算器", 300, 400, bus, theme)
        self._expr = ""

    def _fill(self, parent: tk.Frame):
        t = self._palette
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        self._disp = tk.Label(parent, text="0", bg=t.get("bg"), fg=t.get("fg"),
                               font=font(size=28), anchor="e", padx=14, pady=14)
        self._disp.grid(row=0, column=0, sticky="ew", padx=6, pady=6)

        btns = tk.Frame(parent, bg=t.get("bg"))
        btns.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        for i in range(6): btns.grid_rowconfigure(i, weight=1)
        for i in range(4): btns.grid_columnconfigure(i, weight=1)

        layout = [
            ["C", "\u00b1", "%", "\u00f7"],
            ["7", "8", "9", "\u00d7"],
            ["4", "5", "6", "-"],
            ["1", "2", "3", "+"],
            ["0", ".", "="],
        ]
        for r, row in enumerate(layout):
            cs = 2 if row[0] == "0" else 1
            col = 0
            for txt in row:
                ops = {"\u00f7", "\u00d7", "-", "+", "="}
                bg = t.get("accent") if txt in ops else t.get("bg_panel")
                fg = "white" if txt in ops else t.get("fg")
                if txt in ("C", "\u00b1", "%"):
                    bg, fg = t.get("bg_toolbar"), t.get("fg")
                btn = tk.Button(btns, text=txt, font=font(size=16), relief="flat", bd=0,
                                 cursor="hand2", bg=bg, fg=fg, takefocus=0,
                                 command=lambda x=txt: self._click(x))
                btn.grid(row=r, column=col, columnspan=cs, sticky="nsew", padx=2, pady=2)
                col += cs; cs = 1

        parent.bind("<Key>", self._key)

    def _click(self, ch):
        if ch == "C": self._expr = ""
        elif ch == "\u00b1":
            try: self._expr = str(-float(self._expr or 0))
            except Exception: pass
        elif ch == "%":
            try: self._expr = str(float(self._expr or 0) / 100)
            except Exception: pass
        elif ch == "=":
            try:
                self._expr = str(eval(self._expr.replace("\u00f7", "/").replace("\u00d7", "*")))
            except Exception:
                self._expr = "错误"
        else: self._expr += ch
        self._update()

    def _update(self):
        self._disp.config(text=self._expr[-14:] or "0")

    def _key(self, ev):
        m = {"\r": "=", "/": "\u00f7", "*": "\u00d7", "c": "C", "C": "C"}
        ch = m.get(ev.char, ev.char)
        if ch in "0123456789.+-=C\u00f7\u00d7%":
            self._click(ch)
            return "break"
        if ev.keysym == "BackSpace":
            self._expr = self._expr[:-1]; self._update()
            return "break"
        if ev.keysym == "Escape":
            self._expr = ""; self._update()
            return "break"


# ================================================================
# 15. 备忘录
# ================================================================
class NotepadApp(App):
    def __init__(self, bus, theme):
        super().__init__("notepad", "备忘录", 680, 460, bus, theme)
        self._filepath: str | None = None

    def _fill(self, parent: tk.Frame):
        t = self._palette
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        tb = tk.Frame(parent, bg=t.get("bg_toolbar"), height=36)
        tb.grid(row=0, column=0, sticky="ew")
        tb.grid_propagate(False)

        tk.Button(tb, text="新建", bg=t.get("bg_toolbar"), fg=t.get("fg"), relief="flat",
                  font=font(size=10), command=self._new).pack(side="left", padx=4, pady=4)
        tk.Button(tb, text="打开", bg=t.get("bg_toolbar"), fg=t.get("fg"), relief="flat",
                  font=font(size=10), command=self._open).pack(side="left", padx=4, pady=4)
        tk.Button(tb, text="保存", bg=t.get("accent"), fg="white", relief="flat",
                  font=font(size=10), command=self._save).pack(side="left", padx=4, pady=4)

        self._title = tk.Label(tb, text="未命名", bg=t.get("bg_toolbar"), fg=t.get("fg"),
                                font=font(size=10))
        self._title.pack(side="right", padx=12)

        self._editor = scrolledtext.ScrolledText(parent, bg=t.get("bg"), fg=t.get("fg"),
                                                  font=font(size=12), relief="flat", bd=0,
                                                  highlightthickness=0,
                                                  insertbackground=t.get("fg"))
        self._editor.grid(row=1, column=0, sticky="nsew", padx=12, pady=12)
        self._editor.insert("1.0", "欢迎使用 GerOS 备忘录\n\n在这里记录您的想法...")

    def _new(self):
        self._filepath = None
        self._title.config(text="未命名")
        self._editor.delete("1.0", "end")

    def _open(self):
        path = filedialog.askopenfilename(filetypes=[("文本", "*.txt"), ("所有", "*.*")])
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._editor.delete("1.0", "end")
                    self._editor.insert("1.0", f.read())
                self._filepath = path
                self._title.config(text=os.path.basename(path))
            except Exception as e:
                messagebox.showerror("错误", str(e))

    def _save(self):
        if not self._filepath:
            self._filepath = filedialog.asksaveasfilename(defaultextension=".txt",
                                                            filetypes=[("文本", "*.txt")])
        if self._filepath:
            try:
                with open(self._filepath, "w", encoding="utf-8") as f:
                    f.write(self._editor.get("1.0", "end"))
                self._title.config(text=os.path.basename(self._filepath))
                messagebox.showinfo("完成", "已保存")
            except Exception as e:
                messagebox.showerror("错误", str(e))


# ================================================================
# 16. 系统设置
# ================================================================
class SettingsApp(App):
    def __init__(self, bus, theme, system):
        super().__init__("settings", "系统设置", 660, 480, bus, theme)
        self._system = system
        self._panels: dict[str, tk.Frame] = {}
        self._btns: list[tk.Button] = []

    def _fill(self, parent: tk.Frame):
        t = self._palette
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=1)

        sb = tk.Frame(parent, bg=t.get("sidebar_bg"), width=160)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)

        tabs = ["通用", "显示", "个性化", "声音", "安全", "关于本机"]
        for name in tabs:
            btn = tk.Button(sb, text=f"  {name}", bg=t.get("sidebar_bg"), fg=t.get("fg"),
                            font=font(size=12), relief="flat", anchor="w", pady=10, padx=12,
                            command=lambda n=name: self._switch(n))
            btn.pack(fill="x", padx=3, pady=1)
            self._btns.append((btn, name))

        self._panel = tk.Frame(parent, bg=t.get("bg"))
        self._panel.grid(row=0, column=1, sticky="nsew", padx=16, pady=16)
        self._switch("通用")

    def _switch(self, name):
        for w in self._panel.winfo_children():
            w.destroy()
        for btn, n in self._btns:
            btn.config(bg=self._palette.get("sidebar_bg") if n != name else self._palette.get("bg_hover"))

        t = self._palette
        if name == "通用":
            self._label("通用", sz=20, bl=True)
            self._label("GerOS V0.5.2 模拟桌面系统")
            self._label("全新独立模块化架构，各组件通过事件总线通信")
        elif name == "显示":
            self._label("显示", sz=20, bl=True)
            self._label(f"当前配色: {'暗色' if t.mode == 'dark' else '亮色'}")
            tk.Button(self._panel, text="切换配色", bg=t.get("accent"), fg="white",
                      font=font(size=11), relief="flat", command=self._system.toggle_palette
                      ).pack(anchor="w", pady=10)
        elif name == "个性化":
            self._label("个性化", sz=20, bl=True)
            self._build_personalize()

        elif name == "声音":
            self._label("声音", sz=20, bl=True)
            self._label("启动音效: Ring10.wav")
            self._label("关机音效: Windows Logoff Sound.wav")
            tk.Button(self._panel, text="试听启动音效", bg=t.get("accent"), fg="white",
                      font=font(size=11), relief="flat", command=lambda: Sound.play("Ring10.wav")
                      ).pack(anchor="w", pady=10)
        elif name == "安全":
            self._label("安全", sz=20, bl=True)
            self._label("按 Esc 或菜单锁定屏幕")
            tk.Button(self._panel, text="立即锁定", bg=t.get("accent"), fg="white",
                      font=font(size=11), relief="flat", command=self._system.lock
                      ).pack(anchor="w", pady=10)
        elif name == "关于本机":
            self._label("关于本机", sz=20, bl=True)
            for k, v in [
                ("版本", "GerOS V0.5.2"),
                ("CPU", f"{psutil.cpu_count()} 核心"),
                ("内存", f"{psutil.virtual_memory().total >> 30} GB"),
                ("磁盘", f"{psutil.disk_usage(os.sep).total >> 30} GB"),
                ("架构", "EventBus 事件驱动"),
            ]:
                row = tk.Frame(self._panel, bg=t.get("bg"))
                row.pack(fill="x", pady=5)
                tk.Label(row, text=k, bg=t.get("bg"), fg=t.get("fg_dim"), font=font(size=11),
                         width=8, anchor="w").pack(side="left")
                tk.Label(row, text=v, bg=t.get("bg"), fg=t.get("fg"),
                         font=font(bold=True, size=11)).pack(side="left")

    def _label(self, text, sz=11, bl=False):
        tk.Label(self._panel, text=text, bg=self._palette.get("bg"), fg=self._palette.get("fg"),
                 font=font(bold=bl, size=sz), anchor="w").pack(fill="x", pady=(0 if bl else 4, 8 if bl else 4))

    def navigate_to(self, name: str):
        """从外部切换到指定面板。"""
        self._switch(name)

    def _update_preview(self):
        """刷新壁纸预览缩略图。"""
        if not hasattr(self, '_preview_cv') or self._preview_cv is None:
            return
        try:
            if not self._preview_cv.winfo_exists():
                return
        except Exception:
            return
        pw, ph = 180, 110
        self._preview_cv.delete("all")
        tman = self._system._tman
        info = tman.get_current_theme_info()
        cur = tman.get_current_image()
        self._wp_name.config(text=info["name"] if info else "未选择主题")
        if info:
            self._wp_info.config(text=f"壁纸 {info.get('current_index', 0) + 1} / {len(info['images'])}")
        else:
            self._wp_info.config(text="")
        if cur and os.path.exists(cur):
            try:
                img = Image.open(cur)
                iw, ih = img.size
                s = min(pw / iw, ph / ih)
                img = img.resize((int(iw * s), int(ih * s)), Image.LANCZOS)
                self._preview_photo = ImageTk.PhotoImage(img)
                self._preview_cv.create_image(pw // 2, ph // 2, image=self._preview_photo, anchor="center")
            except Exception:
                self._preview_cv.create_text(pw // 2, ph // 2, text="预览失败", fill="#888", font=font(size=10))
        else:
            self._preview_cv.create_text(pw // 2, ph // 2, text="无预览", fill="#888", font=font(size=10))

    def _build_personalize(self):
        """构建个性化面板（壁纸预览 + 主题管理）。"""
        t = self._palette
        S = self._system
        tman = S._tman

        # ── 壁纸预览区 ──
        pf = tk.Frame(self._panel, bg=t.get("bg"))
        pf.pack(fill="x", pady=(0, 12))
        pw, ph = 180, 110
        self._preview_cv = tk.Canvas(pf, bg=t.get("bg_panel"), width=pw, height=ph,
                                      highlightthickness=1, bd=0,
                                      highlightbackground=t.get("border"),
                                      highlightcolor=t.get("border"),
                                      takefocus=0)
        self._preview_cv.pack(side="left", padx=(0, 10))
        self._preview_photo = None

        info_f = tk.Frame(pf, bg=t.get("bg"))
        info_f.pack(side="left", fill="both", expand=True)
        self._wp_name = tk.Label(info_f, text="", bg=t.get("bg"),
                                  fg=t.get("fg"), font=font(bold=True, size=12), anchor="w")
        self._wp_name.pack(fill="x")
        self._wp_info = tk.Label(info_f, text="", bg=t.get("bg"),
                                  fg=t.get("fg_dim"), font=font(size=10), anchor="w")
        self._wp_info.pack(fill="x", pady=(4, 0))

        nav = tk.Frame(info_f, bg=t.get("bg"))
        nav.pack(fill="x", pady=(8, 0))
        tk.Button(nav, text="◀", bg=t.get("accent"), fg="white", font=font(size=10), relief="flat",
                  width=3, command=lambda: (S.prev_wallpaper(), self._update_preview())
                  ).pack(side="left", padx=(0, 3))
        tk.Button(nav, text="▶", bg=t.get("accent"), fg="white", font=font(size=10), relief="flat",
                  width=3, command=lambda: (S.next_wallpaper(), self._update_preview())
                  ).pack(side="left", padx=(0, 8))
        tk.Button(nav, text="更换壁纸", bg=t.get("bg_panel"), fg=t.get("fg"),
                  font=font(size=9), relief="flat",
                  command=lambda: (S.change_wallpaper(), self._update_preview())
                  ).pack(side="left")

        # ── 主题列表 ──
        self._label("壁纸主题", sz=13, bl=True)
        lf = tk.Frame(self._panel, bg=t.get("bg"))
        lf.pack(fill="both", expand=True, pady=(0, 6))

        def _refresh_list():
            for w in lf.winfo_children():
                w.destroy()
            themes = tman.get_all_themes()
            if not themes:
                tk.Label(lf, text="(暂无主题)", bg=t.get("bg"), fg=t.get("fg_dim"),
                         font=font(size=11)).pack(pady=24)
                return
            cur_id = tman._current_theme_id
            for th in themes:
                icon = {"builtin": "\U0001f5bc", "ite": "\U0001f4c2", "custom": "\U0001f3a8"}.get(th["source"], "\U0001f5bc")
                active = th["id"] == cur_id
                row = tk.Frame(lf, bg=t.get("bg_hover") if active else t.get("bg"),
                               cursor="hand2", height=34)
                row.pack(fill="x", padx=2, pady=1)
                row.pack_propagate(False)
                tk.Label(row, text=icon, bg=row["bg"], font=icon_font(13)).pack(side="left", padx=(8, 3))
                tk.Label(row, text=f"{th['name']}  ({len(th['images'])} 张)",
                         bg=row["bg"], fg=t.get("fg"), font=font(size=11), anchor="w"
                         ).pack(side="left", fill="x", expand=True, padx=4)

                def _apply(th_=th):
                    tman.set_theme(th_)
                    S._apply_theme(th_)
                    _refresh_list()
                    self._update_preview()

                for c in [row] + list(row.winfo_children()):
                    c.bind("<Button-1>", lambda e, t_=th: _apply(t_))
                    c.bind("<Enter>", lambda e, r=row: r.config(bg=t.get("bg_hover")))
                    c.bind("<Leave>", lambda e, r=row, tid=th["id"]:
                           r.config(bg=t.get("bg_hover") if tid == tman._current_theme_id else t.get("bg")))

                if th["source"] != "builtin":
                    db = tk.Label(row, text="\u2715", bg=row["bg"], fg="#e55",
                                   font=font(size=11, bold=True), cursor="hand2")
                    db.pack(side="right", padx=8)

                    def _delete(th_=th):
                        if messagebox.askyesno("删除", f"确定删除 '{th_['name']}'？"):
                            td = th_.get("temp_dir")
                            if td and os.path.isdir(td):
                                import shutil
                                try: shutil.rmtree(td, ignore_errors=True)
                                except: pass
                            if th_["id"] in tman._themes:
                                del tman._themes[th_["id"]]
                            if tman._current_theme_id == th_["id"]:
                                tman._current_theme_id = None
                            _refresh_list()
                            self._update_preview()

                    db.bind("<Button-1>", lambda e, t_=th: _delete(t_))
                    db.bind("<Enter>", lambda e, b=db: b.config(fg="#f88"))
                    db.bind("<Leave>", lambda e, b=db: b.config(fg="#e55"))

        # ── 操作按钮 ──
        bf = tk.Frame(self._panel, bg=t.get("bg"))
        bf.pack(fill="x", pady=(4, 0))

        def _create():
            paths = filedialog.askopenfilenames(title="选择壁纸图片",
                filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp")])
            if not paths: return
            name = simpledialog.askstring("新建", "主题名称:")
            if not name: return
            th = tman.create_theme_from_images(name, list(paths))
            tman.set_theme(th)
            S._apply_theme(th)
            _refresh_list()
            self._update_preview()
            S._toast.show("主题", f"'{name}' 已创建")

        def _load():
            S.load_theme_dialog()
            _refresh_list()
            self._update_preview()

        for txt, cmd, accent in [
            ("新建主题", _create, True),
            ("加载 .ite", _load, False),
            ("导出 .ite", S.save_theme_dialog, False),
        ]:
            tk.Button(bf, text=txt, command=cmd,
                      bg=t.get("accent") if accent else t.get("bg_panel"),
                      fg="white" if accent else t.get("fg"),
                      font=font(size=10), relief="flat", padx=10, cursor="hand2"
                      ).pack(side="left", padx=3)

        _refresh_list()
        self._update_preview()


# ================================================================
# 17. 日历
# ================================================================
class CalendarApp(App):
    def __init__(self, bus, theme):
        super().__init__("calendar", "日历", 400, 400, bus, theme)
        self._y = datetime.now().year
        self._m = datetime.now().month

    def _fill(self, parent: tk.Frame):
        t = self._palette
        hd = tk.Frame(parent, bg=t.get("bg_toolbar"))
        hd.pack(fill="x", padx=8, pady=8)
        tk.Button(hd, text="<", bg=t.get("bg_toolbar"), fg=t.get("fg"), relief="flat",
                  command=self._prev).pack(side="left", padx=5)
        self._ml = tk.Label(hd, text="", bg=t.get("bg_toolbar"), fg=t.get("fg"),
                             font=font(bold=True, size=14))
        self._ml.pack(side="left", expand=True)
        tk.Button(hd, text=">", bg=t.get("bg_toolbar"), fg=t.get("fg"), relief="flat",
                  command=self._next).pack(side="right", padx=5)

        self._grid = tk.Frame(parent, bg=t.get("bg"))
        self._grid.pack(fill="both", expand=True, padx=8, pady=8)
        for i, d in enumerate(["日", "一", "二", "三", "四", "五", "六"]):
            tk.Label(self._grid, text=d, bg=t.get("bg"), fg=t.get("fg_dim"),
                     font=font(bold=True, size=10)).grid(row=0, column=i, sticky="nsew", pady=4)
        for i in range(7): self._grid.grid_columnconfigure(i, weight=1)
        self._render()

    def _render(self):
        for w in self._grid.winfo_children():
            if int(w.grid_info().get("row", 0)) > 0:
                w.destroy()
        self._ml.config(text=f"{self._y}年{self._m}月")
        first = datetime(self._y, self._m, 1)
        swd = (first.weekday() + 1) % 7  # Sunday = 0
        if self._m == 12:
            nd = (datetime(self._y + 1, 1, 1) - first).days
        else:
            nd = (datetime(self._y, self._m + 1, 1) - first).days

        today = datetime.now()
        r, c = 1, swd
        for d in range(1, nd + 1):
            bg = self._palette.get("bg"); fg = self._palette.get("fg")
            if self._y == today.year and self._m == today.month and d == today.day:
                bg = self._palette.get("accent"); fg = "white"
            tk.Label(self._grid, text=str(d), bg=bg, fg=fg, font=font(size=11),
                     padx=6, pady=6).grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
            c += 1
            if c > 6: c = 0; r += 1

    def _prev(self):
        self._m -= 1
        if self._m < 1: self._m = 12; self._y -= 1
        self._render()

    def _next(self):
        self._m += 1
        if self._m > 12: self._m = 1; self._y += 1
        self._render()


# ================================================================
# 18. 时钟（时钟 / 计时器 / 秒表）
# ================================================================
class ClockApp(App):
    def __init__(self, bus, theme):
        super().__init__("clock", "时钟", 400, 380, bus, theme)
        self._tab = "clock"
        self._timer_remaining = 0
        self._timer_running = False
        self._timer_total = 0
        self._sw_ms = 0
        self._sw_running = False
        self._laps = []
        self._built = set()          # 已构建的面板
        self._tick_id = None         # 时钟 after id
        self._timer_id = None        # 计时器 after id
        self._sw_id = None           # 秒表 after id
        self._flash_id = None        # 闪烁 after id

    def _fill(self, parent: tk.Frame):
        t = self._palette
        # 选项卡按钮栏
        tab_bar = tk.Frame(parent, bg=t.get("bg_toolbar"), height=36)
        tab_bar.pack(fill="x")
        tab_bar.pack_propagate(False)

        self._tab_btns = {}
        for tid, txt in [("clock", "时钟"), ("timer", "计时器"), ("stopwatch", "秒表")]:
            btn = tk.Label(tab_bar, text=txt, bg=t.get("bg_toolbar"),
                           fg=t.get("fg_dim"), font=font(size=10), cursor="hand2",
                           padx=16, pady=6)
            btn.pack(side="left")
            btn.bind("<Button-1>", lambda e, tid=tid: self._switch_tab(tid))
            self._tab_btns[tid] = btn

        # 内容区
        self._holder = tk.Frame(parent, bg=t.get("bg"))
        self._holder.pack(fill="both", expand=True)

        # 只预先创建面板容器，不填充内容（延迟构建）
        self._panels = {}
        for tid in ("clock", "timer", "stopwatch"):
            p = tk.Frame(self._holder, bg=t.get("bg"))
            self._panels[tid] = p

        # 默认显示时钟面板
        self._switch_tab("clock")

    def _switch_tab(self, tid):
        self._tab = tid
        t = self._palette
        # 高亮当前选项卡
        for k, btn in self._tab_btns.items():
            btn.config(bg=t.get("bg") if k == tid else t.get("bg_toolbar"),
                       fg=t.get("accent") if k == tid else t.get("fg_dim"))
        # 切换面板
        for k, p in self._panels.items():
            if k == tid:
                p.pack(fill="both", expand=True)
            else:
                p.pack_forget()
        # 延迟构建（首次切换到该面板时）
        if tid not in self._built:
            self._built.add(tid)
            if tid == "clock":
                self._build_clock()
            elif tid == "timer":
                self._build_timer()
            elif tid == "stopwatch":
                self._build_stopwatch()

    # ==================== 时钟面板 ====================
    def _build_clock(self):
        p = self._panels["clock"]
        t = self._palette
        p.grid_rowconfigure(0, weight=1)
        p.grid_columnconfigure(0, weight=1)

        inner = tk.Frame(p, bg=t.get("bg"))
        inner.grid(row=0, column=0)

        self._clk_time = tk.Label(inner, text="", bg=t.get("bg"), fg=t.get("fg"),
                                   font=font(size=42, bold=True))
        self._clk_time.pack()
        self._clk_date = tk.Label(inner, text="", bg=t.get("bg"), fg=t.get("fg_dim"),
                                    font=font(size=12))
        self._clk_date.pack(pady=(4, 0))
        self._clk_tick()

    def _clk_tick(self):
        if self._tab != "clock":
            self._tick_id = self._clk_time.after(1000, self._clk_tick)
            return
        try:
            now = datetime.now()
            weeks = ["星期一","星期二","星期三","星期四","星期五","星期六","星期日"]
            self._clk_time.config(text=now.strftime("%H:%M:%S"))
            self._clk_date.config(text=f"{now.year}年{now.month}月{now.day}日 {weeks[now.weekday()]}")
        except Exception:
            pass
        self._tick_id = self._clk_time.after(200, self._clk_tick)

    # ==================== 计时器面板 ====================
    def _build_timer(self):
        p = self._panels["timer"]
        t = self._palette
        p.grid_rowconfigure(0, weight=0)
        p.grid_rowconfigure(1, weight=0)
        p.grid_rowconfigure(2, weight=0)
        p.grid_rowconfigure(3, weight=0)
        p.grid_columnconfigure(0, weight=1)

        # 时间输入行
        row0 = tk.Frame(p, bg=t.get("bg"))
        row0.grid(row=0, column=0, pady=(30, 10))

        lbl_style = {"bg": t.get("bg"), "fg": t.get("fg_dim"), "font": font(size=9)}
        ent_style = {"width": 3, "font": font(size=14), "justify": "center",
                     "bg": t.get("bg_input"), "fg": t.get("fg"), "relief": "flat",
                     "insertbackground": t.get("fg")}

        for i, (label, mx) in enumerate([("时", 99), ("分", 59), ("秒", 59)]):
            tk.Label(row0, text=label, **lbl_style).grid(row=0, column=i*3, padx=2)
            ent = tk.Entry(row0, **ent_style)
            ent.grid(row=0, column=i*3+1)
            ent.insert(0, "0")
            ent.bind("<KeyRelease>", lambda e, e_=ent, m_=mx: self._tm_clamp(e_, m_))
            setattr(self, f"_tm_{'hms'[i]}", ent)
            # 上下按钮
            btns = tk.Frame(row0, bg=t.get("bg"))
            btns.grid(row=0, column=i*3+2, padx=(0, 2))
            for txt, delta in [("+", 1), ("-", -1)]:
                lb = tk.Label(btns, text=txt, bg=t.get("bg_panel"), fg=t.get("fg_dim"),
                              font=font(size=7), padx=3, cursor="hand2")
                lb.pack()
                lb.bind("<Button-1>", lambda e, e_=ent, d_=delta, m_=mx: self._tm_step(e_, d_, m_))

        # 倒计时大字
        self._tm_display = tk.Label(p, text="00:00:00", bg=t.get("bg"),
                                     fg=t.get("fg"), font=font(size=38, bold=True))
        self._tm_display.grid(row=1, column=0, pady=(10, 10))

        # 进度条
        self._tm_progress = tk.Canvas(p, bg=t.get("bg_toolbar"),
                                       height=6, highlightthickness=0, bd=0,
                                       takefocus=0)
        self._tm_progress.grid(row=2, column=0, sticky="ew", padx=40, pady=(0, 14))

        # 按钮行
        row3 = tk.Frame(p, bg=t.get("bg"))
        row3.grid(row=3, column=0, pady=(0, 20))
        btn_cfg = {"width": 8, "relief": "flat", "font": font(size=10)}

        self._tm_start_btn = tk.Button(row3, text="开始", bg=t.get("accent"), fg="white",
                                        activebackground=t.get("accent_hover"),
                                        command=self._timer_start, **btn_cfg)
        self._tm_start_btn.pack(side="left", padx=3)

        self._tm_pause_btn = tk.Button(row3, text="暂停", bg=t.get("warning"), fg="white",
                                        command=self._timer_pause, state="disabled", **btn_cfg)
        self._tm_pause_btn.pack(side="left", padx=3)

        self._tm_reset_btn = tk.Button(row3, text="重置", bg=t.get("bg_panel"), fg=t.get("fg"),
                                        command=self._timer_reset, **btn_cfg)
        self._tm_reset_btn.pack(side="left", padx=3)

    def _tm_clamp(self, ent, mx):
        try:
            s = ent.get()
            if not s:
                return
            v = int(s)
            v = max(0, min(v, mx))
            ent.delete(0, "end")
            ent.insert(0, str(v))
        except Exception:
            ent.delete(0, "end")
            ent.insert(0, "0")

    def _tm_step(self, ent, delta, mx):
        try:
            v = int(ent.get() or 0) + delta
            ent.delete(0, "end")
            ent.insert(0, str(max(0, min(v, mx))))
        except Exception:
            ent.delete(0, "end")
            ent.insert(0, "0")

    def _timer_start(self):
        if self._timer_running:
            return
        try:
            h = int(self._tm_h.get() or 0)
            m = int(self._tm_m.get() or 0)
            s = int(self._tm_s.get() or 0)
        except Exception:
            return
        self._timer_total = h * 3600 + m * 60 + s
        if self._timer_total <= 0:
            return
        self._timer_remaining = self._timer_total
        self._timer_running = True
        for e in (self._tm_h, self._tm_m, self._tm_s):
            e.config(state="disabled")
        self._tm_start_btn.config(state="disabled")
        self._tm_pause_btn.config(state="normal")
        self._timer_show()
        self._timer_tick()

    def _timer_pause(self):
        self._timer_running = False
        if self._timer_id:
            self._tm_display.after_cancel(self._timer_id)
            self._timer_id = None
        self._tm_start_btn.config(state="normal", text="继续")
        self._tm_pause_btn.config(state="disabled")

    def _timer_reset(self):
        self._timer_running = False
        self._timer_remaining = 0
        self._timer_total = 0
        if self._timer_id:
            try: self._tm_display.after_cancel(self._timer_id)
            except Exception: pass
            self._timer_id = None
        if self._flash_id:
            try: self._tm_display.after_cancel(self._flash_id)
            except Exception: pass
            self._flash_id = None
        for e, v in [(self._tm_h, "0"), (self._tm_m, "0"), (self._tm_s, "0")]:
            try:
                e.config(state="normal")
                e.delete(0, "end")
                e.insert(0, v)
            except Exception:
                pass
        self._tm_start_btn.config(state="normal", text="开始")
        self._tm_pause_btn.config(state="disabled")
        self._tm_display.config(text="00:00:00", fg=self._palette.get("fg"))
        try:
            self._tm_progress.delete("all")
        except Exception:
            pass

    def _timer_show(self):
        r = self._timer_remaining
        self._tm_display.config(text=f"{r//3600:02d}:{(r%3600)//60:02d}:{r%60:02d}")
        try:
            self._tm_progress.delete("all")
            pw = self._tm_progress.winfo_width() or 300
            ratio = r / max(self._timer_total, 1)
            color = self._palette.get("danger") if ratio < 0.15 else self._palette.get("accent")
            self._tm_progress.create_rectangle(0, 0, int(pw * ratio), 6, fill=color, outline="")
        except Exception:
            pass

    def _timer_tick(self):
        if not self._timer_running:
            return
        self._timer_remaining -= 1
        if self._timer_remaining <= 0:
            self._timer_remaining = 0
            self._timer_running = False
            self._timer_show()
            self._tm_display.config(fg=self._palette.get("danger"))
            self._timer_flash(6)
            try:
                Sound.play("Ring10.wav")
            except Exception:
                pass
            self._timer_reset()
            return
        self._timer_show()
        self._timer_id = self._tm_display.after(1000, self._timer_tick)

    def _timer_flash(self, n):
        if n <= 0:
            try:
                self._tm_display.config(fg=self._palette.get("fg"))
            except Exception:
                pass
            return
        t = self._palette
        try:
            cur = str(self._tm_display.cget("fg"))
        except Exception:
            cur = t.get("fg")
        nxt = t.get("bg") if cur == t.get("danger") else t.get("danger")
        self._tm_display.config(fg=nxt)
        self._flash_id = self._tm_display.after(250, lambda: self._timer_flash(n - 1))

    # ==================== 秒表面板 ====================
    def _build_stopwatch(self):
        p = self._panels["stopwatch"]
        t = self._palette
        p.grid_rowconfigure(0, weight=1)
        p.grid_rowconfigure(1, weight=0)
        p.grid_rowconfigure(2, weight=0)
        p.grid_columnconfigure(0, weight=1)

        # 计时显示
        r0 = tk.Frame(p, bg=t.get("bg"))
        r0.grid(row=0, column=0, pady=(50, 0))

        self._sw_display = tk.Label(r0, text="00:00:00.0", bg=t.get("bg"),
                                     fg=t.get("fg"), font=font(size=36, bold=True))
        self._sw_display.pack()

        # 按钮行
        r1 = tk.Frame(p, bg=t.get("bg"))
        r1.grid(row=1, column=0, pady=(20, 10))
        btn_cfg = {"width": 8, "relief": "flat", "font": font(size=10)}

        self._sw_start_btn = tk.Button(r1, text="开始", bg=t.get("accent"), fg="white",
                                        activebackground=t.get("accent_hover"),
                                        command=self._sw_start, **btn_cfg)
        self._sw_start_btn.pack(side="left", padx=3)

        self._sw_pause_btn = tk.Button(r1, text="暂停", bg=t.get("warning"), fg="white",
                                        command=self._sw_pause, state="disabled", **btn_cfg)
        self._sw_pause_btn.pack(side="left", padx=3)

        self._sw_reset_btn = tk.Button(r1, text="重置", bg=t.get("bg_panel"), fg=t.get("fg"),
                                        command=self._sw_reset, **btn_cfg)
        self._sw_reset_btn.pack(side="left", padx=3)

        self._sw_lap_btn = tk.Button(r1, text="计次", bg=t.get("bg_panel"), fg=t.get("fg"),
                                      command=self._sw_lap, state="disabled", **btn_cfg)
        self._sw_lap_btn.pack(side="left", padx=3)

        # 计次记录
        r2 = tk.Frame(p, bg=t.get("bg"))
        r2.grid(row=2, column=0, sticky="nsew", padx=30, pady=(0, 10))
        self._sw_lap_list = tk.Label(r2, text="", bg=t.get("bg"),
                                      fg=t.get("fg_dim"), font=font(size=9),
                                      justify="left", anchor="nw")
        self._sw_lap_list.pack(fill="both", expand=True)

    def _sw_start(self):
        if self._sw_running:
            return
        self._sw_running = True
        self._sw_start_btn.config(state="disabled")
        self._sw_pause_btn.config(state="normal")
        self._sw_lap_btn.config(state="normal")
        self._sw_tick()

    def _sw_pause(self):
        self._sw_running = False
        if self._sw_id:
            try: self._sw_display.after_cancel(self._sw_id)
            except Exception: pass
            self._sw_id = None
        self._sw_start_btn.config(state="normal", text="继续")
        self._sw_pause_btn.config(state="disabled")
        self._sw_lap_btn.config(state="disabled")

    def _sw_reset(self):
        self._sw_running = False
        self._sw_ms = 0
        self._laps.clear()
        if self._sw_id:
            try: self._sw_display.after_cancel(self._sw_id)
            except Exception: pass
            self._sw_id = None
        self._sw_start_btn.config(state="normal", text="开始")
        self._sw_pause_btn.config(state="disabled")
        self._sw_lap_btn.config(state="disabled")
        self._sw_display.config(text="00:00:00.0")
        self._sw_lap_list.config(text="")

    def _sw_lap(self):
        try:
            t = self._sw_display.cget("text")
        except Exception:
            t = "00:00:00.0"
        self._laps.append(str(t))
        recent = self._laps[-5:]
        lines = [f"  #{len(self._laps)-i}   {x}"
                 for i, x in enumerate(reversed(recent))]
        self._sw_lap_list.config(text="\n".join(lines))

    def _sw_tick(self):
        if not self._sw_running:
            return
        self._sw_ms += 100
        ms = self._sw_ms
        try:
            self._sw_display.config(
                text=f"{ms//3600000:02d}:{(ms%3600000)//60000:02d}:{(ms%60000)//1000:02d}.{(ms%1000)//100}")
        except Exception:
            pass
        self._sw_id = self._sw_display.after(100, self._sw_tick)


# ================================================================
# 19. 图片查看器
# ================================================================
class ImageViewerApp(App):
    def __init__(self, bus, theme, filepath: str = None):
        super().__init__("imageviewer", "图片查看器", 780, 560, bus, theme)
        self._fp = filepath

    def _fill(self, parent: tk.Frame):
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        self._cv = tk.Canvas(parent, bg=self._palette.get("bg"), highlightthickness=0,
                              takefocus=0, highlightbackground=self._palette.get("bg"),
                              highlightcolor=self._palette.get("bg"))
        self._cv.grid(row=0, column=0, sticky="nsew")

        tb = tk.Frame(parent, bg=self._palette.get("bg_toolbar"))
        tb.grid(row=1, column=0, sticky="ew")
        tk.Button(tb, text="打开图片", bg=self._palette.get("accent"), fg="white",
                  relief="flat", font=font(size=10), command=self._open).pack(side="left", padx=6, pady=4)

        if self._fp and os.path.isfile(self._fp):
            self._load(self._fp)
        else:
            self._cv.create_text(390, 280, text="打开一张图片", fill=self._palette.get("fg_dim"),
                                  font=font(size=13))

    def _open(self):
        path = filedialog.askopenfilename(
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp")])
        if path: self._load(path)

    def _load(self, path):
        try:
            img = Image.open(path)
            cw = self._cv.winfo_width() or 760
            ch = self._cv.winfo_height() or 520
            iw, ih = img.size
            s = min((cw - 20) / iw, (ch - 20) / ih, 1.0)
            if s < 1:
                img = img.resize((int(iw * s), int(ih * s)), Image.LANCZOS)
            self._photo = ImageTk.PhotoImage(img)
            self._cv.delete("all")
            self._cv.create_image(cw // 2, ch // 2, image=self._photo, anchor="center")
            self._bus.emit("window:title", "imageviewer", os.path.basename(path))
        except Exception as e:
            self._cv.delete("all")
            self._cv.create_text(cw // 2, ch // 2,
                                  text=f"无法加载:\n{e}", fill=self._palette.get("danger"),
                                  font=font(size=12))


# ================================================================
# 20. GerNet 完整网络模块 — HTTP客户端、连接池、重试、缓存、限速
# ================================================================
class GerNet:
    """内建网络引擎：连接复用、自动重试、磁盘缓存、UA轮换、Cookie管理。"""
    _instance_lock = threading.Lock()
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_engine()
        return cls._instance

    def _init_engine(self):
        self._cache_dir = os.path.join(app_dir(), ".netcache")
        os.makedirs(self._cache_dir, exist_ok=True)
        self._memory_cache: OrderedDict[str, tuple[float, bytes, str]] = OrderedDict()
        self._mem_cache_max = 200
        self._mem_cache_lock = threading.Lock()
        # Cookie jar
        self._cookie_jar = http.cookiejar.LWPCookieJar()
        self._cookie_lock = threading.Lock()
        # User-Agent 轮换池
        self._ua_pool = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,like Gecko) Chrome/118.0.0.0 Safari/537.36",
        ]
        self._ua_idx = 0
        self._rate_limits: dict[str, float] = {}  # domain → last_request_time
        self._rate_lock = threading.Lock()
        self._dns_cache: dict[str, str] = {}
        self._dns_ttl = 300  # 5分钟

    # ---- 请求核心 ----
    HTTP_TIMEOUT = 30
    MAX_RETRIES = 2

    def get(self, url: str, headers: dict = None, timeout: int = None,
            use_cache: bool = True, cache_ttl: int = 300,
            encoding: str = None) -> tuple[int, str]:
        """GET请求，返回(status_code, response_text)。"""
        return self._request("GET", url, headers=headers, timeout=timeout,
                            use_cache=use_cache, cache_ttl=cache_ttl, encoding=encoding)

    def post(self, url: str, data: bytes | str = None, headers: dict = None,
             timeout: int = None, use_cache: bool = False,
             encoding: str = None) -> tuple[int, str]:
        """POST请求。"""
        return self._request("POST", url, headers=headers, timeout=timeout,
                            data=data, use_cache=use_cache, encoding=encoding)

    def get_json(self, url: str, headers: dict = None, timeout: int = None,
                 use_cache: bool = True, cache_ttl: int = 300) -> tuple[int, dict | list]:
        """GET返回JSON。"""
        code, text = self.get(url, headers=headers, timeout=timeout,
                             use_cache=use_cache, cache_ttl=cache_ttl)
        try:
            return code, json.loads(text) if text else {}
        except json.JSONDecodeError:
            return code, {}

    def download(self, url: str, dest: str, headers: dict = None,
                 progress_cb: Callable = None, timeout: int = 120) -> tuple[bool, str]:
        """下载文件到dest，支持进度回调 progress_cb(downloaded, total)。"""
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            hdrs = self._build_headers(headers)
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb and total > 0:
                            progress_cb(downloaded, total)
            return True, ""
        except Exception as e:
            return False, str(e)

    def fetch_binary(self, url: str, headers: dict = None,
                     timeout: int = 60) -> tuple[int, bytes]:
        """下载二进制数据。"""
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            hdrs = self._build_headers(headers)
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.status, resp.read()
        except Exception as e:
            return -1, b""

    def _request(self, method: str, url: str, headers: dict = None,
                 timeout: int = None, data: bytes | str = None,
                 use_cache: bool = True, cache_ttl: int = 300,
                 encoding: str = None) -> tuple[int, str]:
        timeout = timeout or self.HTTP_TIMEOUT
        cache_key = self._cache_key(url, data) if use_cache else None

        # 检查内存缓存
        if cache_key:
            with self._mem_cache_lock:
                if cache_key in self._memory_cache:
                    ts, raw, charset = self._memory_cache[cache_key]
                    if time.time() - ts < cache_ttl:
                        return 200, raw.decode(charset, errors="replace") if isinstance(raw, bytes) else raw
                    del self._memory_cache[cache_key]

        # 检查磁盘缓存
        if cache_key:
            disk_result = self._disk_cache_get(cache_key, cache_ttl)
            if disk_result is not None:
                return 200, disk_result

        # 限速
        domain = urllib.parse.urlparse(url).netloc
        self._rate_limit_wait(domain)

        # 发送请求 + 重试
        last_err = ""
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                hdrs = self._build_headers(headers)

                if isinstance(data, str):
                    data = data.encode("utf-8")
                    hdrs["Content-Type"] = hdrs.get("Content-Type", "application/json; charset=utf-8")

                req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                    raw = resp.read()
                    content_type = resp.headers.get("Content-Type", "")
                    charset = "utf-8"
                    if "charset=" in content_type:
                        charset = content_type.split("charset=")[-1].split(";")[0].strip()
                    if encoding:
                        charset = encoding
                    # 处理gzip
                    if resp.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    try:
                        text = raw.decode(charset)
                    except Exception:
                        text = raw.decode("utf-8", errors="replace")

                    # 缓存
                    if cache_key and resp.status == 200:
                        with self._mem_cache_lock:
                            self._memory_cache[cache_key] = (time.time(), raw, charset)
                            while len(self._memory_cache) > self._mem_cache_max:
                                self._memory_cache.popitem(last=False)
                        self._disk_cache_set(cache_key, text)

                    return resp.status, text

            except (urllib.error.URLError, socket.timeout, http.client.HTTPException,
                    ssl.SSLError, ConnectionError, TimeoutError, OSError) as e:
                last_err = f"{type(e).__name__}: {e}"
                print(f"[GerNet] 网络错误 (尝试{attempt+1}/{self.MAX_RETRIES+1}) {url[:80]}: {last_err}")
                if attempt < self.MAX_RETRIES:
                    time.sleep(0.5 * (attempt + 1))
                    continue

        return -1, last_err

    # ---- 缓存 ----
    def _cache_key(self, url: str, data=None) -> str:
        raw = url + (data.decode("utf-8") if isinstance(data, bytes) else str(data or ""))
        return hashlib.md5(raw.encode()).hexdigest()

    def _disk_cache_get(self, key: str, ttl: int) -> str | None:
        fp = os.path.join(self._cache_dir, key + ".json")
        if not os.path.exists(fp):
            return None
        try:
            with open(fp, "r", encoding="utf-8") as f:
                entry = json.load(f)
            if time.time() - entry.get("ts", 0) < ttl:
                return entry.get("data", "")
        except Exception:
            pass
        return None

    def _disk_cache_set(self, key: str, text: str):
        try:
            fp = os.path.join(self._cache_dir, key + ".json")
            with open(fp, "w", encoding="utf-8") as f:
                json.dump({"ts": time.time(), "data": text}, f)
        except Exception:
            pass

    # ---- 限速 ----
    def _rate_limit_wait(self, domain: str, min_interval: float = 0.3):
        with self._rate_lock:
            now = time.time()
            last = self._rate_limits.get(domain, 0)
            wait = min_interval - (now - last)
            if wait > 0:
                time.sleep(wait)
            self._rate_limits[domain] = time.time()

    # ---- 辅助 ----
    def _build_headers(self, extra: dict = None) -> dict:
        base = {
            "User-Agent": self._next_ua(),
            "Accept": "text/html,application/json,application/xhtml+xml,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
        }
        if extra:
            base.update(extra)
        return base

    def _next_ua(self) -> str:
        ua = self._ua_pool[self._ua_idx % len(self._ua_pool)]
        self._ua_idx += 1
        return ua

    def clear_cache(self):
        """清除所有缓存。"""
        with self._mem_cache_lock:
            self._memory_cache.clear()
        try:
            for f in os.listdir(self._cache_dir):
                os.remove(os.path.join(self._cache_dir, f))
        except Exception:
            pass

    def get_cache_dir(self) -> str:
        return self._cache_dir

    def random_ua(self) -> str:
        return random.choice(self._ua_pool)


# ================================================================
# 21. MusicFree 原生插件系统（完整协议实现）
# ================================================================
# 媒体类型定义 (对应 MusicFree IMusicItem / IAlbumItem / IArtistItem)
# IMusicItem: {id, title?, artist?, artistId?, album?, albumId?, cover?, url?, 
#              duration?, platform?, lrc?, artists: [{name, id}]?, albumItem?: {...}}
# ISearchResult: {isEnd: bool, data: [...]}
# ILyricSource: {rawLrc?: str, translation?: str}

class MusicFreePlugin:
    """遵循 MusicFree 插件协议的插件实例。
    支持两种格式：
    - JSON声明式（本系统原生）：methods字段定义API端点
    - JS脚本式（MusicFree原生）：通过Node.js子进程执行，自动桥接
    """
    def __init__(self, data: dict, js_path: str = None):
        self.platform = data.get("platform", "未知")
        self.version = data.get("version", "0.0.0")
        self.author = data.get("author", "")
        self.home = data.get("home", "")
        self.src_url = data.get("srcUrl") or data.get("src_url", "")
        self.primary_key = data.get("primaryKey") or data.get("primary_key", ["id"])
        self.cache_control = data.get("cacheControl") or data.get("cache_control", "no-cache")
        self.supported_search_type = data.get("supportedSearchType") or data.get("supported_search_type",
            ["music", "album", "artist", "sheet", "lyric"])
        self.enabled = True
        self._config = data
        self._js_path = js_path  # 如果是JS插件，存储路径
        self._is_js = bool(js_path and js_path.endswith(".js"))
        self._net = GerNet()
        # JS 插件的能力标记（避免调用不存在的方法）
        self._has_search = data.get("_hasSearch", not self._is_js)
        self._has_get_media_source = data.get("_hasGetMediaSource", not self._is_js)
        self._has_get_lyric = data.get("_hasGetLyric", not self._is_js)
        self._has_get_top_lists = data.get("_hasGetTopLists", not self._is_js)

    def _call_js_function(self, func_name: str, args: list, timeout: int = 30) -> dict | None:
        """通过 Node.js 子进程调用 JS 插件的指定函数。
        返回 JSON 解析后的结果字典，失败返回 None。"""
        if not self._js_path or not os.path.isfile(self._js_path):
            print(f"[Plugin:{self.platform}] JS文件不存在: {self._js_path}")
            return None

        node = MusicFreePluginManager._find_node()
        if not node:
            print(f"[Plugin:{self.platform}] Node.js 不可用，无法执行 JS 插件")
            return None

        sandbox = MusicFreePluginManager._MUSICFREE_SANDBOX
        script = sandbox + r"""
        (async () => {
            try {
                const mod = require(process.argv[2]);
                const plugin = mod.default || mod;
                const fn = process.argv[3];
                if (typeof plugin[fn] !== 'function') {
                    console.log(JSON.stringify({ok: false, error: 'plugin.' + fn + ' is not a function'}));
                    return;
                }
                const args = JSON.parse(process.argv[4]);
                const result = await plugin[fn](...args);
                console.log('__PSYSTEM_RESULT__' + JSON.stringify({ok: true, data: result}));
            } catch(e) {
                console.log('__PSYSTEM_RESULT__' + JSON.stringify({ok: false, error: e.message || String(e), stack: e.stack ? String(e.stack) : undefined}));
            }
        })();
        """
        try:
            args_json = json.dumps(args, ensure_ascii=False)
            node_modules = NodeEnv.get_node_modules_path()
            paths = [os.path.dirname(self._js_path)]
            if node_modules and os.path.isdir(node_modules):
                paths.append(node_modules)

            # 写临时JS文件避免命令行长度超限
            import tempfile as _tmpf
            _tmp_path = None
            try:
                with _tmpf.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8') as _f:
                    _f.write(script)
                    _tmp_path = _f.name
                result = subprocess.run(
                    [node, _tmp_path, self._js_path, func_name, args_json],
                    capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                    cwd=os.path.dirname(self._js_path),
                    env={**os.environ, "NODE_PATH": os.pathsep.join(paths)}
                )
            finally:
                if _tmp_path and os.path.isfile(_tmp_path):
                    try: os.unlink(_tmp_path)
                    except OSError: pass
            raw = (result.stdout or "").strip()
            if not raw:
                stderr_tail = (result.stderr or "")[:200]
                if stderr_tail:
                    print(f"[Plugin:{self.platform}] JS执行失败(无输出): {stderr_tail}")
                return None
            # 查找 __PSYSTEM_RESULT__ 标记行（兼容插件自身输出污染stdout的情况）
            marker = "__PSYSTEM_RESULT__"
            resp = None
            for line in reversed(raw.split("\n")):
                line = line.strip()
                if line.startswith(marker):
                    try:
                        resp = json.loads(line[len(marker):])
                    except json.JSONDecodeError:
                        continue
                    break
            if resp is None:
                # 回退：尝试取第一行作为结果
                first_line = raw.split("\n")[0].strip()
                try:
                    resp = json.loads(first_line)
                except json.JSONDecodeError:
                    print(f"[Plugin:{self.platform}] JS输出无法解析: {first_line[:200]}")
                    return None
            if resp.get("ok"):
                return resp.get("data")
            else:
                print(f"[Plugin:{self.platform}] JS执行错误: {resp.get('error', '未知')}")
                return None
        except subprocess.TimeoutExpired:
            print(f"[Plugin:{self.platform}] JS执行超时({timeout}s)")
            return None
        except Exception as e:
            print(f"[Plugin:{self.platform}] JS执行异常: {e}")
            return None

    @property
    def base_url(self) -> str:
        return self._config.get("base_url", "")

    def get_headers(self) -> dict:
        return self._config.get("headers", {
            "User-Agent": self._net.random_ua(),
            "Referer": self.home or "",
        })

    # ===== 核心方法：search (对应 MusicFree search) =====
    def search(self, keyword: str, page: int = 1, search_type: str = "music",
               limit: int = 30) -> dict:
        """返回 {isEnd: bool, data: [IMusicItem]}"""
        if self._is_js:
            if not self._has_search:
                return {"isEnd": True, "data": []}
            result = self._call_js_function("search", [keyword, page, search_type])
            if result:
                return result if isinstance(result, dict) else {"isEnd": True, "data": result or []}
            return {"isEnd": True, "data": []}

        method = self._config.get("methods", {}).get("search")
        if not method:
            return {"isEnd": True, "data": []}
        url = method.get("url", "")
        req_method = method.get("method", "GET").upper()
        result_path = method.get("result_path", "")
        extra_params = dict(method.get("params", {}))
        body = method.get("body")
        headers = dict(method.get("headers", {}))

        url = self._fill_url(url, keyword=keyword, page=page, limit=limit,
                            offset=(page - 1) * limit, type=search_type)
        # 如果 body 是字典模板（用于 POST 请求），填充占位符
        if isinstance(body, dict):
            body = self._fill_body(body, keyword=keyword, page=page, limit=limit,
                                   offset=(page - 1) * limit, type=search_type)

        try:
            result = self._do_request(url, req_method, extra_params, body, headers, result_path)
            items = self._normalize_items(result, result_path)
            return {"isEnd": len(items) < limit, "data": items}
        except Exception as e:
            print(f"[Plugin:{self.platform}] 搜索失败: {e}")
            return {"isEnd": True, "data": []}

    # ===== 核心方法：getMediaSource (对应 MusicFree getMediaSource) =====
    def get_media_source(self, song_item: dict, quality: str = "standard") -> dict | None:
        """返回 {url: str, headers?: {}} 或 None"""
        if self._is_js:
            if not self._has_get_media_source:
                return None
            result = self._call_js_function("getMediaSource", [song_item, quality])
            if isinstance(result, dict) and result.get("url"):
                return result
            elif isinstance(result, str) and result:
                return {"url": result}
            return None

        method = self._config.get("methods", {}).get("getMediaSource") or \
                 self._config.get("methods", {}).get("get_music_url")
        if not method:
            return None
        url_tpl = method.get("url", "")
        result_path = method.get("result_path", "")
        # @url 模式：直接返回填充后的URL，不发起网络请求（用于重定向URL等场景）
        if result_path == "@url":
            # 过滤掉可能和 _fill_url 参数冲突的字段名（如 url）
            _safe_kwargs = {k: str(v) for k, v in song_item.items()
                            if not k.startswith("_") and k not in ("url",)}
            url = self._fill_url(url_tpl, **_safe_kwargs)
            url = url.replace("{quality}", quality)
            return {"url": url}
        body = method.get("body")
        extra_params = dict(method.get("params", {}))
        headers = dict(method.get("headers", {}))

        # 过滤掉可能和 _fill_url 参数冲突的字段名（如 url）
        _safe_kwargs = {k: str(v) for k, v in song_item.items()
                        if not k.startswith("_") and k not in ("url",)}
        url = self._fill_url(url_tpl, **_safe_kwargs)
        url = url.replace("{quality}", quality)

        try:
            result = self._do_request(url, method.get("method", "GET").upper(),
                                     extra_params, body, headers, result_path)
            result_url = ""
            result_headers = {}
            if isinstance(result, str):
                result_url = result
            elif isinstance(result, list) and result:
                r0 = result[0]
                result_url = r0 if isinstance(r0, str) else r0.get("url", "")
                if isinstance(r0, dict):
                    result_headers = r0.get("headers", {})
            elif isinstance(result, dict):
                result_url = result.get("url", "") or result.get("sourceUrl", "") or ""
                result_headers = result.get("headers", {})
            if not result_url:
                return None
            # url_prefix：如果返回的不是完整URL（如QQ音乐只返回purl），自动补全前缀
            url_prefix = method.get("url_prefix", "")
            if url_prefix and not result_url.startswith(("http://", "https://")):
                result_url = url_prefix + result_url
            return {"url": result_url, "headers": result_headers}
        except Exception as e:
            print(f"[Plugin:{self.platform}] 获取音源失败: {e}")
            return None

    # 兼容旧接口
    def get_music_url(self, song_item: dict) -> str:
        source = self.get_media_source(song_item)
        return source.get("url", "") if source else ""

    # ===== 核心方法：getLyric (对应 MusicFree getLyric) =====
    def get_lyric(self, song_item: dict) -> dict | None:
        """返回 {rawLrc?: str, translation?: str} 或 None"""
        if self._is_js:
            if not self._has_get_lyric:
                return None
            result = self._call_js_function("getLyric", [song_item])
            if isinstance(result, dict):
                return result
            if isinstance(result, str) and result:
                return {"rawLrc": result}
            return None

        method = self._config.get("methods", {}).get("getLyric") or \
                 self._config.get("methods", {}).get("get_lyric")
        if not method:
            return None
        url_tpl = method.get("url", "")
        result_path = method.get("result_path", "")
        body = method.get("body")
        extra_params = dict(method.get("params", {}))
        headers = dict(method.get("headers", {}))

        # 过滤掉可能和 _fill_url 参数冲突的字段名（如 url）
        _safe_kwargs = {k: str(v) for k, v in song_item.items()
                        if not k.startswith("_") and k not in ("url",)}
        url = self._fill_url(url_tpl, **_safe_kwargs)

        try:
            result = self._do_request(url, method.get("method", "GET").upper(),
                                     extra_params, body, headers, result_path)
            if isinstance(result, str):
                return {"rawLrc": result}
            if isinstance(result, list) and result:
                r = result[0]
                if isinstance(r, str):
                    return {"rawLrc": r}
                if isinstance(r, dict):
                    lrc = r.get("lyric") or r.get("lrc") or r.get("rawLrc") or ""
                    trans = r.get("translation") or r.get("tlyric") or ""
                    return {"rawLrc": lrc, "translation": trans} if lrc else None
            if isinstance(result, dict):
                lrc = result.get("lyric") or result.get("lrc") or result.get("rawLrc") or ""
                trans = result.get("translation") or result.get("tlyric") or ""
                return {"rawLrc": lrc, "translation": trans} if lrc else None
        except Exception as e:
            print(f"[Plugin:{self.platform}] 获取歌词失败: {e}")
        return None

    # ===== 扩展方法：getTopLists (对应 MusicFree getTopLists) =====
    def get_top_lists(self) -> list:
        """返回 [{title, data: [IMusicSheetItem]}] """
        if self._is_js:
            if not self._has_get_top_lists:
                return []
            result = self._call_js_function("getTopLists", [])
            if isinstance(result, list):
                return result
            return []

        method = self._config.get("methods", {}).get("getTopLists") or \
                 self._config.get("methods", {}).get("hot_list")
        if not method:
            return []
        url = method.get("url", "")
        result_path = method.get("result_path", "")
        body = method.get("body")
        extra_params = dict(method.get("params", {}))
        headers = dict(method.get("headers", {}))

        try:
            result = self._do_request(url, method.get("method", "GET").upper(),
                                     extra_params, body, headers, result_path)
            if isinstance(result, list):
                # 如果是分组格式 [{title, data}]
                if result and isinstance(result[0], dict) and "data" in result[0]:
                    return result
                # 否则包装成一组
                return [{"title": "热门推荐", "data": result}]
            return []
        except Exception as e:
            print(f"[Plugin:{self.platform}] 获取榜单失败: {e}")
            return []

    def get_hot_list(self) -> list:
        """兼容旧接口，返回扁平列表。"""
        top = self.get_top_lists()
        items = []
        for group in top:
            items.extend(group.get("data", []))
        return items

    def get_top_list_detail(self, sheet_item: dict, page: int = 1) -> dict:
        """获取歌单/榜单内的歌曲列表。
        返回 {isEnd: bool, musicList: [IMusicItem], sheetItem?: {}}
        支持 JS 插件 getTopListDetail 和 JSON 插件 getAlbumInfo/getMusicSheetInfo。
        """
        # JS 插件：调用 getTopListDetail
        if self._is_js:
            result = self._call_js_function("getTopListDetail", [sheet_item, page])
            if isinstance(result, dict):
                if "musicList" in result:
                    return {"isEnd": bool(result.get("isEnd", True)),
                            "musicList": result.get("musicList", []),
                            "sheetItem": sheet_item if page <= 1 else None}
                if "data" in result:
                    return {"isEnd": True, "musicList": result["data"]}
            if isinstance(result, list):
                return {"isEnd": True, "musicList": result}
            result = self._call_js_function("getMusicSheetInfo", [sheet_item, page])
            if isinstance(result, dict) and "musicList" in result:
                return {"isEnd": bool(result.get("isEnd", True)),
                        "musicList": result.get("musicList", []),
                        "sheetItem": sheet_item if page <= 1 else None}
            if isinstance(result, list):
                return {"isEnd": True, "musicList": result}
            return {"isEnd": True, "musicList": []}

        # JSON 声明式插件
        for method_key in ("getSheetDetail", "getAlbumInfo", "getTopListDetail",
                           "getMusicSheetInfo", "getPlaylistDetail"):
            method = self._config.get("methods", {}).get(method_key)
            if not method:
                continue
            url_tpl = method.get("url", "")
            result_path = method.get("result_path", "")
            body = method.get("body")
            extra_params = dict(method.get("params", {}))
            headers = dict(method.get("headers", {}))
            url = self._fill_url(url_tpl, album_id=str(sheet_item.get("id", "")), page=page)
            try:
                result = self._do_request(url, method.get("method", "GET").upper(),
                                         extra_params, body, headers, result_path)
                items = self._normalize_items(result, result_path)
                return {"isEnd": len(items) < 30, "musicList": items,
                        "sheetItem": sheet_item if page <= 1 else None}
            except Exception as e:
                print(f"[Plugin:{self.platform}] {method_key} 失败: {e}")
                continue
        return {"isEnd": True, "musicList": []}

    # ===== 扩展方法：getRecommendSheetTags =====
    def get_recommend_tags(self) -> list:
        """返回 [{id, title}] """
        method = self._config.get("methods", {}).get("getRecommendSheetTags") or \
                 self._config.get("methods", {}).get("recommend_tags")
        if not method:
            return []
        url = method.get("url", "")
        result_path = method.get("result_path", "")
        body = method.get("body")
        extra_params = dict(method.get("params", {}))
        headers = dict(method.get("headers", {}))

        try:
            result = self._do_request(url, method.get("method", "GET").upper(),
                                     extra_params, body, headers, result_path)
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            print(f"[Plugin:{self.platform}] 获取推荐标签失败: {e}")
            return []

    # ===== 扩展方法：getRecommendSheetsByTag =====
    def get_sheets_by_tag(self, tag: dict, page: int = 1) -> dict:
        """返回 {isEnd: bool, data: [IMusicSheetItem]}"""
        method = self._config.get("methods", {}).get("getRecommendSheetsByTag")
        if not method:
            return {"isEnd": True, "data": []}
        url_tpl = method.get("url", "")
        result_path = method.get("result_path", "")
        body = method.get("body")
        extra_params = dict(method.get("params", {}))
        headers = dict(method.get("headers", {}))

        url = url_tpl.replace("{tag_id}", str(tag.get("id", "")))
        url = url.replace("{tag_title}", urllib.parse.quote(str(tag.get("title", ""))))
        url = url.replace("{page}", str(page))

        try:
            result = self._do_request(url, method.get("method", "GET").upper(),
                                     extra_params, body, headers, result_path)
            items = self._normalize_items(result, result_path)
            return {"isEnd": len(items) < 20, "data": items}
        except Exception as e:
            print(f"[Plugin:{self.platform}] 获取标签歌单失败: {e}")
            return {"isEnd": True, "data": []}

    # ===== 扩展方法：getAlbumInfo / getMusicSheetInfo =====
    def get_album_info(self, album_item: dict, page: int = 1) -> dict:
        """返回 {isEnd?: bool, musicList: [IMusicItem], albumItem?: {}}"""
        method = self._config.get("methods", {}).get("getAlbumInfo")
        if not method:
            return {"isEnd": True, "musicList": []}
        url_tpl = method.get("url", "")
        result_path = method.get("result_path", "")
        body = method.get("body")
        extra_params = dict(method.get("params", {}))
        headers = dict(method.get("headers", {}))

        url = self._fill_url(url_tpl, album_id=str(album_item.get("id", "")), page=page)
        try:
            result = self._do_request(url, method.get("method", "GET").upper(),
                                     extra_params, body, headers, result_path)
            items = self._normalize_items(result, result_path)
            return {"isEnd": len(items) < 30, "musicList": items,
                    "albumItem": album_item if page <= 1 else None}
        except Exception as e:
            print(f"[Plugin:{self.platform}] 获取专辑详情失败: {e}")
            return {"isEnd": True, "musicList": []}

    # ===== 内部工具 =====
    # URL 模板变量别名：当模板用 {mid} 但数据只有 {songmid} 时自动匹配
    _FIELD_ALIASES = {
        "mid": ["songmid", "id", "albummid"],
        "id": ["songid", "mid", "songmid"],
        "songid": ["id", "mid"],
        "songmid": ["mid", "songid"],
        "albumid": ["id", "albummid"],
        "albummid": ["albumid", "mid"],
    }

    def _fill_url(self, url: str, **kwargs) -> str:
        """替换URL模板变量（含字段别名）。"""
        for key, val in kwargs.items():
            url = url.replace("{" + key + "}", urllib.parse.quote(str(val)) if not str(val).startswith("http") else str(val))
        # 检查残留的未替换占位符，尝试别名
        import re as _re
        missing = _re.findall(r'\{(\w+)\}', url)
        for token in set(missing):
            if token in self._FIELD_ALIASES:
                for alias in self._FIELD_ALIASES[token]:
                    if alias in kwargs:
                        val = kwargs[alias]
                        url = url.replace("{" + token + "}", urllib.parse.quote(str(val)) if not str(val).startswith("http") else str(val))
                        break
        return url if url.startswith("http") else self.base_url + url

    @staticmethod
    def _fill_body(body: dict, **kwargs) -> dict:
        """填充 POST body 模板中的占位符（支持别名）。"""
        body_str = json.dumps(body, ensure_ascii=False)
        for key, val in kwargs.items():
            body_str = body_str.replace("{" + key + "}", str(val))
        # 别名
        import re as _re
        missing = _re.findall(r'\{(\w+)\}', body_str)
        for token in set(missing):
            if token in MusicFreePlugin._FIELD_ALIASES:
                for alias in MusicFreePlugin._FIELD_ALIASES[token]:
                    if alias in kwargs:
                        body_str = body_str.replace("{" + token + "}", str(kwargs[alias]))
                        break
        return json.loads(body_str)

    def _do_request(self, url: str, method: str, params: dict,
                    body, extra_headers: dict, result_path: str):
        """统一请求处理。"""
        full_headers = self.get_headers()
        full_headers.update(extra_headers)

        if method == "GET":
            query = urllib.parse.urlencode(params) if params else ""
            if query:
                url = url + ("&" if "?" in url else "?") + query
            code, text = self._net.get(url, headers=full_headers)
        elif method == "POST":
            if body is not None:
                if isinstance(body, dict):
                    post_data = json.dumps(body)
                    full_headers["Content-Type"] = "application/json"
                else:
                    post_data = str(body)
                code, text = self._net.post(url, data=post_data, headers=full_headers)
            elif params:
                post_data = urllib.parse.urlencode(params)
                full_headers["Content-Type"] = "application/x-www-form-urlencoded"
                code, text = self._net.post(url, data=post_data, headers=full_headers)
            else:
                code, text = self._net.post(url, headers=full_headers)
        else:
            code, text = self._net.get(url, headers=full_headers)

        if code == -1:
            raise ConnectionError(text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = text
        return self._extract_path(data, result_path)

    @staticmethod
    def _extract_path(data, path: str):
        """从JSON数据中按路径提取。"""
        if not path:
            return data
        keys = path.split(".")
        current = data
        for k in keys:
            if isinstance(current, dict):
                current = current.get(k)
            elif isinstance(current, list) and k.isdigit():
                current = current[int(k)]
            else:
                return []
            if current is None:
                return []
        return current if isinstance(current, (list, dict, str)) else [current] if current else []

    @staticmethod
    def _normalize_items(result, path: str) -> list[dict]:
        """将提取结果标准化为 IMusicItem 列表。
        注意：result 已经由 _do_request 通过 result_path 提取过，此处不再重复提取。"""
        items = result  # _do_request 已提取，直接使用
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return []
        normalized = []
        for item in items:
            if isinstance(item, dict):
                # 标准化字段名
                norm = dict(item)
                # 确保有 id 字段
                if "id" not in norm:
                    for k in ("songid", "songId", "musicid", "rid", "sid", "mid"):
                        if k in norm:
                            norm["id"] = str(norm[k])
                            break
                if "id" not in norm:
                    norm["id"] = hashlib.md5(json.dumps(item, sort_keys=True).encode()).hexdigest()[:12]
                # 标准化 title/name
                if "title" not in norm:
                    norm["title"] = norm.get("name") or norm.get("songname") or norm.get("songName") or "未知"
                # 标准化 artist
                if "artist" not in norm:
                    artists = norm.get("ar") or norm.get("artists") or norm.get("singer") or []
                    if isinstance(artists, list) and artists:
                        norm["artist"] = "/".join(
                            a.get("name", str(a)) if isinstance(a, dict) else str(a)
                            for a in artists[:3])
                    elif isinstance(artists, str):
                        norm["artist"] = artists
                    else:
                        norm["artist"] = norm.get("author", "未知")
                normalized.append(norm)
            elif isinstance(item, str):
                normalized.append({"id": item, "title": item, "artist": ""})
        return normalized


class MusicFreePluginManager:
    """插件管理器：加载/安装/卸载/市场。"""
    PLUGIN_DIR = os.path.join(app_dir(), "plugins")
    # MusicFree 官方插件市场
    MARKET_URL = "https://raw.githubusercontent.com/maotoumao/MusicFreePlugins/master/plugins.json"
    MARKET_MIRROR = "https://gitee.com/maotoumao/MusicFreePlugins/raw/master/plugins.json"
    MARKET_CDN = "https://cdn.jsdelivr.net/gh/maotoumao/MusicFreePlugins@master/plugins.json"

    # ================================================================
    #  万能音乐插件沙箱环境 V3 —— 终极限定·完全不慌版
    #
    #  覆盖范围：
    #    ▸ 浏览器Web API: fetch/Headers/Request/Response/FormData/Blob/File/FileReader/
    #                      WebSocket/EventTarget/Event/CustomEvent/AbortController/
    #                      setTimeout/setInterval/requestAnimationFrame/queueMicrotask/
    #                      atob/btoa/TextEncoder/TextDecoder/navigator/location/
    #                      structuredClone/performance/console
    #    ▸ Node内置模块: crypto/buffer/url/util/fs/path/os/stream/events/zlib/http/https/
    #                     net/tls/dns/querystring/timers/readline/string_decoder
    #    ▸ NPM包: axios/cheerio/crypto-js/dayjs/he/iconv-lite/webdav/fast-xml-parser/
    #              fast-xml-builder/xml2js/sax/md5/base-64/entities/url-parse/url-join/
    #              undici/nested-property/form-data/fetch-blob/https-proxy-agent/strnum
    #    ▸ MusicFree env: getUserVariables/Cookie管理/localStorage/sessionStorage/
    #                      代理/网络请求/文件下载/时间/加密/编解码/XML-JSON/cheerio/
    #                      压缩解压/WebDAV/持久化/用户变量
    #    ▸ 加密全套: MD5/SHA1/SHA256/SHA384/SHA512/HMAC-SHA1/HMAC-SHA256/HMAC-SHA512/
    #                 AES-ECB/AES-CBC/AES-GCM 加解密/RSA签名验签/RIPEMD160
    #    ▸ 编解码: Base64/Base64URL/Hex/UTF8/GBK/BIG5/Shift_JIS/EUC-KR/拉丁系
    #    ▸ 流处理: Readable/Writable/Transform/Duplex/pipeline/promisify
    #    ▸ 事件: EventEmitter
    #    ▸ 其他: uuid/sleep/mimeType检测/protobuf预留位置/
    #             Promise.allSettled/any/finally 垫片
    # ================================================================
    _MUSICFREE_SANDBOX = r"""
// ======== MusicFree 万能沙箱 V3 · 终极限定版 ========
// 任何未来插件——浏览器API、Node原生、npm生态、加解密、
// 流处理、WebSocket、编解码——全已就位，一个都不需要补。

(function initUltimateSandbox() {
    'use strict';

    // ============================================================
    //  区块 1：Node.js 内置模块 → 全局化
    // ============================================================

    // 1a. crypto — Web Crypto + Node Crypto 双轨
    try {
        var _crypto = require('crypto');
        globalThis.crypto = {
            getRandomValues: function(arr) {
                var buf = _crypto.randomBytes(arr.length);
                for (var i = 0; i < arr.length; i++) arr[i] = buf[i];
                return arr;
            },
            randomUUID: function() { return _crypto.randomUUID(); },
            subtle: {
                digest: function(algo, buf) {
                    return Promise.resolve(_crypto.createHash(algo.name ? algo.name.replace(/[^A-Za-z0-9]/g,'').toLowerCase() : algo).update(Buffer.from(buf)).digest());
                },
            },
        };
        globalThis.CryptoJS = require('crypto-js');
    } catch(e) {}
    // 内置 crypto 对象别名（供 require('crypto') 之外直接用）
    try { globalThis._nativeCrypto = require('crypto'); } catch(e) {}

    // 1b. Buffer — 已在 Node 原生 global，确保就位
    try { if (!globalThis.Buffer) globalThis.Buffer = require('buffer').Buffer; } catch(e) {}
    // Buffer 别名系列
    globalThis.Buffer_from = function(s, enc) { return Buffer.from(s, enc); };
    globalThis.Buffer_alloc = function(sz) { return Buffer.alloc(sz); };
    globalThis.Buffer_concat = function(arr) { return Buffer.concat(arr); };

    // 1c. URL / URLSearchParams / querystring
    try {
        globalThis.URL = require('url').URL;
        globalThis.URLSearchParams = require('url').URLSearchParams;
        globalThis.URL_canParse = function(u, base) {
            try { new URL(u, base); return true; } catch(e) { return false; }
        };
        globalThis._querystring = require('querystring');
    } catch(e) {}

    // 1d. TextEncoder / TextDecoder
    try {
        var _util = require('util');
        globalThis.TextEncoder = _util.TextEncoder;
        globalThis.TextDecoder = _util.TextDecoder;
    } catch(e) {}
    try { globalThis.atob = function(s) { return Buffer.from(String(s), 'base64').toString('binary'); }; } catch(e) {}
    try { globalThis.btoa = function(s) { return Buffer.from(String(s), 'binary').toString('base64'); }; } catch(e) {}

    // 1e. 流 (Stream) — 预加载所有流类型
    try {
        var _stream = require('stream');
        globalThis.ReadableStream = _stream.Readable;
        globalThis.WritableStream = _stream.Writable;
        globalThis.TransformStream = _stream.Transform;
        globalThis.DuplexStream = _stream.Duplex;
        globalThis.pipeline = require('util').promisify(_stream.pipeline);
        globalThis.promisify = require('util').promisify;
    } catch(e) {}

    // 1f. events — EventEmitter
    try {
        var _events = require('events');
        globalThis.EventEmitter = _events.EventEmitter;
        globalThis._events = _events;
    } catch(e) {}

    // 1g. 网络 — http/https/net/tls/dns
    try {
        globalThis._http = require('http');
        globalThis._https = require('https');
        globalThis._net = require('net');
        globalThis._tls = require('tls');
        globalThis._dns = require('dns');
    } catch(e) {}

    // 1h. zlib — 全部压缩算法
    try {
        var _zlib = require('zlib');
        globalThis._zlib = _zlib;
        globalThis._brotli = require('zlib').brotliCompress ? require('zlib') : null;
    } catch(e) {}

    // 1i. 其它常用 Node 模块
    try { globalThis._readline = require('readline'); } catch(e) {}
    try { globalThis._stringDecoder = require('string_decoder'); } catch(e) {}
    try { globalThis._childProcess_execFileSync = null; /* 安全限制不开 child_process */ } catch(e) {}
    try { globalThis._assert = require('assert'); } catch(e) {}

    // 1j. util 便捷方法
    try {
        globalThis._util = require('util');
        globalThis._inspect = function(o) { return require('util').inspect(o, { depth: 5, colors: false }); };
    } catch(e) {}


    // ============================================================
    //  区块 2：浏览器 API 全兼容
    // ============================================================

    // 2a. fetch / Headers / Request / Response — 三层回退
    try {
        var _nodeFetch = require('node-fetch');
        globalThis.fetch = _nodeFetch.default || _nodeFetch;
        globalThis.Headers = _nodeFetch.Headers;
        globalThis.Request = _nodeFetch.Request;
        globalThis.Response = _nodeFetch.Response;
    } catch(e) {
        if (!globalThis.fetch) {
            // Node 18+ 有原生 fetch，但这里确保完整性
            var _httpMod = require('http'), _httpsMod = require('https'), _urlMod = require('url');
            globalThis.fetch = function(u, opts) {
                opts = opts || {};
                return new Promise(function(resolve, reject) {
                    var purl, mod;
                    if (typeof u === 'string') { purl = new _urlMod.URL(u); mod = purl.protocol === 'https:' ? _httpsMod : _httpMod; }
                    else { purl = new _urlMod.URL(u.url || u.href || ''); mod = purl.protocol === 'https:' ? _httpsMod : _httpMod; }
                    var scheme = purl.protocol || '';
                    if (scheme !== 'http:' && scheme !== 'https:') {
                        // 非 http 协议，尝试用 axios
                        try { require('axios')({ url: u, method: opts.method || 'GET', headers: opts.headers, data: opts.body, timeout: opts.timeout || 30000, responseType: 'text' }).then(function(r) { resolve({ status: r.status, ok: r.status >= 200 && r.status < 300, statusText: r.statusText, headers: r.headers, json: function() { return Promise.resolve(typeof r.data === 'string' ? JSON.parse(r.data) : r.data); }, text: function() { return Promise.resolve(typeof r.data === 'string' ? r.data : JSON.stringify(r.data)); }, arrayBuffer: function() { return Promise.resolve(Buffer.from(typeof r.data === 'string' ? r.data : JSON.stringify(r.data))); }, blob: function() { return Promise.resolve({ type: (r.headers || {})['content-type'] || '', size: (typeof r.data === 'string' ? r.data.length : 0), arrayBuffer: function() { return Promise.resolve(Buffer.from(typeof r.data === 'string' ? r.data : '')); } }); }, }); }).catch(reject); } catch(e2) { reject(new Error('Unsupported protocol: ' + scheme)); }
                        return;
                    }
                    var reqOpts = {
                        hostname: purl.hostname, port: purl.port || (scheme === 'https:' ? 443 : 80),
                        path: purl.pathname + (purl.search || ''), method: (opts.method || 'GET').toUpperCase(),
                        headers: opts.headers || {},
                        timeout: opts.timeout || 30000,
                        rejectUnauthorized: false,
                    };
                    if (opts.signal && opts.signal.aborted) { reject(new Error('Aborted')); return; }
                    var req = mod.request(reqOpts, function(res) {
                        var chunks = [];
                        res.on('data', function(c) { chunks.push(c); });
                        res.on('end', function() {
                            var buf = Buffer.concat(chunks);
                            resolve({
                                status: res.statusCode, ok: res.statusCode >= 200 && res.statusCode < 300,
                                statusText: res.statusMessage, headers: res.headers,
                                redirected: false, url: u, type: 'default',
                                json: function() { return Promise.resolve(JSON.parse(buf.toString('utf8'))); },
                                text: function() { return Promise.resolve(buf.toString('utf8')); },
                                arrayBuffer: function() { return Promise.resolve(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength)); },
                                blob: function() { return Promise.resolve({ type: res.headers['content-type'] || '', size: buf.length, arrayBuffer: function() { return Promise.resolve(buf); }, text: function() { return Promise.resolve(buf.toString('utf8')); } }); },
                                clone: function() { return this; },
                            });
                        });
                    });
                    req.on('error', function(e) { reject(e); });
                    req.on('timeout', function() { req.destroy(); reject(new Error('timeout')); });
                    if (opts.signal) { opts.signal.addEventListener('abort', function() { req.destroy(); reject(new Error('Aborted')); }); }
                    if (opts.body) {
                        var bd = opts.body;
                        if (typeof bd === 'string') req.write(bd);
                        else if (Buffer.isBuffer(bd)) req.write(bd);
                        else req.write(JSON.stringify(bd));
                    }
                    req.end();
                });
            };
        }
        if (!globalThis.Headers) {
            globalThis.Headers = function(h) {
                var self = this;
                this._headers = {};
                if (h) Object.keys(h).forEach(function(k) { self._headers[k.toLowerCase()] = h[k]; });
                this.get = function(k) { return self._headers[k.toLowerCase()] || null; };
                this.set = function(k, v) { self._headers[k.toLowerCase()] = v; };
                this.has = function(k) { return self._headers.hasOwnProperty(k.toLowerCase()); };
                this.delete = function(k) { delete self._headers[k.toLowerCase()]; };
                this.forEach = function(fn) { Object.keys(self._headers).forEach(function(k) { fn(self._headers[k], k, self); }); };
                this.entries = function() { return Object.entries(self._headers); };
            };
        }
        if (!globalThis.Request) globalThis.Request = function(u, opts) { this.url = u; this.method = (opts || {}).method || 'GET'; this.headers = new (globalThis.Headers)((opts || {}).headers); this.body = (opts || {}).body; };
        if (!globalThis.Response) globalThis.Response = function(body, opts) { this.body = body; this.status = (opts || {}).status || 200; this.ok = this.status >= 200 && this.status < 300; this.headers = new (globalThis.Headers)((opts || {}).headers); };
    }

    // 2b. FormData / Blob / File / FileReader
    try {
        globalThis.FormData = require('form-data');
    } catch(e) {
        if (!globalThis.FormData) {
            globalThis.FormData = function() {
                this._boundary = '----FormBoundary' + Math.random().toString(36).substring(2);
                this._data = [];
                this.append = function(k, v, filename) {
                    if (v && typeof v === 'object' && v.buffer) { this._data.push({ key: k, value: v, filename: filename, type: 'file' }); }
                    else { this._data.push({ key: k, value: v, filename: filename, type: 'text' }); }
                };
                this.getHeaders = function() { return { 'Content-Type': 'multipart/form-data; boundary=' + this._boundary }; };
            };
        }
    }
    try {
        globalThis.Blob = require('fetch-blob');
    } catch(e) {
        if (!globalThis.Blob) {
            globalThis.Blob = function(parts, opts) {
                parts = parts || [];
                this._parts = parts;
                this.type = (opts || {}).type || '';
                this.size = 0;
                for (var i = 0; i < parts.length; i++) {
                    if (typeof parts[i] === 'string') this.size += Buffer.byteLength(parts[i]);
                    else if (Buffer.isBuffer(parts[i])) this.size += parts[i].length;
                    else if (parts[i] && typeof parts[i] === 'object') this.size += (parts[i].length || parts[i].size || 0);
                }
                this.arrayBuffer = function() {
                    return Promise.resolve(Buffer.concat(parts.map(function(p) {
                        if (typeof p === 'string') return Buffer.from(p);
                        if (Buffer.isBuffer(p)) return p;
                        if (p && p.buffer) return Buffer.from(p.buffer);
                        return Buffer.from(JSON.stringify(p || ''));
                    })));
                };
                this.text = function() { return this.arrayBuffer().then(function(b) { return b.toString('utf8'); }); };
                this.stream = function() { var Readable = require('stream').Readable; var r = new Readable(); r.push(Buffer.concat(parts.map(function(p) { return typeof p === 'string' ? Buffer.from(p) : Buffer.isBuffer(p) ? p : Buffer.from(JSON.stringify(p || '')); }))); r.push(null); return r; };
                this.slice = function(start, end) { return new Blob(parts.slice(start, end), opts); };
            };
        }
    }
    try {
        globalThis.File = require('fetch-blob/file');
    } catch(e) {
        if (!globalThis.File) {
            globalThis.File = function(parts, name, opts) {
                globalThis.Blob.call(this, parts, opts);
                this.name = name || '';
                this.lastModified = (opts || {}).lastModified || Date.now();
                this.webkitRelativePath = '';
            };
            if (globalThis.Blob) globalThis.File.prototype = Object.create(globalThis.Blob.prototype);
        }
    }
    if (!globalThis.FileReader) {
        globalThis.FileReader = function() {
            var self = this;
            this.result = null; this.error = null; this.readyState = 0; this.onload = null; this.onerror = null;
            this.readAsArrayBuffer = function(blob) { self.readyState = 1; blob.arrayBuffer().then(function(b) { self.readyState = 2; self.result = b; if (self.onload) self.onload(); }).catch(function(e) { self.readyState = 2; self.error = e; if (self.onerror) self.onerror(); }); };
            this.readAsText = function(blob, enc) { self.readyState = 1; blob.text().then(function(t) { self.readyState = 2; self.result = t; if (self.onload) self.onload(); }).catch(function(e) { self.readyState = 2; self.error = e; if (self.onerror) self.onerror(); }); };
            this.readAsDataURL = function(blob) { self.readyState = 1; blob.arrayBuffer().then(function(b) { self.readyState = 2; self.result = 'data:' + (blob.type || 'application/octet-stream') + ';base64,' + Buffer.from(b).toString('base64'); if (self.onload) self.onload(); }).catch(function(e) { self.readyState = 2; self.error = e; if (self.onerror) self.onerror(); }); };
        };
    }

    // 2c. WebSocket (ws 库)
    try {
        var _WebSocket = require('ws');
        globalThis.WebSocket = _WebSocket;
        globalThis._ws = _WebSocket;
    } catch(e) {
        // 无 ws 库时提供一个基于 http 的空实现（避免报错）
        if (!globalThis.WebSocket) {
            globalThis.WebSocket = function(url, protocols) {
                this.url = url; this.protocols = protocols;
                this.readyState = 3; this.CONNECTING = 0; this.OPEN = 1; this.CLOSING = 2; this.CLOSED = 3;
                this.send = function(d) { throw new Error('WebSocket not available (install ws module)'); };
                this.close = function() {};
                this.onopen = null; this.onmessage = null; this.onclose = null; this.onerror = null;
                this.addEventListener = function() {};
            };
        }
    }

    // 2d. navigator 模拟
    var _nav = {
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 MusicFree/1.0.0',
        platform: process.platform || 'Win32',
        language: 'zh-CN',
        languages: ['zh-CN', 'zh', 'en'],
        cookieEnabled: true,
        onLine: true,
        hardwareConcurrency: (function() { try { return require('os').cpus().length; } catch(e) { return 4; } })(),
    };
    try { globalThis.navigator = _nav; } catch(e) {
        try { Object.defineProperty(globalThis, 'navigator', { value: _nav, writable: true, configurable: true }); } catch(e2) {}
    }

    // 2e. location 模拟
    var _loc = {
        href: 'https://musicfree.app',
        protocol: 'https:',
        host: 'musicfree.app',
        hostname: 'musicfree.app',
        port: '',
        pathname: '/',
        search: '',
        hash: '',
        origin: 'https://musicfree.app',
        assign: function() {}, replace: function() {}, reload: function() {},
    };
    try { globalThis.location = _loc; } catch(e) {
        try { Object.defineProperty(globalThis, 'location', { value: _loc, writable: true, configurable: true }); } catch(e2) {}
    }

    // 2f. performance 模拟
    var _perfStart = Date.now();
    var _perfObj = {
        now: function() { return Date.now() - _perfStart; },
        timeOrigin: _perfStart,
        mark: function(name) {
            try {
                var perf = require('perf_hooks').performance;
                perf.mark(name);
                if (!this._marks) this._marks = {};
                this._marks[name] = perf.now();
            } catch(e) {}
        },
        measure: function(name, start, end) {
            try {
                var perf = require('perf_hooks').performance;
                perf.measure(name, start, end);
            } catch(e) {}
        },
        _marks: {},
    };
    try { globalThis.performance = _perfObj; } catch(e) {
        try { Object.defineProperty(globalThis, 'performance', { value: _perfObj, writable: true, configurable: true }); } catch(e2) {}
    }

    // 2g. structuredClone
    if (!globalThis.structuredClone) {
        globalThis.structuredClone = function(obj) {
            if (obj === undefined || obj === null) return obj;
            try { return JSON.parse(JSON.stringify(obj)); } catch(e) { return obj; }
        };
    }

    // 2h. requestAnimationFrame
    if (!globalThis.requestAnimationFrame) { globalThis.requestAnimationFrame = function(fn) { return setTimeout(fn, 16); }; }
    if (!globalThis.cancelAnimationFrame) { globalThis.cancelAnimationFrame = function(id) { clearTimeout(id); }; }


    // ============================================================
    //  区块 3：npm 生态包全部预加载
    // ============================================================
    try { globalThis._cheerio = require('cheerio'); } catch(e) {}
    try { globalThis._iconv = require('iconv-lite'); } catch(e) {}
    try { globalThis._dayjs = require('dayjs'); } catch(e) {}
    try { globalThis._he = require('he'); } catch(e) {}
    try { globalThis._fastXmlParser = require('fast-xml-parser'); } catch(e) {}
    try { globalThis._fastXmlBuilder = require('fast-xml-builder'); } catch(e) {}
    try { globalThis._webdav = require('webdav'); } catch(e) {}
    try { globalThis._xml2js = require('xml2js'); } catch(e) {}
    try { globalThis._sax = require('sax'); } catch(e) {}
    try { globalThis._axios = require('axios'); } catch(e) {}
    try { globalThis._md5 = require('md5'); } catch(e) {}
    try { globalThis._base64 = require('base-64'); } catch(e) {}
    try { globalThis._strnum = require('strnum'); } catch(e) {}
    try { globalThis._urlJoin = require('url-join'); } catch(e) {}
    try { globalThis._urlParse = require('url-parse'); } catch(e) {}
    try { globalThis._querystringify = require('querystringify'); } catch(e) {}
    try { globalThis._entities = require('entities'); } catch(e) {}
    try { globalThis._undici = require('undici'); } catch(e) {}
    try { globalThis._nestedProperty = require('nested-property'); } catch(e) {}
    try { globalThis._mime = require('mime-types'); } catch(e) {}
    try { globalThis._mimeDb = require('mime-db'); } catch(e) {}
    try { globalThis._protobuf = require('protobufjs'); } catch(e) {}        // protobuf 解码预留
    try { globalThis._uWebSockets = require('uWebSockets.js'); } catch(e) {} // 高性能 WS 预留
    try { globalThis._followRedirects = require('follow-redirects'); } catch(e) {}
    try { globalThis._toughCookie = require('tough-cookie'); } catch(e) {}
    try { globalThis._jsdom = require('jsdom'); } catch(e) {}                // DOM 模拟预留
    try { globalThis._puppeteer = null; /* 太重不予加载 */ } catch(e) {}


    // ============================================================
    //  区块 4：MusicFree 完整 env 环境对象（超集）
    // ============================================================
    globalThis.env = {
        // --- 4a. 用户变量 ---
        getUserVariables: function() {
            try { return this.userVariables || {}; } catch(e) { return {}; }
        },
        userVariables: {},
        _userVarBacking: {},

        // --- 4b. 应用/系统信息 ---
        appName: 'MusicFree',
        appVersion: '1.0.0',
        os: (function() { try { return process.platform; } catch(e) { return 'win32'; } })(),
        platform: (function() { try { return process.platform; } catch(e) { return 'windows'; } })(),
        arch: (function() { try { return process.arch; } catch(e) { return 'x64'; } })(),
        nodeVersion: (function() { try { return process.version; } catch(e) { return ''; } })(),
        language: 'zh-CN',
        timezone: 'Asia/Shanghai',
        ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',

        // --- 4c. Cookie 管理 ---
        _cookies: {},
        getCookie: function(url) { try { return this._cookies[url] || ''; } catch(e) { return ''; } },
        setCookie: function(url, cookie) { try { this._cookies[url] = cookie; } catch(e) {} },
        removeCookie: function(url) { try { delete this._cookies[url]; } catch(e) {} },
        // Cookie 字符串解析
        parseCookies: function(cookieStr) {
            try {
                var result = {};
                (cookieStr || '').split(';').forEach(function(pair) {
                    var idx = pair.indexOf('=');
                    if (idx > 0) result[pair.substring(0, idx).trim()] = pair.substring(idx + 1).trim();
                });
                return result;
            } catch(e) { return {}; }
        },
        serializeCookies: function(cookieObj) {
            try {
                var self = this;
                return Object.keys(cookieObj || {}).map(function(k) { return k + '=' + cookieObj[k]; }).join('; ');
            } catch(e) { return ''; }
        },

        // --- 4d. localStorage 完整模拟 ---
        _localStorage: {},
        localStorage: (function() {
            var _store = {};
            return {
                get length() { return Object.keys(_store).length; },
                getItem: function(key) { return _store.hasOwnProperty(key) ? String(_store[key]) : null; },
                setItem: function(key, value) { _store[key] = String(value); },
                removeItem: function(key) { delete _store[key]; },
                clear: function() { _store = {}; },
                key: function(index) { return Object.keys(_store)[index] || null; },
            };
        })(),

        // --- 4e. sessionStorage 完整模拟 ---
        _sessionStorage: {},
        sessionStorage: (function() {
            var _store = {};
            return {
                get length() { return Object.keys(_store).length; },
                getItem: function(key) { return _store.hasOwnProperty(key) ? String(_store[key]) : null; },
                setItem: function(key, value) { _store[key] = String(value); },
                removeItem: function(key) { delete _store[key]; },
                clear: function() { _store = {}; },
                key: function(index) { return Object.keys(_store)[index] || null; },
            };
        })(),

        // --- 4f. 代理 ---
        proxy: { host: '', port: 0, enable: false },
        setProxy: function(host, port) { this.proxy = { host: host || '', port: port || 0, enable: true }; },
        clearProxy: function() { this.proxy = { host: '', port: 0, enable: false }; },

        // --- 4g. 网络请求 ---
        request: async function(url, options) {
            try {
                var axios = require('axios');
                var config = { url: url, method: (options && options.method) || 'GET', validateStatus: function() { return true; } };
                if (options) {
                    if (options.headers) config.headers = JSON.parse(JSON.stringify(options.headers));
                    if (options.data || options.body) config.data = options.data || options.body;
                    if (options.params) config.params = options.params;
                    if (options.timeout != null) config.timeout = options.timeout;
                    if (options.responseType) config.responseType = options.responseType;
                    if (options.maxRedirects != null) config.maxRedirects = options.maxRedirects;
                    if (options.maxContentLength != null) config.maxContentLength = options.maxContentLength;
                    if (options.maxBodyLength != null) config.maxBodyLength = options.maxBodyLength;
                }
                if (this.proxy && this.proxy.enable && this.proxy.host) {
                    try {
                        var HttpsProxyAgent = require('https-proxy-agent').HttpsProxyAgent;
                        config.httpsAgent = new HttpsProxyAgent('http://' + this.proxy.host + ':' + this.proxy.port);
                    } catch(e) {}
                }
                var resp = await axios(config);
                return resp.data;
            } catch(e) { throw e; }
        },

        // --- 4h. 文件下载 ---
        download: async function(url, destPath) {
            try { var axios = require('axios'), fs = require('fs'), path = require('path'); var resp = await axios({ url: url, method: 'GET', responseType: 'arraybuffer' }); fs.mkdirSync(path.dirname(destPath), { recursive: true }); fs.writeFileSync(destPath, Buffer.from(resp.data)); return { path: destPath, size: resp.data.byteLength }; } catch(e) { throw e; }
        },

        // --- 4i. 时间 ---
        dayjs: function(date) { try { return require('dayjs')(date); } catch(e) { return new Date(date); } },
        now: function() { return Date.now(); },
        nowISO: function() { return new Date().toISOString(); },
        timestamp: function() { return Math.floor(Date.now() / 1000); },

        // === 4j. 加密全套 ===

        // -- 哈希 --
        hash: function(algo, str) {
            try { return require('crypto').createHash(algo).update(String(str)).digest('hex'); } catch(e) { return ''; }
        },
        md5: function(str) { return this.hash('md5', str); },
        sha1: function(str) { return this.hash('sha1', str); },
        sha256: function(str) { return this.hash('sha256', str); },
        sha384: function(str) { return this.hash('sha384', str); },
        sha512: function(str) { return this.hash('sha512', str); },
        ripemd160: function(str) {
            try { return require('crypto').createHash('ripemd160').update(String(str)).digest('hex'); } catch(e) { return ''; }
        },

        // -- HMAC --
        hmac: function(algo, key, str) {
            try { return require('crypto').createHmac(algo, key).update(String(str)).digest('hex'); } catch(e) { return ''; }
        },
        hmacBase64: function(algo, key, str) {
            try { return require('crypto').createHmac(algo, key).update(String(str)).digest('base64'); } catch(e) { return ''; }
        },
        hmacSha1: function(key, str) { return this.hmacBase64('sha1', key, str); },
        hmacSha256: function(key, str) { return this.hmacBase64('sha256', key, str); },
        hmacSha512: function(key, str) { return this.hmacBase64('sha512', key, str); },

        // -- AES 对称加密 --
        aesEncrypt: function(data, key, opts) {
            try {
                var crypto = require('crypto');
                opts = opts || {};
                var algo = 'aes-128-ecb', iv = null;
                if (opts.mode === 'cbc') { algo = 'aes-128-cbc'; iv = opts.iv || Buffer.alloc(16, 0); }
                else if (opts.mode === 'gcm') { algo = 'aes-128-gcm'; iv = opts.iv || Buffer.alloc(12, 0); }
                if (opts.bits === 256) algo = algo.replace('128', '256');
                if (opts.bits === 192) algo = algo.replace('128', '192');
                var keyBuf = typeof key === 'string' ? Buffer.from(key, opts.keyEncoding || 'utf8') : key;
                var dataBuf = typeof data === 'string' ? Buffer.from(data, opts.dataEncoding || 'utf8') : data;
                var cipher = iv ? crypto.createCipheriv(algo, keyBuf, iv) : crypto.createCipheriv(algo, keyBuf, null);
                var result = Buffer.concat([cipher.update(dataBuf), cipher.final()]);
                return result;
            } catch(e) { return Buffer.from(''); }
        },
        aesDecrypt: function(data, key, opts) {
            try {
                var crypto = require('crypto');
                opts = opts || {};
                var algo = 'aes-128-ecb', iv = null;
                if (opts.mode === 'cbc') { algo = 'aes-128-cbc'; iv = opts.iv || Buffer.alloc(16, 0); }
                else if (opts.mode === 'gcm') { algo = 'aes-128-gcm'; iv = opts.iv || Buffer.alloc(12, 0); }
                if (opts.bits === 256) algo = algo.replace('128', '256');
                if (opts.bits === 192) algo = algo.replace('128', '192');
                var keyBuf = typeof key === 'string' ? Buffer.from(key, opts.keyEncoding || 'utf8') : key;
                var dataBuf = typeof data === 'string' ? Buffer.from(data, opts.dataEncoding || 'base64') : data;
                var decipher = iv ? crypto.createDecipheriv(algo, keyBuf, iv) : crypto.createDecipheriv(algo, keyBuf, null);
                var result = Buffer.concat([decipher.update(dataBuf), decipher.final()]);
                return result;
            } catch(e) { return Buffer.from(''); }
        },
        // AES-ECB 快捷
        aesEcbEncrypt: function(data, key) { return this.aesEncrypt(data, key, { mode: 'ecb' }); },
        aesEcbDecrypt: function(data, key) { return this.aesDecrypt(data, key, { mode: 'ecb' }); },
        // AES-CBC 快捷（PKCS7 padding）
        aesCbcEncrypt: function(data, key, iv) { return this.aesEncrypt(data, key, { mode: 'cbc', iv: iv }); },
        aesCbcDecrypt: function(data, key, iv) { return this.aesDecrypt(data, key, { mode: 'cbc', iv: iv }); },
        // AES-GCM 快捷
        aesGcmEncrypt: function(data, key, iv) { return this.aesEncrypt(data, key, { mode: 'gcm', iv: iv }); },
        aesGcmDecrypt: function(data, key, iv) { return this.aesDecrypt(data, key, { mode: 'gcm', iv: iv }); },

        // -- RSA --
        rsaSign: function(data, privateKeyPem, algo) {
            try {
                var sign = require('crypto').createSign(algo || 'RSA-SHA256');
                sign.update(typeof data === 'string' ? data : JSON.stringify(data));
                return sign.sign(privateKeyPem, 'base64');
            } catch(e) { return ''; }
        },
        rsaVerify: function(data, signature, publicKeyPem, algo) {
            try {
                var verify = require('crypto').createVerify(algo || 'RSA-SHA256');
                verify.update(typeof data === 'string' ? data : JSON.stringify(data));
                return verify.verify(publicKeyPem, signature, 'base64');
            } catch(e) { return false; }
        },

        // -- Base64 / Hex / 编解码 --
        encodeBase64: function(str) { try { return Buffer.from(String(str)).toString('base64'); } catch(e) { return ''; } },
        decodeBase64: function(str) { try { return Buffer.from(String(str), 'base64').toString('utf8'); } catch(e) { return String(str); } },
        encodeBase64Url: function(str) {
            try { return Buffer.from(String(str)).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); } catch(e) { return ''; }
        },
        decodeBase64Url: function(str) {
            try {
                var s = String(str).replace(/-/g, '+').replace(/_/g, '/');
                var mod = s.length % 4;
                if (mod === 2) s += '==';
                else if (mod === 3) s += '=';
                return Buffer.from(s, 'base64').toString('utf8');
            } catch(e) { return String(str); }
        },
        encodeHex: function(str) { try { return Buffer.from(String(str)).toString('hex'); } catch(e) { return ''; } },
        decodeHex: function(str) { try { return Buffer.from(String(str), 'hex').toString('utf8'); } catch(e) { return ''; } },
        decodeURIComponentSafe: function(str) { try { return decodeURIComponent(String(str)); } catch(e) { return String(str); } },
        encodeURIComponentFull: function(str) { try { return encodeURIComponent(String(str)); } catch(e) { return ''; } },

        // -- HTML 编解码 --
        htmlDecode: function(str) { try { return require('he').decode(String(str)); } catch(e) { return String(str); } },
        htmlEncode: function(str) { try { return require('he').encode(String(str)); } catch(e) { return String(str); } },
        htmlEntityName: function(str) {
            try {
                var he = require('he');
                var result = [];
                for (var i = 0; i < str.length; i++) {
                    var c = str.charCodeAt(i);
                    var s = he.encode(str[i], { useNamedReferences: true });
                    result.push(s === str[i] ? '&#' + c + ';' : s);
                }
                return result.join('');
            } catch(e) { return String(str); }
        },

        // --- 4k. 编码转换 (iconv-lite) ---
        iconv: {
            decode: function(buf, encoding) { try { return require('iconv-lite').decode(buf, encoding || 'gbk'); } catch(e) { return String(buf); } },
            encode: function(str, encoding) { try { return require('iconv-lite').encode(String(str), encoding || 'gbk'); } catch(e) { return Buffer.from(String(str)); } },
            encodingExists: function(enc) { try { return require('iconv-lite').encodingExists(enc); } catch(e) { return false; } },
        },

        // --- 4l. 数据解析 ---
        xml2json: function(xmlStr, opts) {
            try {
                var parser = require('fast-xml-parser');
                return parser.parse(String(xmlStr), Object.assign({ ignoreAttributes: false, attributeNamePrefix: '@_', allowBooleanAttributes: true }, opts || {}));
            } catch(e) { return null; }
        },
        json2xml: function(obj, opts) {
            try {
                var XmlBuilder = require('fast-xml-builder').XMLBuilder;
                return new XmlBuilder(obj, opts || {}).toString();
            } catch(e) { return ''; }
        },
        cheerio: function(html) { try { return require('cheerio').load(html || ''); } catch(e) { return null; } },

        // --- 4m. 压缩/解压 ---
        gzip: function(buf, opts) {
            try { var z = require('zlib'); return (opts && opts.async) ? new Promise(function(r, j) { z.gunzip(buf, function(e, d) { if (e) j(e); else r(d); }); }) : z.gunzipSync(buf); } catch(e) { return buf; }
        },
        deflate: function(buf, opts) {
            try { var z = require('zlib'); return (opts && opts.async) ? new Promise(function(r, j) { z.inflate(buf, function(e, d) { if (e) j(e); else r(d); }); }) : z.inflateSync(buf); } catch(e) { return buf; }
        },
        compressGzip: function(buf, opts) {
            try { var z = require('zlib'); return (opts && opts.async) ? new Promise(function(r, j) { z.gzip(buf, function(e, d) { if (e) j(e); else r(d); }); }) : z.gzipSync(buf); } catch(e) { return buf; }
        },
        compressDeflate: function(buf, opts) {
            try { var z = require('zlib'); return (opts && opts.async) ? new Promise(function(r, j) { z.deflate(buf, function(e, d) { if (e) j(e); else r(d); }); }) : z.deflateSync(buf); } catch(e) { return buf; }
        },
        brotliDecompress: function(buf, opts) {
            try {
                var z = require('zlib');
                if (z.brotliDecompressSync) { return (opts && opts.async) ? new Promise(function(r, j) { z.brotliDecompress(buf, function(e, d) { if (e) j(e); else r(d); }); }) : z.brotliDecompressSync(buf); }
                return buf;
            } catch(e) { return buf; }
        },
        brotliCompress: function(buf, opts) {
            try {
                var z = require('zlib');
                if (z.brotliCompressSync) { return (opts && opts.async) ? new Promise(function(r, j) { z.brotliCompress(buf, function(e, d) { if (e) j(e); else r(d); }); }) : z.brotliCompressSync(buf); }
                return buf;
            } catch(e) { return buf; }
        },
        inflate: function(buf) { return this.deflate(buf); },

        // --- 4n. WebDAV ---
        createWebDAVClient: function(url, options) {
            try { var wd = require('webdav'); return wd.createClient(url, options || {}); } catch(e) { return null; }
        },

        // --- 4o. 数据持久化 ---
        _persist: {},
        setPersistent: function(key, value) { try { this._persist[key] = value; } catch(e) {} },
        getPersistent: function(key) { try { return this._persist[key]; } catch(e) { return undefined; } },
        deletePersistent: function(key) { try { delete this._persist[key]; } catch(e) {} },
        allPersistent: function() { try { return Object.assign({}, this._persist); } catch(e) { return {}; } },

        // --- 4p. MIME 类型检测 ---
        mimeType: function(filenameOrExt) {
            try { return require('mime-types').lookup(filenameOrExt) || 'application/octet-stream'; } catch(e) { return 'application/octet-stream'; }
        },
        mimeExtension: function(mimeType) {
            try { return require('mime-types').extension(mimeType) || ''; } catch(e) { return ''; }
        },

        // --- 4q. 网络工具 ---
        isUrl: function(str) {
            try { new URL(str); return true; } catch(e) { return false; }
        },
        resolveUrl: function(base, relative) {
            try { return new URL(relative, base).href; } catch(e) { return relative; }
        },
        parseUrl: function(url) {
            try { var u = new URL(url); return { protocol: u.protocol, host: u.host, hostname: u.hostname, port: u.port, pathname: u.pathname, search: u.search, hash: u.hash, href: u.href, origin: u.origin }; } catch(e) { return null; }
        },
        buildQuery: function(params) {
            try { return Object.keys(params || {}).map(function(k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); }).join('&'); } catch(e) { return ''; }
        },
        parseQuery: function(qs) {
            try {
                var obj = {};
                (qs || '').replace(/^[?#]/, '').split('&').forEach(function(pair) {
                    if (!pair) return;
                    var idx = pair.indexOf('=');
                    if (idx > 0) obj[decodeURIComponent(pair.substring(0, idx))] = decodeURIComponent(pair.substring(idx + 1));
                    else obj[decodeURIComponent(pair)] = '';
                });
                return obj;
            } catch(e) { return {}; }
        },

        // --- 4r. 字符串/数据工具 ---
        uuid: function() {
            try { return require('crypto').randomUUID(); } catch(e) {
                return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
                    var r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
                    return v.toString(16);
                });
            }
        },
        randomHex: function(len) { try { return require('crypto').randomBytes((len || 16) / 2).toString('hex'); } catch(e) { return ''; } },
        randomInt: function(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; },
        sleep: function(ms) { return new Promise(function(r) { setTimeout(r, ms || 0); }); },
        retry: async function(fn, times, delay) {
            times = times || 3; delay = delay || 1000;
            for (var i = 0; i < times; i++) {
                try { return await fn(); } catch(e) { if (i === times - 1) throw e; }
                await this.sleep(delay);
            }
        },
        pad: function(num, size) { var s = String(num); while (s.length < (size || 2)) s = '0' + s; return s; },
        sortBy: function(arr, key, desc) {
            return (arr || []).slice().sort(function(a, b) {
                var va = typeof key === 'function' ? key(a) : a[key];
                var vb = typeof key === 'function' ? key(b) : b[key];
                return desc ? (vb > va ? 1 : vb < va ? -1 : 0) : (va > vb ? 1 : va < vb ? -1 : 0);
            });
        },
        chunk: function(arr, size) {
            var result = [];
            for (var i = 0; i < (arr || []).length; i += size) result.push(arr.slice(i, i + size));
            return result;
        },
        deepClone: function(obj) {
            try { return JSON.parse(JSON.stringify(obj)); } catch(e) { return obj; }
        },
        safeJsonParse: function(str, fallback) {
            try { return JSON.parse(str); } catch(e) { return fallback !== undefined ? fallback : null; }
        },
    };

    // ---------- env 子对象引用快捷方式 ----------
    globalThis.env.localStorage = globalThis.env.localStorage;
    globalThis.env.sessionStorage = globalThis.env.sessionStorage;


    // ============================================================
    //  区块 5：全局工具函数
    // ============================================================
    globalThis.uuid = function() { return globalThis.env.uuid(); };
    globalThis.sleep = function(ms) { return globalThis.env.sleep(ms); };
    globalThis.pause = function(ms) { return globalThis.env.sleep(ms); };
    globalThis._retry = function(fn, times, delay) { return globalThis.env.retry(fn, times, delay); };


    // ============================================================
    //  区块 6：XMLHttpRequest 完整模拟
    // ============================================================
    if (!globalThis.XMLHttpRequest) {
        globalThis.XMLHttpRequest = function() {
            var self = this;
            this.UNSENT = 0; this.OPENED = 1; this.HEADERS_RECEIVED = 2; this.LOADING = 3; this.DONE = 4;
            this.readyState = 0; this.status = 0; this.statusText = '';
            this.responseText = ''; this.response = null; this.responseXML = null;
            this.responseType = ''; this.responseURL = '';
            this.onreadystatechange = null; this.onload = null; this.onerror = null;
            this.onprogress = null; this.ontimeout = null; this.onabort = null;
            this.timeout = 0; this.withCredentials = false;
            this._headers = {}; this._method = 'GET'; this._url = '';
            this._aborted = false;
            this._eventListeners = {};

            this.addEventListener = function(type, fn) { if (!self._eventListeners[type]) self._eventListeners[type] = []; self._eventListeners[type].push(fn); };
            this.removeEventListener = function(type, fn) { if (!self._eventListeners[type]) return; self._eventListeners[type] = self._eventListeners[type].filter(function(f) { return f !== fn; }); };
            function _fire(type) {
                if (self._eventListeners[type]) self._eventListeners[type].forEach(function(f) { try { f.call(self); } catch(e) {} });
                if (type === 'load' && self.onload) self.onload();
                if (type === 'error' && self.onerror) self.onerror();
                if (type === 'timeout' && self.ontimeout) self.ontimeout();
                if (type === 'abort' && self.onabort) self.onabort();
            }
            this.open = function(method, url, async, user, password) {
                self._method = (method || 'GET').toUpperCase();
                self._url = url || '';
                self.readyState = 1;
                if (self.onreadystatechange) self.onreadystatechange();
            };
            this.setRequestHeader = function(k, v) { self._headers[k] = v; };
            this.getResponseHeader = function(k) { return self._respHeaders ? self._respHeaders[k] || null : null; };
            this.getAllResponseHeaders = function() {
                if (!self._respHeaders) return '';
                return Object.keys(self._respHeaders).map(function(k) { return k + ': ' + self._respHeaders[k]; }).join('\r\n');
            };
            this.overrideMimeType = function(mime) { self._overrideMime = mime; };
            this.send = function(body) {
                if (self._aborted) return;
                self.readyState = 2;
                if (self.onreadystatechange) self.onreadystatechange();
                var axios = require('axios');
                var cfg = {
                    url: self._url, method: self._method, headers: self._headers,
                    responseType: self.responseType === 'arraybuffer' ? 'arraybuffer' : 'text',
                    timeout: self.timeout || 30000,
                    validateStatus: function() { return true; },
                };
                if (body !== undefined && body !== null) cfg.data = body;
                axios(cfg).then(function(res) {
                    if (self._aborted) return;
                    self._respHeaders = res.headers || {};
                    self.status = res.status;
                    self.statusText = res.statusText || '';
                    self.responseURL = self._url;
                    self.readyState = 3; if (self.onreadystatechange) self.onreadystatechange();
                    if (self.responseType === 'arraybuffer') {
                        self.response = Buffer.from(res.data || '');
                    } else if (self.responseType === 'json') {
                        self.response = typeof res.data === 'string' ? JSON.parse(res.data) : res.data;
                        self.responseText = JSON.stringify(self.response);
                    } else if (self.responseType === 'document') {
                        self.responseText = typeof res.data === 'string' ? res.data : JSON.stringify(res.data);
                        self.response = self.responseText;
                        self.responseXML = null;
                    } else {
                        self.responseText = typeof res.data === 'string' ? res.data : JSON.stringify(res.data);
                        self.response = self.responseText;
                    }
                    self.readyState = 4;
                    if (self.onreadystatechange) self.onreadystatechange();
                    _fire('load');
                }).catch(function(e) {
                    if (self._aborted) return;
                    self.status = 0; self.statusText = e.message || 'Network Error';
                    self.readyState = 4;
                    if (self.onreadystatechange) self.onreadystatechange();
                    _fire('error');
                });
            };
            this.abort = function() { self._aborted = true; self.readyState = 4; self.status = 0; _fire('abort'); };
        };
        globalThis.XMLHttpRequest.UNSENT = 0;
        globalThis.XMLHttpRequest.OPENED = 1;
        globalThis.XMLHttpRequest.HEADERS_RECEIVED = 2;
        globalThis.XMLHttpRequest.LOADING = 3;
        globalThis.XMLHttpRequest.DONE = 4;
    }


    // ============================================================
    //  区块 7：Event/EventTarget/CustomEvent/AbortController
    // ============================================================
    if (!globalThis.Event) {
        globalThis.Event = function(type, opts) {
            this.type = type;
            this.bubbles = (opts || {}).bubbles || false;
            this.cancelable = (opts || {}).cancelable || false;
            this.composed = (opts || {}).composed || false;
            this.defaultPrevented = false;
            this.preventDefault = function() { this.defaultPrevented = true; };
            this.stopPropagation = function() {};
            this.stopImmediatePropagation = function() {};
        };
    }
    if (!globalThis.CustomEvent) {
        globalThis.CustomEvent = function(type, opts) {
            globalThis.Event.call(this, type, opts);
            this.detail = (opts || {}).detail;
        };
    }
    if (!globalThis.AbortController) {
        globalThis.AbortController = function() {
            var self = this;
            var _listeners = {};
            this.signal = {
                aborted: false,
                reason: null,
                onabort: null,
                addEventListener: function(type, fn) { if (!_listeners[type]) _listeners[type] = []; _listeners[type].push(fn); },
                removeEventListener: function(type, fn) { if (_listeners[type]) _listeners[type] = _listeners[type].filter(function(f) { return f !== fn; }); },
                throwIfAborted: function() { if (self.signal.aborted) throw self.signal.reason || new Error('Aborted'); },
            };
            this.abort = function(reason) {
                if (self.signal.aborted) return;
                self.signal.aborted = true;
                self.signal.reason = reason || new Error('Aborted');
                if (self.signal.onabort) try { self.signal.onabort(); } catch(e) {}
                if (_listeners['abort']) _listeners['abort'].forEach(function(f) { try { f(); } catch(e) {} });
            };
        };
    }


    // ============================================================
    //  区块 8：文件系统快捷访问
    // ============================================================
    try {
        globalThis.fs = require('fs');
        globalThis.path = require('path');
        globalThis.os = require('os');
        // path 工具别名
        globalThis._join = require('path').join;
        globalThis._resolve = require('path').resolve;
        globalThis._dirname = require('path').dirname;
        globalThis._basename = require('path').basename;
        globalThis._extname = require('path').extname;
        globalThis._normalize = require('path').normalize;
    } catch(e) {}


    // ============================================================
    //  区块 9：定时器 / 微任务 / process 增强
    // ============================================================
    if (!globalThis.setTimeout) globalThis.setTimeout = setTimeout;
    if (!globalThis.setInterval) globalThis.setInterval = setInterval;
    if (!globalThis.clearTimeout) globalThis.clearTimeout = clearTimeout;
    if (!globalThis.clearInterval) globalThis.clearInterval = clearInterval;
    if (!globalThis.queueMicrotask) globalThis.queueMicrotask = function(fn) { Promise.resolve().then(fn); };
    if (!globalThis.setImmediate) {
        try { globalThis.setImmediate = require('timers').setImmediate; } catch(e) {
            globalThis.setImmediate = function(fn) { return setTimeout(fn, 0); };
        }
    }
    if (!globalThis.clearImmediate) {
        try { globalThis.clearImmediate = require('timers').clearImmediate; } catch(e) {
            globalThis.clearImmediate = function(id) { clearTimeout(id); };
        }
    }
    // process.nextTick
    try { globalThis._nextTick = process.nextTick.bind(process); } catch(e) {}


    // ============================================================
    //  区块 10：Promise 扩充垫片
    // ============================================================
    if (!Promise.allSettled) {
        Promise.allSettled = function(promises) {
            return Promise.all((promises || []).map(function(p) {
                return Promise.resolve(p).then(
                    function(v) { return { status: 'fulfilled', value: v }; },
                    function(e) { return { status: 'rejected', reason: e }; }
                );
            }));
        };
    }
    if (!Promise.any) {
        Promise.any = function(promises) {
            return new Promise(function(resolve, reject) {
                var errors = []; var remaining = (promises || []).length;
                if (remaining === 0) { reject(new AggregateError([], 'All promises were rejected')); return; }
                promises.forEach(function(p, i) {
                    Promise.resolve(p).then(resolve).catch(function(e) {
                        errors[i] = e; remaining--;
                        if (remaining === 0) reject(new AggregateError(errors, 'All promises were rejected'));
                    });
                });
            });
        };
    }
    if (!AggregateError) { globalThis.AggregateError = function(errors, message) { var e = new Error(message); e.errors = errors; e.name = 'AggregateError'; return e; }; }


    // ============================================================
    //  区块 11：常见全局别名 (防某些插件直接引用)
    // ============================================================
    globalThis.axios = globalThis._axios || null;
    globalThis.cheerio = globalThis._cheerio || null;
    // 如果插件不用 env.cheerio 而直接调 cheerio.load
    if (globalThis._cheerio) { globalThis._cheerioLoad = globalThis._cheerio.load.bind(globalThis._cheerio); }


    // ============================================================
    //  区块 12：process 对象补齐
    // ============================================================
    try {
        if (!process.env) process.env = {};
        if (!process.versions) process.versions = { node: process.version.replace(/^v/, '') };
        if (!process.cwd) process.cwd = function() { return require('path').resolve('.'); };
        if (!process.hrtime) process.hrtime = function() { var t = Date.now(); return [Math.floor(t / 1000), (t % 1000) * 1000000]; };
        if (!process.memoryUsage) process.memoryUsage = function() { return { rss: 0, heapTotal: 0, heapUsed: 0, external: 0 }; };
    } catch(e) {}


    // ============================================================
    //  区块 13：未捕获异常全局兜底（让插件崩了不影响宿主）
    // ============================================================
    var _origUncaught = process.listeners('uncaughtException');
    process.removeAllListeners('uncaughtException');
    process.on('uncaughtException', function(err) {
        try { console.error('[Sandbox] Uncaught:', err && err.message); } catch(e) {}
        if (_origUncaught && _origUncaught.length) _origUncaught.forEach(function(f) { try { f(err); } catch(e2) {} });
    });
    process.on('unhandledRejection', function(reason) {
        try { console.error('[Sandbox] UnhandledRejection:', reason && reason.message); } catch(e) {}
    });

    // ---------- 环境加载完成签名 ----------
    // console.log('[Sandbox] MusicFree V3 终极沙箱已就位');
})();
"""

    def __init__(self):
        self._plugins: dict[str, MusicFreePlugin] = {}
        self._js_plugins: dict[str, MusicFreePlugin] = {}
        self._net = GerNet()
        os.makedirs(self.PLUGIN_DIR, exist_ok=True)
        self._load_all()

    def _load_all(self):
        """从plugins目录加载所有插件（JSON + JS）。"""
        import hashlib
        self._plugins.clear()
        self._js_plugins.clear()
        self._loaded_hashes = set()
        if not os.path.isdir(self.PLUGIN_DIR):
            return
        for fname in os.listdir(self.PLUGIN_DIR):
            fpath = os.path.join(self.PLUGIN_DIR, fname)
            try:
                if fname.endswith(".json"):
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    plugin = MusicFreePlugin(data)
                    self._plugins[plugin.platform] = plugin
                    print(f"[PluginMgr] JSON插件: {plugin.platform} v{plugin.version}")
                elif fname.endswith(".js"):
                    # 去重：相同内容的 JS 文件只加载一次
                    with open(fpath, "rb") as fhash:
                        h = hashlib.md5(fhash.read()).hexdigest()
                    if h in self._loaded_hashes:
                        continue
                    self._loaded_hashes.add(h)
                    plugin = self._load_js_plugin(fpath)
                    if plugin:
                        self._js_plugins[plugin.platform] = plugin
                        print(f"[PluginMgr] JS插件: {plugin.platform} v{plugin.version}")
            except Exception as e:
                print(f"[PluginMgr] 加载 {fname} 失败: {e}")

    def _load_js_plugin(self, js_path: str) -> MusicFreePlugin | None:
        """通过Node.js解析JS插件元数据。"""
        node = self._find_node()
        if not node:
            return None
        sandbox = MusicFreePluginManager._MUSICFREE_SANDBOX
        script = sandbox + """
        try {
            const mod = require(process.argv[2]);
            const m = mod.default || mod;
            console.log('__PSYSTEM_META__' + JSON.stringify({
                platform: m.platform || '未知',
                version: m.version || '0.0.0',
                author: m.author || '',
                srcUrl: m.srcUrl || '',
                supportedSearchType: m.supportedSearchType || ['music'],
                hasSearch: typeof m.search === 'function',
                hasGetMediaSource: typeof m.getMediaSource === 'function',
                hasGetLyric: typeof m.getLyric === 'function',
                hasGetTopLists: typeof m.getTopLists === 'function',
            }));
        } catch(e) { console.log('__PSYSTEM_META__' + JSON.stringify({error: e.message, stack: e.stack ? String(e.stack) : undefined})); }
        """
        try:
            node_modules = NodeEnv.get_node_modules_path()
            paths = [os.path.dirname(js_path)]
            if node_modules and os.path.isdir(node_modules):
                paths.append(node_modules)

            # 写临时JS文件避免命令行长度超限
            _tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8') as _f:
                    _f.write(script)
                    _tmp_path = _f.name
                result = subprocess.run(
                    [node, _tmp_path, js_path],
                    capture_output=True, text=True, encoding="utf-8", timeout=15,
                    cwd=os.path.dirname(js_path),
                    env={**os.environ, "NODE_PATH": os.pathsep.join(paths)}
                )
            finally:
                if _tmp_path and os.path.isfile(_tmp_path):
                    try: os.unlink(_tmp_path)
                    except OSError: pass
            raw = (result.stdout or "").strip()
            if not raw:
                stderr_tail = (result.stderr or "")[:200]
                if stderr_tail:
                    print(f"[PluginMgr] JS解析失败(无输出): {stderr_tail}")
                return None
            # 查找 __PSYSTEM_META__ 标记行（兼容插件自执行代码污染 stdout）
            marker = "__PSYSTEM_META__"
            meta = None
            for line in reversed(raw.split("\n")):
                line = line.strip()
                if line.startswith(marker):
                    try:
                        meta = json.loads(line[len(marker):])
                    except json.JSONDecodeError:
                        continue
                    break
            if meta is None:
                # 回退：尝试第一行
                first_line = raw.split("\n")[0].strip()
                try:
                    meta = json.loads(first_line)
                except json.JSONDecodeError:
                    print(f"[PluginMgr] JS元数据无法解析: {first_line[:200]}")
                    return None
            if "error" in meta:
                print(f"[PluginMgr] JS解析错误: {meta['error']}")
                return None
            data = {"platform": meta["platform"], "version": meta["version"],
                    "author": meta["author"], "srcUrl": meta.get("srcUrl", ""),
                    "supportedSearchType": meta.get("supportedSearchType", ["music"]),
                    "_hasSearch": meta.get("hasSearch", False),
                    "_hasGetMediaSource": meta.get("hasGetMediaSource", False),
                    "_hasGetLyric": meta.get("hasGetLyric", False),
                    "_hasGetTopLists": meta.get("hasGetTopLists", False)}
            return MusicFreePlugin(data, js_path=js_path)
        except Exception as e:
            print(f"[PluginMgr] JS插件加载异常: {e}")
            return None

    @staticmethod
    def _find_node() -> str | None:
        """查找Node.js路径：内建 > 系统PATH > 常见安装路径。"""
        # 1) 优先使用内建 Node.js（由 NodeEnv 管理）
        builtin = NodeEnv.get_path()
        if builtin and os.path.isfile(builtin):
            return builtin

        # 1.5) NodeEnv 未初始化时，直接搜索 nodejs 目录
        extracted = NodeEnv._find_extracted_node()
        if extracted and os.path.isfile(extracted):
            return extracted

        # 2) 系统 PATH
        for cmd in ["node", "nodejs"]:
            try:
                r = subprocess.run([cmd, "--version"], capture_output=True, text=True, encoding="utf-8", timeout=5)
                if r.returncode == 0:
                    return cmd
            except Exception:
                continue
        # 3) 常见安装路径
        for p in [r"C:\Program Files\nodejs\node.exe",
                  r"C:\Program Files (x86)\nodejs\node.exe"]:
            if os.path.exists(p):
                return p
        return None

    def reload(self):
        self._load_all()

    # ===== CRUD =====
    def install_from_file(self, filepath: str) -> tuple[bool, str]:
        if not os.path.exists(filepath):
            return False, "文件不存在"
        try:
            ext = os.path.splitext(filepath)[1].lower()
            fname = os.path.basename(filepath)
            dest = os.path.join(self.PLUGIN_DIR, fname)
            if ext == ".json":
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                platform = data.get("platform", "")
                if not platform:
                    return False, "插件缺少 platform 字段"
                with open(dest, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self._plugins[platform] = MusicFreePlugin(data)
            elif ext == ".js":
                with open(filepath, "rb") as f:
                    content = f.read()
                with open(dest, "wb") as f:
                    f.write(content)
                plugin = self._load_js_plugin(dest)
                if plugin:
                    self._js_plugins[plugin.platform] = plugin
                else:
                    # 即使解析失败也保留文件
                    return True, "JS插件已安装（解析元数据失败但文件已保存，可能仍需Node.js）"
            else:
                return False, "不支持的文件格式（仅支持 .json / .js）"
            return True, f"插件安装成功"
        except json.JSONDecodeError:
            return False, "JSON 格式无效"
        except Exception as e:
            return False, str(e)

    def install_from_url(self, url: str) -> tuple[bool, str]:
        try:
            code, content = self._net.get(url, use_cache=False)
            if code != 200:
                return False, f"下载失败 HTTP {code}"
            ext = ".js" if url.endswith(".js") else ".json"
            # 判断内容类型
            if content.strip().startswith("module.exports") or content.strip().startswith("const "):
                ext = ".js"
            elif content.strip().startswith(("{", "[")):
                ext = ".json"
            # 保存到临时文件
            tmp = os.path.join(tempfile.gettempdir(), f"mf_plugin_{int(time.time())}{ext}")
            with open(tmp, "w" if ext == ".json" else "w", encoding="utf-8") as f:
                f.write(content)
            return self.install_from_file(tmp)
        except Exception as e:
            return False, str(e)

    def uninstall(self, platform: str) -> tuple[bool, str]:
        for fname in os.listdir(self.PLUGIN_DIR):
            fpath = os.path.join(self.PLUGIN_DIR, fname)
            try:
                if fname.endswith(".json"):
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if data.get("platform") == platform:
                        os.remove(fpath)
                        self._plugins.pop(platform, None)
                        return True, f"插件 '{platform}' 已卸载"
            except Exception:
                pass
        self._plugins.pop(platform, None)
        self._js_plugins.pop(platform, None)
        return False, f"插件 '{platform}' 不存在"

    def get_plugin(self, platform: str) -> MusicFreePlugin | None:
        return self._plugins.get(platform) or self._js_plugins.get(platform)

    def get_all_plugins(self) -> list[MusicFreePlugin]:
        return list(self._plugins.values()) + list(self._js_plugins.values())

    def get_platforms(self) -> list[str]:
        return list(self._plugins.keys()) + list(self._js_plugins.keys())

    def search_all(self, keyword: str, page: int = 1) -> list[dict]:
        results = []
        for plugin in self.get_all_plugins():
            if not plugin.enabled:
                continue
            sr = plugin.search(keyword, page)
            for song in sr.get("data", []):
                song["_platform"] = plugin.platform
            results.extend(sr.get("data", []))
        return results

    # ===== 插件市场 =====
    def fetch_marketplace(self) -> list[dict]:
        """从官方市场获取可用插件列表（自动尝试多源）。"""
        for market_url in [self.MARKET_URL, self.MARKET_MIRROR, self.MARKET_CDN]:
            try:
                code, text = self._net.get(market_url, use_cache=True, cache_ttl=1800)
                if code == 200:
                    data = json.loads(text)
                    if isinstance(data, dict) and "plugins" in data:
                        return data["plugins"]
                    if isinstance(data, list):
                        return data
            except Exception as e:
                print(f"[PluginMgr] 市场请求失败 ({market_url[:50]}...): {e}")
                continue
        return []

    def install_from_market(self, plugin_entry: dict) -> tuple[bool, str]:
        """从市场条目安装插件。"""
        url = plugin_entry.get("url") or plugin_entry.get("srcUrl", "")
        if not url:
            return False, "插件地址无效"
        platform = plugin_entry.get("platform") or plugin_entry.get("name", "")
        if platform and self.get_plugin(platform):
            # 已安装，检查更新
            installed = self.get_plugin(platform)
            new_ver = plugin_entry.get("version", "0.0.0")
            if self._version_compare(new_ver, installed.version) <= 0:
                return True, f"'{platform}' 已是最新版本 v{installed.version}"
        return self.install_from_url(url)

    @staticmethod
    def _version_compare(a: str, b: str) -> int:
        """比较版本号 a vs b, 返回 1/0/-1。"""
        def _parts(v):
            try:
                return [int(x) for x in v.split(".")]
            except Exception:
                return [0, 0, 0]
        pa, pb = _parts(a), _parts(b)
        for i in range(3):
            if pa[i] > pb[i]: return 1
            if pa[i] < pb[i]: return -1
        return 0


# ================================================================
# 22. 内置默认插件配置（多音源、完整方法）
# ================================================================
def _create_default_plugins():
    """创建丰富的内置插件配置。"""
    plugins_data = [
        {
            "platform": "网易云音乐",
            "version": "1.0.0",
            "author": "GerOS",
            "home": "https://music.163.com",
            "base_url": "https://music.163.com",
            "primaryKey": ["id"],
            "supportedSearchType": ["music", "album", "artist", "sheet"],
            "headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://music.163.com",
            },
            "methods": {
                "search": {
                    "url": "https://music.163.com/api/search/get?s={keyword}&type=1&limit={limit}&offset={offset}",
                    "method": "GET",
                    "result_path": "result.songs",
                },
                "getMediaSource": {
                    "url": "https://music.163.com/api/song/enhance/player/url?id={id}&ids=[{id}]&br=320000",
                    "method": "GET",
                    "result_path": "data.0.url",
                },
                "getLyric": {
                    "url": "https://music.163.com/api/song/lyric?id={id}&lv=-1",
                    "method": "GET",
                    "result_path": "lrc.lyric",
                },
                "getTopLists": {
                    "url": "https://music.163.com/api/playlist/detail?id=3778678",
                    "method": "GET",
                    "result_path": "result.tracks",
                },
            },
        },
        {
            "platform": "QQ音乐",
            "version": "1.0.0",
            "author": "GerOS",
            "home": "https://y.qq.com",
            "base_url": "https://c.y.qq.com",
            "primaryKey": ["mid"],
            "supportedSearchType": ["music"],
            "headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://y.qq.com",
            },
            "methods": {
                "search": {
                    "url": "https://c.y.qq.com/soso/fcgi-bin/client_search_cp?w={keyword}&p={page}&n={limit}&format=json",
                    "method": "GET",
                    "result_path": "data.song.list",
                },
                "getMediaSource": {
                    "url": "https://u.y.qq.com/cgi-bin/musicu.fcg?data=%7B%22req_0%22%3A%7B%22module%22%3A%22vkey.GetVkeyServer%22%2C%22method%22%3A%22CgiGetVkey%22%2C%22param%22%3A%7B%22guid%22%3A%22358840384%22%2C%22songmid%22%3A%5B%22{mid}%22%5D%2C%22songtype%22%3A%5B0%5D%2C%22uin%22%3A%220%22%2C%22loginflag%22%3A1%2C%22platform%22%3A%2220%22%7D%7D%7D",
                    "method": "GET",
                    "result_path": "req_0.data.midurlinfo.0.purl",
                },
                "getTopLists": {
                    "url": "https://c.y.qq.com/v8/fcg-bin/fcg_myqq_toplist.fcg?format=json",
                    "method": "GET",
                    "result_path": "data.topList",
                },
            },
        },
    ]

    plugin_dir = os.path.join(app_dir(), "plugins")
    os.makedirs(plugin_dir, exist_ok=True)
    for data in plugins_data:
        fpath = os.path.join(plugin_dir, f"{data['platform']}.json")
        if not os.path.exists(fpath):
            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass


_create_default_plugins()


# ================================================================
# 21. 音乐播放器（时间条、频谱动画、历史播放、播放列表、MusicFree插件集成）
# ================================================================
class MusicPlayerApp(App):
    MCI_ALIAS = "geros_mp"
    VIZ_BARS = 24                     # 频谱柱数量
    VIZ_H = 50                        # 频谱区高度

    def __init__(self, bus, theme):
        super().__init__("music", "音乐", 860, 580, bus, theme)
        self._fp: str | None = None
        self._playlist: list[str] = []
        self._history: list[str] = []
        self._playing = False
        self._paused = False
        self._total_ms = 0
        self._seek_dragging = False
        self._update_id = None         # 进度轮询 after id
        self._viz_id = None            # 频谱动画 after id
        self._volume = 800
        self._root_frame: tk.Frame | None = None
        self._viz_heights = [2.0] * self.VIZ_BARS   # 当前柱高
        self._viz_speeds = [0.0] * self.VIZ_BARS     # 变化速度
        self._viz_phase = 0
        self._closed = False
        # 音效/EQ
        self._eq_mode = "默认"          # 当前预设名
        self._eq_auto = False           # 自调节开关
        self._eq_bands = [0] * 10       # 10频段增益 -20~+20
        self._eq_panel_visible = False  # EQ 面板是否展开
        # MusicFree 插件系统
        self._plugin_mgr = MusicFreePluginManager()
        self._online_results: list[dict] = []  # 搜索结果缓存
        self._temp_dir = tempfile.mkdtemp(prefix="geros_music_")  # 临时下载目录
        self._stream_fp: str | None = None    # 流媒体临时文件路径
        # 歌词系统
        self._current_song_item: dict | None = None  # 当前播放歌曲的完整item（用于获取歌词）
        self._current_plugin_name: str = ""          # 当前歌曲所属插件名
        self._lrc_lines: list[tuple[float, str]] = []      # [(time_ms, text), ...] 主歌词
        self._lrc_trans: list[tuple[float, str]] = []      # [(time_ms, text), ...] 翻译歌词
        self._current_lrc_idx: int = -1                     # 当前高亮行索引
        self._lyric_fetching: bool = False                  # 是否正在获取歌词
        self._lyric_canvas: tk.Canvas | None = None         # 歌词绘制画布
        self._lyric_scroll: float = 0.0                     # 歌词滚动offset
        self._has_lyrics: bool = False                      # 是否有歌词可显示
        # 关闭窗口 → 清理
        self._bus.on("window:close", self._on_window_close)

    # ===== 工具 =====
    @staticmethod
    def _ms_to_str(ms: int) -> str:
        ms = max(0, ms)
        s = ms // 1000
        m, sec = divmod(s, 60)
        return f"{m}:{sec:02d}"

    def _mci(self, cmd: str) -> int:
        buf = ctypes.create_unicode_buffer(256)
        return ctypes.windll.winmm.mciSendStringW(cmd, buf, 255, None)

    def _mci_str(self, cmd: str) -> str:
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.winmm.mciSendStringW(cmd, buf, 255, None)
        return buf.value.strip()

    # ===== 窗口关闭清理 =====
    def _on_window_close(self, app_id: str):
        if app_id != self.app_id or self._closed:
            return
        self._closed = True
        self.cleanup()

    def cleanup(self):
        """释放所有资源：停止 MCI、取消定时器、清空数据、清理临时文件。"""
        # 取消频谱动画
        if self._viz_id:
            try:
                if self._root_frame:
                    self._root_frame.after_cancel(self._viz_id)
            except Exception:
                pass
            self._viz_id = None
        # 取消进度轮询 + 停止 MCI
        self._stop_mci()
        self._playing = False
        self._paused = False
        # 清空运行时数据
        self._playlist.clear()
        self._history.clear()
        self._viz_heights.clear()
        self._viz_speeds.clear()
        self._online_results.clear()
        # 清空歌词数据
        self._lrc_lines.clear()
        self._lrc_trans.clear()
        self._current_lrc_idx = -1
        self._current_song_item = None
        self._has_lyrics = False
        # 清理临时下载文件
        try:
            if self._temp_dir and os.path.isdir(self._temp_dir):
                import shutil
                shutil.rmtree(self._temp_dir, ignore_errors=True)
        except Exception:
            pass
        self._stream_fp = None

    # ===== 界面 =====
    def _fill(self, parent: tk.Frame):
        t = self._palette
        self._root_frame = parent

        parent.grid_rowconfigure(0, weight=0)   # 顶部信息
        parent.grid_rowconfigure(1, weight=0)   # 进度条
        parent.grid_rowconfigure(2, weight=0)   # 频谱
        parent.grid_rowconfigure(3, weight=0)   # 控制栏
        parent.grid_rowconfigure(4, weight=0)   # 歌词区
        parent.grid_rowconfigure(5, weight=1)   # 标签页内容区
        parent.grid_columnconfigure(0, weight=1)

        # ---- 顶部信息 + 音效按钮 ----
        top = tk.Frame(parent, bg=t.get("bg"), height=44)
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 0))
        top.pack_propagate(False)

        self._eq_btn = tk.Button(top, text="\U0001f39b 音效 \u25be", bg=t.get("bg_toolbar"),
                                 fg=t.get("fg"), relief="flat", font=font(size=9),
                                 cursor="hand2", padx=8, pady=3,
                                 command=self._on_eq_click)
        self._eq_btn.pack(side="right", padx=(6, 0))

        self._song_icon = tk.Label(top, text="\u266b", bg=t.get("bg"), fg=t.get("accent"),
                                   font=icon_font(26))
        self._song_icon.pack(side="left")

        info_f = tk.Frame(top, bg=t.get("bg"))
        info_f.pack(side="left", fill="both", expand=True, padx=(8, 6))
        self._lbl = tk.Label(info_f, text="未选择曲目", bg=t.get("bg"), fg=t.get("fg"),
                             font=font(size=13, bold=True), anchor="w")
        self._lbl.pack(fill="x")
        self._time_lbl = tk.Label(info_f, text="--:-- / --:--", bg=t.get("bg"),
                                  fg=t.get("fg_dim"), font=font(size=9), anchor="w")
        self._time_lbl.pack(fill="x", pady=(2, 0))

        # ---- 进度条 ----
        pb_f = tk.Frame(parent, bg=t.get("bg"))
        pb_f.grid(row=1, column=0, sticky="ew", padx=14, pady=(6, 0))
        self._pb = tk.Canvas(pb_f, bg=t.get("bg_panel"), height=7,
                             highlightthickness=1, bd=0,
                             highlightbackground=t.get("border"),
                             highlightcolor=t.get("border"),
                             cursor="hand2", takefocus=0)
        self._pb.pack(fill="x")
        self._pb.bind("<Button-1>", self._on_pb_click)
        self._pb.bind("<B1-Motion>", self._on_pb_drag)
        self._pb.bind("<ButtonRelease-1>", self._on_pb_release)

        # ---- 频谱可视化 ----
        self._viz = tk.Canvas(parent, bg=t.get("bg"), height=self.VIZ_H,
                              highlightthickness=0, bd=0, takefocus=0)
        self._viz.grid(row=2, column=0, sticky="ew", padx=14, pady=(2, 0))

        # ---- 控制栏 ----
        ctrl = tk.Frame(parent, bg=t.get("bg"))
        ctrl.grid(row=3, column=0, sticky="ew", padx=14, pady=(4, 2))

        tk.Button(ctrl, text="\u23ee", bg=t.get("bg"), fg=t.get("fg"), relief="flat",
                  font=font(size=12), command=self._play_prev, cursor="hand2",
                  pady=2).pack(side="left", padx=1)
        self._pp_btn = tk.Button(ctrl, text="\u25b6", bg=t.get("accent"), fg="white",
                                 relief="flat", font=font(size=11), command=self._toggle_play,
                                 cursor="hand2", padx=12, pady=3)
        self._pp_btn.pack(side="left", padx=2)
        tk.Button(ctrl, text="\u23ed", bg=t.get("bg"), fg=t.get("fg"), relief="flat",
                  font=font(size=12), command=self._play_next, cursor="hand2",
                  pady=2).pack(side="left", padx=1)
        tk.Button(ctrl, text="\u23f9", bg=t.get("bg"), fg=t.get("fg"), relief="flat",
                  font=font(size=12), command=self._stop, cursor="hand2",
                  pady=2).pack(side="left", padx=6)

        vol_f = tk.Frame(ctrl, bg=t.get("bg"))
        vol_f.pack(side="right")
        tk.Label(vol_f, text="\U0001f50a", bg=t.get("bg"), fg=t.get("fg"),
                 font=font(size=10)).pack(side="left", padx=(0, 1))
        self._vol_scale = tk.Scale(vol_f, from_=0, to=1000, orient="horizontal",
                                   bg=t.get("bg"), fg=t.get("fg"), relief="flat",
                                   highlightthickness=0, length=100, showvalue=False,
                                   command=self._on_volume)
        self._vol_scale.set(self._volume)
        self._vol_scale.pack(side="left")

        # ---- 歌词区 (动态显示/隐藏) ----
        self._lyric_canvas = tk.Canvas(parent, bg=t.get("bg"), height=0,
                                        highlightthickness=0, bd=0, takefocus=0)
        self._lyric_canvas.grid(row=4, column=0, sticky="ew", padx=14)

        # ---- 标签页 (Notebook) ----
        self._notebook = ttk.Notebook(parent)
        self._notebook.grid(row=5, column=0, sticky="nsew", padx=14, pady=(8, 10))

        # 配置 Notebook 样式
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=t.get("bg"), borderwidth=0)
        style.configure("TNotebook.Tab", background=t.get("bg_toolbar"), foreground=t.get("fg"),
                        padding=[12, 4], font=font(size=10))
        style.map("TNotebook.Tab", background=[("selected", t.get("accent"))],
                  foreground=[("selected", "white")])

        # Tab 1: 本地音乐
        self._build_local_tab()
        # Tab 2: 在线音乐
        self._build_online_tab()
        # Tab 3: 插件管理
        self._build_plugins_tab()
        # Tab 4: 发现音乐
        self._build_discover_tab()

        self._draw_pb(0)
        self._draw_viz_frame()
        self._refresh_playlist()
        self._refresh_history()
        self._start_viz_loop()

    # ===== 本地音乐标签页 =====
    def _build_local_tab(self):
        t = self._palette
        tab = tk.Frame(self._notebook, bg=t.get("bg"))
        self._notebook.add(tab, text="  本地音乐  ")

        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(0, weight=0)
        tab.grid_rowconfigure(1, weight=1)

        # ---- 播放历史 (左) ----
        hl = tk.Frame(tab, bg=t.get("bg"))
        hl.grid(row=0, column=0, sticky="ew", padx=(0, 7), pady=(4, 0))
        tk.Label(hl, text="\U0001f4dc 播放历史", bg=t.get("bg"), fg=t.get("fg_dim"),
                 font=font(size=10, bold=True)).pack(side="left")

        hf = tk.Frame(tab, bg=t.get("bg_panel"),
                      highlightbackground=t.get("border"), highlightthickness=1)
        hf.grid(row=1, column=0, sticky="nsew", padx=(0, 7), pady=(2, 0))
        hf.grid_rowconfigure(0, weight=1)
        hf.grid_columnconfigure(0, weight=1)

        self._hist_list = tk.Listbox(hf, bg=t.get("bg_panel"), fg=t.get("fg"),
                                     selectbackground=t.get("accent"), selectforeground="white",
                                     font=font(size=10), relief="flat", highlightthickness=0,
                                     activestyle="none")
        self._hist_list.pack(fill="both", expand=True, padx=1, pady=1)
        self._hist_list.bind("<Double-Button-1>", self._on_history_dclick)

        # ---- 播放列表 (右) ----
        pl = tk.Frame(tab, bg=t.get("bg"))
        pl.grid(row=0, column=1, sticky="ew", padx=(7, 0), pady=(4, 0))
        tk.Label(pl, text="\U0001f3b5 播放列表", bg=t.get("bg"), fg=t.get("fg_dim"),
                 font=font(size=10, bold=True)).pack(side="left")
        self._pl_count = tk.Label(pl, text="(0)", bg=t.get("bg"), fg=t.get("fg_dim"),
                                  font=font(size=9))
        self._pl_count.pack(side="left", padx=4)

        pf = tk.Frame(tab, bg=t.get("bg_panel"),
                      highlightbackground=t.get("border"), highlightthickness=1)
        pf.grid(row=1, column=1, sticky="nsew", padx=(7, 0), pady=(2, 0))
        pf.grid_rowconfigure(0, weight=1)
        pf.grid_columnconfigure(0, weight=1)

        self._pl_list = tk.Listbox(pf, bg=t.get("bg_panel"), fg=t.get("fg"),
                                   selectbackground=t.get("accent"), selectforeground="white",
                                   font=font(size=10), relief="flat", highlightthickness=0,
                                   activestyle="none")
        self._pl_list.pack(fill="both", expand=True, padx=1, pady=1)
        self._pl_list.bind("<Double-Button-1>", self._on_playlist_dclick)

        # 底部按钮栏
        bf = tk.Frame(tab, bg=t.get("bg"))
        bf.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        for txt, cmd, clr, side in [
            ("\U0001f4c2 选择文件",   self._add_files,       t.get("fg"), "left"),
            ("\U0001f4c1 选择文件夹", self._add_folder,      t.get("fg"), "left"),
            ("\U0001f5d1 清空历史",   self._clear_history,   "#e55",     "right"),
            ("\U0001f5d1 清空列表",   self._clear_playlist,  "#e55",     "right"),
        ]:
            tk.Button(bf, text=txt, command=cmd, bg=t.get("bg_toolbar"), fg=clr,
                      relief="flat", font=font(size=10), cursor="hand2",
                      padx=8, pady=2).pack(side=side, padx=3)

    # ===== 在线音乐标签页 =====
    def _build_online_tab(self):
        t = self._palette
        tab = tk.Frame(self._notebook, bg=t.get("bg"))
        self._notebook.add(tab, text="  在线音乐  ")

        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=0)
        tab.grid_rowconfigure(1, weight=0)
        tab.grid_rowconfigure(2, weight=1)

        # 搜索栏
        search_f = tk.Frame(tab, bg=t.get("bg"))
        search_f.grid(row=0, column=0, sticky="ew", pady=(6, 4))

        self._search_entry = tk.Entry(search_f, bg=t.get("bg_input"), fg=t.get("fg"),
                                      insertbackground=t.get("fg"), relief="flat",
                                      font=font(size=11), bd=0, highlightthickness=0)
        self._search_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))
        self._search_entry.bind("<Return>", lambda e: self._do_online_search())
        self._search_entry.insert(0, "")

        tk.Button(search_f, text="\U0001f50d 搜索", bg=t.get("accent"), fg="white",
                  font=font(size=10), relief="flat", cursor="hand2", padx=14, pady=4,
                  command=self._do_online_search).pack(side="left")

        # 音源选择 + 热门
        src_f = tk.Frame(tab, bg=t.get("bg"))
        src_f.grid(row=1, column=0, sticky="ew", pady=(0, 4))

        platforms = self._plugin_mgr.get_platforms()
        self._src_var = tk.StringVar(value=platforms[0] if platforms else "全部")
        self._src_combo = ttk.Combobox(src_f, textvariable=self._src_var,
                                        values=["全部"] + platforms,
                                        state="readonly", font=font(size=10), width=12)
        self._src_combo.pack(side="left")
        self._src_combo.bind("<<ComboboxSelected>>", lambda e: self._do_online_search())

        tk.Button(src_f, text="\U0001f525 热门推荐", bg=t.get("bg_toolbar"), fg=t.get("fg"),
                  font=font(size=10), relief="flat", cursor="hand2", padx=10, pady=3,
                  command=self._load_hot_list).pack(side="left", padx=8)

        self._search_status = tk.Label(src_f, text="", bg=t.get("bg"), fg=t.get("fg_dim"),
                                        font=font(size=9))
        self._search_status.pack(side="right")

        # 搜索结果列表
        res_f = tk.Frame(tab, bg=t.get("bg_panel"),
                         highlightbackground=t.get("border"), highlightthickness=1)
        res_f.grid(row=2, column=0, sticky="nsew")
        res_f.grid_rowconfigure(0, weight=1)
        res_f.grid_columnconfigure(0, weight=1)

        self._online_list = tk.Listbox(res_f, bg=t.get("bg_panel"), fg=t.get("fg"),
                                       selectbackground=t.get("accent"), selectforeground="white",
                                       font=font(size=10), relief="flat", highlightthickness=0,
                                       activestyle="none")
        self._online_list.pack(fill="both", expand=True, padx=1, pady=1)
        self._online_list.bind("<Double-Button-1>", self._on_online_dclick)

    # ===== 插件管理标签页 =====
    def _build_plugins_tab(self):
        t = self._palette
        tab = tk.Frame(self._notebook, bg=t.get("bg"))
        self._notebook.add(tab, text="  插件管理  ")

        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=0)
        tab.grid_rowconfigure(1, weight=1)

        # 顶部按钮
        btn_f = tk.Frame(tab, bg=t.get("bg"))
        btn_f.grid(row=0, column=0, sticky="ew", pady=(6, 4))

        for txt, cmd in [
            ("\U0001f4c2 从本地安装", self._install_plugin_local),
            ("\U0001f310 从URL安装",  self._install_plugin_url),
            ("\U0001f4e6 插件市场",    self._open_marketplace),
            ("\U0001f504 刷新列表",    self._refresh_plugins_ui),
        ]:
            tk.Button(btn_f, text=txt, command=cmd, bg=t.get("bg_toolbar"), fg=t.get("fg"),
                      font=font(size=10), relief="flat", cursor="hand2",
                      padx=10, pady=3).pack(side="left", padx=3)

        # 状态提示
        self._plugins_status = tk.Label(btn_f, text="", bg=t.get("bg"), fg=t.get("fg_dim"),
                                         font=font(size=9))
        self._plugins_status.pack(side="right")

        # 插件列表
        pl_f = tk.Frame(tab, bg=t.get("bg_panel"),
                        highlightbackground=t.get("border"), highlightthickness=1)
        pl_f.grid(row=1, column=0, sticky="nsew")
        pl_f.grid_rowconfigure(0, weight=1)
        pl_f.grid_columnconfigure(0, weight=1)

        self._plugins_list = tk.Listbox(pl_f, bg=t.get("bg_panel"), fg=t.get("fg"),
                                         selectbackground=t.get("accent"),
                                         selectforeground="white",
                                         font=font(size=10), relief="flat", highlightthickness=0,
                                         activestyle="none")
        self._plugins_list.pack(fill="both", expand=True, padx=1, pady=1)

        # 右键菜单
        self._plugins_menu = tk.Menu(self._plugins_list, tearoff=0,
                                      bg=t.get("bg_panel"), fg=t.get("fg"),
                                      font=font(size=10),
                                      activebackground=t.get("accent"),
                                      activeforeground="white")
        self._plugins_menu.add_command(label="\u2705 启用/禁用", command=self._toggle_plugin)
        self._plugins_menu.add_command(label="\U0001f4e4 导出插件", command=self._export_plugin)
        self._plugins_menu.add_separator()
        self._plugins_menu.add_command(label="\U0001f5d1 卸载插件", command=self._uninstall_plugin)
        self._plugins_list.bind("<Button-3>", self._on_plugin_right_click)

        self._refresh_plugins_ui()

    # ===== 发现音乐标签页（MusicFree原生体验）=====
    def _build_discover_tab(self):
        t = self._palette
        tab = tk.Frame(self._notebook, bg=t.get("bg"))
        self._notebook.add(tab, text="  发现  ")

        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=0)
        tab.grid_rowconfigure(1, weight=1)

        top_f = tk.Frame(tab, bg=t.get("bg"))
        top_f.grid(row=0, column=0, sticky="ew", pady=(6, 4))

        self._discover_btn = tk.Button(top_f, text="\U0001f31f 加载推荐", bg=t.get("accent"),
                                        fg="white", font=font(size=10), relief="flat",
                                        cursor="hand2", padx=14, pady=4,
                                        command=self._load_discover)
        self._discover_btn.pack(side="left")

        tk.Button(top_f, text="\U0001f4e6 插件市场", bg=t.get("bg_toolbar"), fg=t.get("fg"),
                  font=font(size=10), relief="flat", cursor="hand2", padx=10, pady=3,
                  command=self._open_marketplace).pack(side="left", padx=6)

        self._discover_status = tk.Label(top_f, text="点击加载推荐发现好音乐",
                                         bg=t.get("bg"), fg=t.get("fg_dim"), font=font(size=9))
        self._discover_status.pack(side="right")

        container = tk.Frame(tab, bg=t.get("bg_panel"),
                             highlightbackground=t.get("border"), highlightthickness=1)
        container.grid(row=1, column=0, sticky="nsew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self._discover_canvas = tk.Canvas(container, bg=t.get("bg_panel"),
                                          highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(container, orient="vertical", command=self._discover_canvas.yview)
        self._discover_inner = tk.Frame(self._discover_canvas, bg=t.get("bg_panel"))

        self._discover_inner.bind("<Configure>",
            lambda e: self._discover_canvas.configure(scrollregion=self._discover_canvas.bbox("all")))
        self._discover_canvas.create_window((0, 0), window=self._discover_inner, anchor="nw", tags="inner")
        self._discover_canvas.configure(yscrollcommand=scrollbar.set)

        self._discover_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._discover_canvas.bind("<MouseWheel>",
            lambda e: self._discover_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        self._discover_canvas.bind("<Enter>", lambda e: self._discover_canvas.focus_set())

        self._root_frame.after(300, self._load_discover)

    def _load_discover(self):
        """加载发现页：展示各插件的榜单和推荐。"""
        t = self._palette
        self._discover_status.config(text="加载中...", fg=t.get("accent"))
        self._root_frame.update_idletasks()

        for child in self._discover_inner.winfo_children():
            child.destroy()

        plugins = self._plugin_mgr.get_all_plugins()
        if not plugins:
            tk.Label(self._discover_inner, text="暂无音源插件，请先到「插件管理」安装",
                     bg=t.get("bg_panel"), fg=t.get("fg_dim"), font=font(size=11),
                     pady=30).pack()
            self._discover_status.config(text="无可用插件", fg=t.get("warning"))
            return

        def fetch_one(plugin):
            try:
                top = plugin.get_top_lists()
                return plugin.platform, top
            except Exception:
                return plugin.platform, []

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_one, p): p for p in plugins if p.enabled}
            for future in as_completed(futures):
                try:
                    platform, top_lists = future.result()
                    if top_lists:
                        self._root_frame.after(0, lambda p=platform, tl=top_lists:
                            self._add_discover_section(p, tl))
                except Exception:
                    pass

        self._discover_status.config(text="发现音乐  MusicFree原生引擎", fg=t.get("success"))

    def _add_discover_section(self, platform: str, top_lists: list):
        """在发现页添加一个音源的歌单区块。歌单为第一级，点击进入歌曲列表。"""
        t = self._palette
        if not top_lists:
            return
        section = tk.Frame(self._discover_inner, bg=t.get("bg_panel"))
        section.pack(fill="x", padx=12, pady=(8, 2))

        tk.Label(section, text=f"\U0001f3b5 {platform}", bg=t.get("bg_panel"),
                 fg=t.get("accent"), font=font(size=12, bold=True)).pack(anchor="w", pady=(4, 6))

        for group in top_lists:
            title = group.get("title", "") if isinstance(group, dict) else ""
            items = group.get("data", []) if isinstance(group, dict) else (group if isinstance(group, list) else [])
            if not items:
                continue
            if title:
                tk.Label(section, text=title, bg=t.get("bg_panel"), fg=t.get("fg_dim"),
                         font=font(size=10, bold=True)).pack(anchor="w", pady=(4, 0))

            sheets_f = tk.Frame(section, bg=t.get("bg_panel"))
            sheets_f.pack(fill="x", pady=(2, 6))

            for i, sheet in enumerate(items[:10]):
                sheet_title = sheet.get("title") or sheet.get("name") or f"歌单{i+1}"
                desc = sheet.get("description") or sheet.get("desc") or ""
                if desc and len(desc) > 30:
                    desc = desc[:30] + "..."
                # 歌单行：标题 + 描述 + 箭头
                display = f"  \U0001f4c4 {sheet_title}"
                if desc:
                    display += f"  —  {desc}"
                display += "  \u276f"

                lbl = tk.Label(sheets_f, text=display, bg=t.get("bg_panel"), fg=t.get("fg"),
                               font=font(size=10), anchor="w", cursor="hand2")
                lbl.pack(fill="x", pady=1)
                lbl.bind("<Button-1>", lambda e, s=sheet, p=platform: self._open_sheet_detail(p, s))
                lbl.bind("<Enter>", lambda e, l=lbl: l.config(fg=t.get("accent")))
                lbl.bind("<Leave>", lambda e, l=lbl: l.config(fg=t.get("fg")))

        tk.Frame(self._discover_inner, bg=t.get("border"), height=1).pack(fill="x", padx=12, pady=2)

    def _open_sheet_detail(self, platform: str, sheet_item: dict):
        """打开歌单详情弹窗，显示歌曲列表（第二级），点击可播放。"""
        t = self._palette
        plugin = self._plugin_mgr.get_plugin(platform)
        if not plugin:
            return

        sheet_name = sheet_item.get("title") or sheet_item.get("name") or "歌单详情"
        desc = sheet_item.get("description") or sheet_item.get("desc") or ""

        win = tk.Toplevel(self._root_frame)
        win.title(f"{sheet_name} — {platform}")
        win.geometry("520x480")
        win.configure(bg=t.get("bg"))
        win.transient(self._root_frame)
        win.grab_set()
        win.minsize(400, 300)

        # 顶部信息
        header = tk.Frame(win, bg=t.get("bg"))
        header.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(header, text=sheet_name, bg=t.get("bg"), fg=t.get("fg"),
                 font=font(size=13, bold=True)).pack(anchor="w")
        if desc:
            tk.Label(header, text=desc, bg=t.get("bg"), fg=t.get("fg_dim"),
                     font=font(size=9), wraplength=480, justify="left").pack(anchor="w", pady=(2, 0))
        tk.Label(header, text=f"音源: {platform}", bg=t.get("bg"), fg=t.get("fg_dim"),
                 font=font(size=8)).pack(anchor="w", pady=(2, 0))

        # 状态栏
        status_lbl = tk.Label(win, text="加载中...", bg=t.get("bg"), fg=t.get("accent"),
                              font=font(size=9))
        status_lbl.pack(fill="x", padx=16, pady=(2, 4))

        # 歌曲列表
        list_frame = tk.Frame(win, bg=t.get("bg_panel"),
                              highlightbackground=t.get("border"), highlightthickness=1)
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        lb = tk.Listbox(list_frame, bg=t.get("bg_panel"), fg=t.get("fg"),
                        selectbackground=t.get("accent"), selectforeground="white",
                        font=font(size=10), relief="flat", highlightthickness=0,
                        activestyle="none")
        lb.pack(fill="both", expand=True, padx=1, pady=1)
        lb.insert("end", "  加载中，请稍候...")

        # 按播放键或双击播放
        def play_song(idx: int):
            if idx < 0 or idx >= len(songs_cache):
                return
            song = songs_cache[idx]
            song["_platform"] = platform
            source = plugin.get_media_source(song)
            if source and source.get("url"):
                self._download_and_play(source["url"], song)
            else:
                messagebox.showwarning("播放失败", f"无法获取 {platform} 音源地址")

        lb.bind("<Double-Button-1>", lambda e: play_song(lb.curselection()[0] if lb.curselection() else -1))
        lb.bind("<Return>", lambda e: play_song(lb.curselection()[0] if lb.curselection() else -1))

        songs_cache: list[dict] = []

        # 后台加载歌曲
        def do_load():
            nonlocal songs_cache
            try:
                result = plugin.get_top_list_detail(sheet_item)
                songs_cache = result.get("musicList", [])
                for s in songs_cache:
                    s["_platform"] = platform
            except Exception as e:
                print(f"[Sheet] 加载歌单失败: {e}")
                songs_cache = []

            if win.winfo_exists():
                win.after(0, lambda: _on_loaded())

        def _on_loaded():
            lb.delete(0, "end")
            if not songs_cache:
                lb.insert("end", "  该歌单暂无歌曲或加载失败")
                status_lbl.config(text="加载失败", fg=t.get("danger"))
                return
            for i, song in enumerate(songs_cache):
                song_title = song.get("title") or song.get("name") or song.get("songname") or f"曲目{i+1}"
                artist = song.get("artist") or ""
                if not artist:
                    ar = song.get("ar") or song.get("artists") or []
                    if isinstance(ar, list) and ar:
                        artist = "/".join(
                            a.get("name", str(a)) if isinstance(a, dict) else str(a)
                            for a in ar[:2])
                display = f"  {i+1:2d}. {song_title}"
                if artist:
                    display += f"  —  {artist}"
                lb.insert("end", display)
            status_lbl.config(text=f"共 {len(songs_cache)} 首", fg=t.get("success"))

        import threading
        threading.Thread(target=do_load, daemon=True).start()

    def _open_marketplace(self):
        """打开插件市场对话框。"""
        t = self._palette
        self._discover_status.config(text="正在获取插件市场...", fg=t.get("accent"))
        self._root_frame.update_idletasks()

        plugins_list = self._plugin_mgr.fetch_marketplace()
        if not plugins_list:
            self._discover_status.config(text="获取市场失败，请检查网络", fg=t.get("danger"))
            return

        installed = set(self._plugin_mgr.get_platforms())

        win = tk.Toplevel(self._root_frame)
        win.title("插件市场  MusicFree")
        win.geometry("600x420")
        win.configure(bg=t.get("bg"))
        win.transient(self._root_frame)
        win.grab_set()

        tk.Label(win, text="\U0001f4e6 MusicFree 插件市场",
                 bg=t.get("bg"), fg=t.get("fg"), font=font(size=14, bold=True)).pack(pady=(12, 4))
        tk.Label(win, text=f"共 {len(plugins_list)} 个可用插件 | 已安装 {len(installed)} 个",
                 bg=t.get("bg"), fg=t.get("fg_dim"), font=font(size=9)).pack()

        list_frame = tk.Frame(win, bg=t.get("bg_panel"),
                              highlightbackground=t.get("border"), highlightthickness=1)
        list_frame.pack(fill="both", expand=True, padx=16, pady=(8, 12))

        lb = tk.Listbox(list_frame, bg=t.get("bg_panel"), fg=t.get("fg"),
                        selectbackground=t.get("accent"), selectforeground="white",
                        font=font(size=10), relief="flat", highlightthickness=0,
                        activestyle="none")
        lb.pack(fill="both", expand=True, padx=1, pady=1)

        for p in plugins_list:
            pname = p.get("platform") or p.get("name", "未知")
            ver = p.get("version", "0.0.0")
            author = p.get("author", "")
            status = "\u2705" if pname in installed else "\u2b07"
            lb.insert("end", f"  {status}  {pname}  v{ver}  —  {author}")

        def install_selected():
            sel = lb.curselection()
            if not sel:
                return
            idx = sel[0]
            if idx >= len(plugins_list):
                return
            entry = plugins_list[idx]
            pname = entry.get("platform") or entry.get("name", "")
            if pname in installed:
                if not messagebox.askyesno("更新插件", f"'{pname}' 已安装，是否重新安装？"):
                    return
            ok, msg = self._plugin_mgr.install_from_market(entry)
            if ok:
                installed.add(pname)
                lb.delete(idx)
                lb.insert(idx, f"  \u2705  {pname}  v{entry.get('version','0.0.0')}  —  {entry.get('author','')}")
                messagebox.showinfo("安装结果", msg)
            else:
                messagebox.showerror("安装失败", msg)
            self._root_frame.after(100, self._refresh_plugins_ui)

        btn_f = tk.Frame(win, bg=t.get("bg"))
        btn_f.pack(pady=(0, 12))
        tk.Button(btn_f, text="\U0001f4e5 安装选中", bg=t.get("accent"), fg="white",
                  font=font(size=11), relief="flat", cursor="hand2", padx=16, pady=5,
                  command=install_selected).pack(side="left", padx=4)
        tk.Button(btn_f, text="关闭", bg=t.get("bg_toolbar"), fg=t.get("fg"),
                  font=font(size=11), relief="flat", cursor="hand2", padx=12, pady=5,
                  command=win.destroy).pack(side="left", padx=4)

        self._discover_status.config(text=f"插件市场 {len(plugins_list)} 个可用", fg=t.get("success"))

    # ===== 在线搜索 =====
    def _do_online_search(self, page: int = 1):
        """后台线程执行在线搜索。"""
        keyword = self._search_entry.get().strip()
        if not keyword:
            self._load_hot_list()
            return

        t = self._palette
        self._search_status.config(text="搜索中...", fg=t.get("accent"))
        src = self._src_var.get()

        def _run():
            if src == "全部":
                results = self._plugin_mgr.search_all(keyword, page)
            else:
                plugin = self._plugin_mgr.get_plugin(src)
                if plugin:
                    sr = plugin.search(keyword, page)
                    results = sr.get("data", []) if isinstance(sr, dict) else sr
                else:
                    results = []

            if self._root_frame:
                self._root_frame.after(0, lambda r=results: self._on_search_result(r))

        threading.Thread(target=_run, daemon=True).start()

    def _on_search_result(self, results: list):
        """搜索结果回调（主线程）。"""
        t = self._palette
        if results:
            self._online_results = results
            self._display_online_results()
            self._search_status.config(text=f"共 {len(results)} 个结果", fg=t.get("success"))
        else:
            self._online_list.delete(0, "end")
            self._online_list.insert("end", "  未找到结果，请尝试其他关键词")
            self._online_results.clear()
            self._search_status.config(text="未找到结果", fg=t.get("warning"))

    def _load_hot_list(self):
        """后台线程加载热门歌曲。"""
        t = self._palette
        self._search_status.config(text="加载中...", fg=t.get("accent"))
        src = self._src_var.get()

        def _run():
            if src == "全部":
                plugins = self._plugin_mgr.get_all_plugins()
            else:
                p = self._plugin_mgr.get_plugin(src)
                plugins = [p] if p else []

            all_items = []
            for plugin in plugins:
                if not plugin.enabled:
                    continue
                items = plugin.get_hot_list()
                for item in items:
                    item["_platform"] = plugin.platform
                if items:
                    all_items.extend(items)
                    break

            if self._root_frame:
                self._root_frame.after(0, lambda r=all_items: self._on_hot_list_result(r))

        threading.Thread(target=_run, daemon=True).start()

    def _on_hot_list_result(self, all_items: list):
        """热门列表结果回调（主线程）。"""
        t = self._palette
        if all_items:
            self._online_results = all_items
            self._display_online_results()
            self._search_status.config(text=f"热门推荐 {len(all_items)} 首", fg=t.get("success"))
        else:
            self._online_list.delete(0, "end")
            self._online_list.insert("end", "  热门列表加载失败，请检查网络")
            self._search_status.config(text="加载失败", fg=t.get("danger"))

    def _display_online_results(self):
        """在在线音乐列表中显示搜索结果。"""

        self._online_list.delete(0, "end")
        for i, item in enumerate(self._online_results):
            # 使用标准化后的字段
            title = item.get("title") or item.get("name") or item.get("songname") or f"未知曲目{i+1}"
            artist = item.get("artist") or ""
            if not artist:
                artists = item.get("ar") or item.get("artists") or item.get("singer") or []
                if isinstance(artists, list):
                    artist = " / ".join(
                        a.get("name", "") if isinstance(a, dict) else str(a)
                        for a in artists[:3]
                    )
                elif isinstance(artists, str):
                    artist = artists
                else:
                    artist = "未知艺术家"
            platform = item.get("_platform", "")
            album = item.get("al", {}) if isinstance(item.get("al"), dict) else {}
            album_name = album.get("name", "") if isinstance(album, dict) else ""
            display = f"  {title}  —  {artist}"
            if album_name:
                display += f"  [{album_name}]"
            if platform:
                display += f"  [{platform}]"
            self._online_list.insert("end", display)

    def _on_online_dclick(self, e):
        """双击在线搜索结果 获取播放URL并播放。"""
        sel = self._online_list.curselection()
        if not sel or not self._online_results:
            return
        idx = sel[0]
        if idx >= len(self._online_results):
            return

        item = self._online_results[idx]
        platform_name = item.get("_platform", "")
        plugin = self._plugin_mgr.get_plugin(platform_name)

        if plugin:
            t = self._palette
            self._search_status.config(text="正在获取播放地址...", fg=t.get("accent"))
            self._root_frame.update_idletasks()

            source = plugin.get_media_source(item)
            if source and source.get("url"):
                self._search_status.config(text="正在缓冲播放...", fg=t.get("success"))
                self._download_and_play(source["url"], item)
            else:
                self._search_status.config(text="无法获取播放地址", fg=t.get("danger"))
        else:
            messagebox.showwarning("播放失败", f"插件 '{platform_name}' 不可用")

    def _download_and_play(self, url: str, item: dict):
        """下载流媒体文件到临时目录并播放。"""
        t = self._palette
        title = item.get("name") or item.get("songname") or item.get("title") or "在线音乐"

        # 保存当前歌曲信息（供歌词系统使用）
        self._current_song_item = dict(item)
        self._current_plugin_name = item.get("_platform", "")

        # 校验URL有效性
        if not url or not isinstance(url, str) or not url.startswith(("http://", "https://")):
            print(f"[MusicPlayer] 无效播放地址: {url!r}")
            if self._root_frame:
                self._root_frame.after(0, lambda msg="无效播放地址": self._search_status.config(
                    text=msg, fg=self._palette.get("danger")))
            return

        # 确定文件扩展名
        ext = ".mp3"
        url_lower = url.lower()
        for e in [".flac", ".m4a", ".ogg", ".wav", ".mp3", ".wma"]:
            if e in url_lower:
                ext = e
                break

        temp_fp = os.path.join(self._temp_dir, f"{hashlib.md5(url.encode()).hexdigest()[:12]}{ext}")

        if os.path.exists(temp_fp):
            # 已缓存，直接播放
            self._stop_mci()
            self._fp = temp_fp
            self._stream_fp = temp_fp
            self._do_play_with_title(title)
            return

        # 后台下载
        threading.Thread(target=self._bg_download, args=(url, temp_fp, title), daemon=True).start()

    def _bg_download(self, url: str, temp_fp: str, title: str):
        """后台线程下载音乐文件。"""
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                with open(temp_fp, "wb") as f:
                    total = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0 and downloaded % (256 * 1024) < 65536:
                            pct = int(downloaded / total * 100)
                            try:
                                if self._root_frame:
                                    self._root_frame.after(0, lambda p=pct: self._search_status.config(
                                        text=f"下载中... {p}%", fg=self._palette.get("accent")))
                            except Exception:
                                pass

            # 下载完成，播放
            if self._root_frame:
                self._root_frame.after(0, lambda: self._on_download_complete(temp_fp, title))
        except Exception as e:
            err_msg = str(e)[:80]
            print(f"[MusicPlayer] 下载失败: {err_msg}")
            if self._root_frame:
                self._root_frame.after(0, lambda msg=err_msg: self._search_status.config(
                    text=f"下载失败: {msg}", fg=self._palette.get("danger")))

    def _on_download_complete(self, temp_fp: str, title: str):
        """下载完成后切换到播放。"""
        self._search_status.config(text="下载完成，开始播放", fg=self._palette.get("success"))
        self._stop_mci()
        self._fp = temp_fp
        self._stream_fp = temp_fp
        self._do_play_with_title(title)

    def _do_play_with_title(self, title: str):
        """播放并设置自定义标题（用于在线音乐）。"""
        if not self._fp or not os.path.exists(self._fp):
            return
        self._stop_mci()
        try:
            ext = os.path.splitext(self._fp)[1].lower()
            if ext == ".wav":
                mci_type = "waveaudio"
            else:
                mci_type = "mpegvideo"

            cmd = f'open "{self._fp}" type {mci_type} alias {self.MCI_ALIAS}'
            if self._mci(cmd) != 0:
                self._mci(f'open "{self._fp}" alias {self.MCI_ALIAS}')

            self._mci(f"setaudio {self.MCI_ALIAS} volume to {self._volume}")
            self._mci(f"play {self.MCI_ALIAS}")

            s = self._mci_str(f"status {self.MCI_ALIAS} length")
            self._total_ms = int(s) if s.isdigit() else 0
        except Exception:
            try:
                os.startfile(self._fp)
            except Exception:
                self._lbl.config(text="播放失败")
                return

        self._playing = True
        self._paused = False
        self._pp_btn.config(text="\u23f8")
        self._lbl.config(text=title)
        self._time_lbl.config(text=f"--:-- / {self._ms_to_str(self._total_ms)}")
        self._hide_lyrics()
        self._fetch_lyrics_async()
        self._start_progress_poll()

    # ===== 歌词系统 (MusicFree 同款 LRC 同步歌词) =====
    @staticmethod
    def _parse_lrc(lrc_text: str) -> list[tuple[float, str]]:
        """解析 LRC 歌词格式 → [(time_ms, text), ...]，按时间排序。"""
        import re
        lines = []
        # 匹配 [mm:ss.xx] 或 [mm:ss] 格式
        tag_re = re.compile(r'^\[(\d{1,3}):(\d{2}(?:\.\d+)?)\](.*)')
        # 也支持多标签行 [00:01.00][00:02.00]text
        multi_re = re.compile(r'\[(\d{1,3}):(\d{2}(?:\.\d+)?)\]')
        for raw in lrc_text.strip().split('\n'):
            raw = raw.strip()
            if not raw:
                continue
            tags = list(multi_re.finditer(raw))
            texts = multi_re.split(raw)
            # 最后一个元素是去掉所有标签后的文本
            text = texts[-1].strip() if len(texts) > 1 else ''
            if not text and tags:
                text = multi_re.sub('', raw).strip()
            for m in tags:
                minutes = int(m.group(1))
                seconds = float(m.group(2))
                time_ms = (minutes * 60 + seconds) * 1000
                lines.append((time_ms, text))
        # 按时间排序
        lines.sort(key=lambda x: x[0])
        return lines

    def _fetch_lyrics_async(self):
        """后台线程获取歌词。"""
        item = self._current_song_item
        if not item or self._lyric_fetching:
            return
        self._lyric_fetching = True
        import threading
        threading.Thread(target=self._do_fetch_lyrics, args=(item,), daemon=True).start()

    def _do_fetch_lyrics(self, item: dict):
        """在线程中获取歌词并解析。"""
        try:
            platform = item.get("_platform", "")
            if not platform:
                return
            plugin = self._plugin_mgr.get_plugin(platform)
            if not plugin:
                return

            # 构造标准化的 MusicFree songItem
            song_item = {
                "id": str(item.get("id") or item.get("songid") or item.get("song_id") or ""),
                "name": str(item.get("name") or item.get("title") or item.get("songname") or ""),
                "artist": str(item.get("artist") or ""),
                "songmid": str(item.get("songmid") or item.get("mid") or ""),
            }
            # 传递所有额外字段
            for k, v in item.items():
                if k not in song_item and not k.startswith("_"):
                    song_item[k] = v

            lrc_result = plugin.get_lyric(song_item)
            if not lrc_result:
                if self._root_frame:
                    self._root_frame.after(0, self._on_no_lyrics)
                return

            raw_lrc = lrc_result.get("rawLrc", "")
            trans = lrc_result.get("translation", "")
            lrc_lines = self._parse_lrc(raw_lrc) if raw_lrc else []
            trans_lines = self._parse_lrc(trans) if trans else []

            if self._root_frame:
                self._root_frame.after(0, lambda: self._on_lyrics_loaded(lrc_lines, trans_lines))

        except Exception as e:
            print(f"[Lyrics] 获取歌词失败: {e}")
            if self._root_frame:
                self._root_frame.after(0, self._on_no_lyrics)
        finally:
            self._lyric_fetching = False

    def _on_no_lyrics(self):
        """无歌词时显示提示。"""
        self._lrc_lines = []
        self._lrc_trans = []
        self._current_lrc_idx = -1
        self._has_lyrics = False
        self._show_lyric_placeholder("暂无歌词")

    def _on_lyrics_loaded(self, lrc_lines: list, trans_lines: list):
        """主线程：歌词加载成功，显示歌词。"""
        self._lrc_lines = lrc_lines
        self._lrc_trans = trans_lines
        self._current_lrc_idx = -1
        self._lyric_scroll = 0.0
        if lrc_lines:
            self._has_lyrics = True
            self._draw_lyrics()
        else:
            self._has_lyrics = False
            self._show_lyric_placeholder("暂无歌词")

    def _show_lyric_placeholder(self, text: str):
        """显示歌词占位提示。"""
        if not self._lyric_canvas:
            return
        t = self._palette
        c = self._lyric_canvas
        c.configure(height=60)
        c.delete("all")
        w = c.winfo_width()
        if w < 50:
            w = 800
        c.create_text(w // 2, 30, text=text,
                       fill=t.get("fg_dim"), font=font(size=11), anchor="center", tags="placeholder")

    def _hide_lyrics(self):
        """清空歌词并收起歌词区。"""
        self._lrc_lines = []
        self._lrc_trans = []
        self._current_lrc_idx = -1
        self._has_lyrics = False
        if self._lyric_canvas:
            self._lyric_canvas.delete("all")
            self._lyric_canvas.configure(height=0)

    def _draw_lyrics(self, pos_ms: int | None = None):
        """在 Canvas 上绘制滚动歌词（MusicFree 风格）。
        
        Args:
            pos_ms: 当前播放位置(milliseconds)，None 时自行从 MCI 读取。
        """
        if not self._lyric_canvas or not self._has_lyrics:
            return
        t = self._palette
        c = self._lyric_canvas
        w = c.winfo_width()
        if w < 50:
            w = 800
        c.delete("all")

        LYRIC_H = 160
        LINE_H = 32
        CENTER_Y = LYRIC_H // 2
        c.configure(height=LYRIC_H)

        # 背景区域
        c.create_rectangle(0, 0, w, LYRIC_H, fill=t.get("bg"), outline="", tags="bg")

        # 渐隐上下边界
        for i in range(20):
            alpha = i / 20
            color = self._blend_color(t.get("bg"), t.get("bg_toolbar"), alpha)
            c.create_rectangle(0, LYRIC_H - 20 + i, w, LYRIC_H - 20 + i + 1,
                               fill=color, outline="", tags="fade")
            c.create_rectangle(0, i, w, i + 1,
                               fill=color, outline="", tags="fade")

        if not self._lrc_lines:
            c.create_text(w // 2, CENTER_Y, text="暂无歌词",
                           fill=t.get("fg_dim"), font=font(size=11), anchor="center")
            return

        # 计算当前播放位置（优先使用传入的 pos_ms，避免重复 MCI 查询）
        if pos_ms is None and self._playing and self._total_ms > 0:
            try:
                s = self._mci_str(f"status {self.MCI_ALIAS} position")
                if s.isdigit():
                    pos_ms = int(s)
            except Exception:
                pass
        if pos_ms is None:
            pos_ms = 0

        # 找到当前应高亮的行（二分查找优化）
        cur_idx = -1
        for i, (tms, _) in enumerate(self._lrc_lines):
            if pos_ms >= tms:
                cur_idx = i
            else:
                break
        self._current_lrc_idx = cur_idx

        # 每行的固定 Y 位置：第 i 行画在 CENTER_Y + (i - cur_idx) * LINE_H
        # 即当前行居中，前行在上方，后行在下方
        for i in range(len(self._lrc_lines)):
            y = CENTER_Y + (i - cur_idx) * LINE_H

            # 超出可视范围跳过
            if y < -LINE_H or y > LYRIC_H + LINE_H:
                continue

            tms, text = self._lrc_lines[i]
            if not text:
                continue

            # 根据距离中心的远近调节样式
            dist = abs(i - cur_idx)
            if dist == 0:
                color = t.get("accent")
                font_sz = 14
                bold = True
            elif dist == 1:
                color = t.get("fg")
                font_sz = 11
                bold = False
            elif dist <= 3:
                color = t.get("fg_dim")
                font_sz = 10
                bold = False
            else:
                continue

            c.create_text(w // 2, y, text=text, fill=color,
                           font=font(size=font_sz, bold=bold), anchor="center",
                           tags=f"lrc_{i}")

            # 如果有对应的翻译行（仅当前行显示翻译）
            if dist == 0 and self._lrc_trans:
                closest_trans = None
                for ttms, ttxt in reversed(self._lrc_trans):
                    if ttms <= tms:
                        closest_trans = ttxt
                        break
                if closest_trans:
                    c.create_text(w // 2, y + LINE_H - 4, text=closest_trans,
                                   fill=t.get("fg_dim"), font=font(size=9),
                                   anchor="center", tags=f"trans_{i}")

    def _update_lyrics(self, pos_ms: int | None = None):
        """歌词定时刷新（由进度轮询驱动）。
        
        Args:
            pos_ms: 当前播放位置，None 时自行从 MCI 读取。
        """
        if self._has_lyrics and self._lyric_canvas:
            self._draw_lyrics(pos_ms)

    @staticmethod
    def _blend_color(c1: str, c2: str, ratio: float) -> str:
        """混合两个十六进制颜色。"""
        try:
            r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
            r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
            r = int(r1 + (r2 - r1) * ratio)
            g = int(g1 + (g2 - g1) * ratio)
            b = int(b1 + (b2 - b1) * ratio)
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return c1

    # ===== 插件管理方法 =====
    def _refresh_plugins_ui(self):
        """刷新插件管理界面。"""
        self._plugins_list.delete(0, "end")
        plugins = self._plugin_mgr.get_all_plugins()
        if not plugins:
            self._plugins_list.insert("end", "  暂无插件，请安装音乐源插件")
            if hasattr(self, '_src_combo') and self._src_combo:
                self._src_combo["values"] = ["全部"]
            if hasattr(self, '_plugins_status'):
                self._plugins_status.config(text="")
            return

        node_ok = MusicFreePluginManager._find_node() is not None
        for p in plugins:
            status = "\u2705" if p.enabled else "\u274c"
            tag = "[JS]" if getattr(p, '_is_js', False) else "[JSON]"
            note = "  (需Node.js)" if getattr(p, '_is_js', False) and not node_ok else ""
            self._plugins_list.insert("end",
                f"  {status}  {tag}  {p.platform}  v{p.version}  —  {p.author}{note}")

        if hasattr(self, '_src_combo') and self._src_combo:
            self._src_combo["values"] = ["全部"] + self._plugin_mgr.get_platforms()
        if hasattr(self, '_plugins_status'):
            self._plugins_status.config(text=f"{len(plugins)} 个插件已加载")

    def _install_plugin_local(self):
        """从本地文件安装插件（支持 .json 和 .js）。"""
        path = filedialog.askopenfilename(
            title="选择插件文件 (JSON 或 JS)",
            filetypes=[("插件文件", "*.json;*.js"), ("JSON插件", "*.json"), ("JS插件", "*.js"), ("所有文件", "*.*")])
        if not path:
            return
        ok, msg = self._plugin_mgr.install_from_file(path)
        if ok:
            messagebox.showinfo("安装成功", msg)
        else:
            messagebox.showerror("安装失败", msg)
        self._refresh_plugins_ui()

    def _install_plugin_url(self):
        """从URL安装插件。"""
        url = simpledialog.askstring("从URL安装", "请输入插件URL\n（以 .json 结尾）:", parent=self._root_frame)
        if not url:
            return
        if not url.startswith("http"):
            messagebox.showerror("错误", "请输入有效的 HTTP/HTTPS URL")
            return
        ok, msg = self._plugin_mgr.install_from_url(url)
        if ok:
            messagebox.showinfo("安装成功", msg)
        else:
            messagebox.showerror("安装失败", msg)
        self._refresh_plugins_ui()

    def _on_plugin_right_click(self, e):
        """插件列表右键菜单。"""
        try:
            idx = self._plugins_list.nearest(e.y)
            if idx >= 0:
                self._plugins_list.selection_clear(0, "end")
                self._plugins_list.selection_set(idx)
                self._plugins_menu.tk_popup(e.x_root, e.y_root)
        finally:
            self._plugins_menu.grab_release()

    def _uninstall_plugin(self):
        """卸载选中的插件。"""
        sel = self._plugins_list.curselection()
        if not sel:
            return
        idx = sel[0]
        plugins = self._plugin_mgr.get_all_plugins()
        if idx >= len(plugins):
            return
        platform = plugins[idx].platform
        if messagebox.askyesno("确认卸载", f"确定要卸载插件 '{platform}' 吗？"):
            ok, msg = self._plugin_mgr.uninstall(platform)
            if ok:
                messagebox.showinfo("卸载成功", msg)
            else:
                messagebox.showerror("卸载失败", msg)
            self._refresh_plugins_ui()

    def _toggle_plugin(self):
        """启用/禁用选中的插件。"""
        sel = self._plugins_list.curselection()
        if not sel:
            return
        idx = sel[0]
        plugins = self._plugin_mgr.get_all_plugins()
        if idx >= len(plugins):
            return
        p = plugins[idx]
        p.enabled = not p.enabled
        self._plugins_status.config(text=f"{p.platform} {'已启用' if p.enabled else '已禁用'}")
        self._refresh_plugins_ui()

    def _export_plugin(self):
        """导出选中的插件到文件。"""
        sel = self._plugins_list.curselection()
        if not sel:
            return
        idx = sel[0]
        plugins = self._plugin_mgr.get_all_plugins()
        if idx >= len(plugins):
            return
        p = plugins[idx]
        dest = filedialog.asksaveasfilename(
            title="导出插件",
            defaultextension=".json",
            initialfile=f"{p.platform}.json",
            filetypes=[("插件文件", "*.json"), ("所有文件", "*.*")])
        if not dest:
            return
        try:
            fpath = os.path.join(self._plugin_mgr.PLUGIN_DIR, f"{p.platform}.json")
            if os.path.exists(fpath):
                import shutil
                shutil.copy(fpath, dest)
                messagebox.showinfo("导出成功", f"插件已导出到:\n{dest}")
            else:
                messagebox.showwarning("导出失败", "找不到插件文件")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    # ===== 频谱可视化 ====
    def _draw_viz_frame(self):
        """绘制频谱的静态框架（底部基线）。"""
        try:
            self._viz.delete("all")
            w = self._viz.winfo_width()
            h = self._viz.winfo_height()
            if w < 10 or h < 3:
                return
            self._viz.create_line(0, h - 1, w, h - 1,
                                  fill=self._palette.get("border"), tags="base")
        except Exception:
            pass

    def _draw_viz_bars(self):
        """根据当前 _viz_heights 绘制频谱柱。"""
        try:
            self._viz.delete("bar")
            t = self._palette
            w = self._viz.winfo_width()
            h = self._viz.winfo_height()
            if w < 10 or h < 4:
                return
            n = self.VIZ_BARS
            bar_w = max(2, (w - n - 2) // n)
            gap = max(1, (w - bar_w * n) // (n + 1))
            accent = t.get("accent")  # e.g. "#3b82f6"
            x = gap
            for i in range(n):
                ht = max(2, self._viz_heights[i])
                y0 = h - 2 - ht
                y1 = h - 2
                # 渐变：顶部明亮，底部暗淡
                intensity = int(0.4 + 0.6 * (ht / self.VIZ_H))
                r = int(59 * intensity)
                g_val = int(130 * intensity)
                b_val = int(246 * intensity)
                color = f"#{r:02x}{g_val:02x}{b_val:02x}"
                self._viz.create_rectangle(x, y0, x + bar_w, y1,
                                           fill=color, outline="", tags="bar")
                x += bar_w + gap
        except Exception:
            pass

    def _update_viz(self):
        """更新频谱柱高度——播放时跳动，停止时渐降。"""
        active = self._playing and not self._paused
        for i in range(self.VIZ_BARS):
            if active:
                # 模拟频率分布：低频（左）偏高，高频（右）偏低 + 随机抖动
                base = self.VIZ_H * (0.3 + 0.5 * (1 - i / self.VIZ_BARS))
                # 加入节奏感：phase 偏移使相邻柱异步
                jitter = 0.4 * self.VIZ_H * abs(
                    (i * 0.73 + self._viz_phase * 0.15) % 2.0 - 1.0)
                target = base + jitter
                # 向 target 平滑移动
                self._viz_heights[i] += (target - self._viz_heights[i]) * 0.35
            else:
                # 逐渐降到底部
                self._viz_heights[i] += (2.0 - self._viz_heights[i]) * 0.15
        self._viz_phase += 1
        self._draw_viz_bars()

    def _start_viz_loop(self):
        """启动/重启频谱动画循环。"""
        if self._viz_id and self._root_frame:
            try:
                self._root_frame.after_cancel(self._viz_id)
            except Exception:
                pass
        self._viz_loop()

    def _viz_loop(self):
        if self._closed:
            return
        self._update_viz()
        self._auto_adjust_eq()
        if self._root_frame:
            self._viz_id = self._root_frame.after(50, self._viz_loop)

    # ===== 进度条绘制 =====
    def _draw_pb(self, ratio: float):
        try:
            self._pb.delete("all")
            w = self._pb.winfo_width()
            h = self._pb.winfo_height()
            if w < 4 or h < 4:
                return
            filled = int(w * ratio)
            self._pb.create_rectangle(0, 0, filled, h,
                                      fill=self._palette.get("accent"),
                                      outline="", tags="bar")
            cx = filled
            r = 4
            self._pb.create_oval(cx - r, h // 2 - r, cx + r, h // 2 + r,
                                 fill=self._palette.get("accent"),
                                 outline=self._palette.get("accent"), tags="knob")
        except Exception:
            pass

    def _pb_ratio_from_x(self, x: int) -> float:
        try:
            w = self._pb.winfo_width()
            return max(0.0, min(1.0, x / max(w, 1)))
        except Exception:
            return 0.0

    def _on_pb_click(self, e):
        self._seek_dragging = True
        self._draw_pb(self._pb_ratio_from_x(e.x))

    def _on_pb_drag(self, e):
        self._draw_pb(self._pb_ratio_from_x(e.x))

    def _on_pb_release(self, e):
        r = self._pb_ratio_from_x(e.x)
        if self._total_ms > 0 and self._fp:
            ms = int(self._total_ms * r)
            try:
                self._mci(f"seek {self.MCI_ALIAS} to {ms}")
                if self._playing and not self._paused:
                    self._mci(f"play {self.MCI_ALIAS} from {ms}")
            except Exception:
                pass
        self._seek_dragging = False
        self._draw_pb(r)

    # ===== 列表刷新 =====
    def _refresh_playlist(self):
        self._pl_list.delete(0, "end")
        for p in self._playlist:
            self._pl_list.insert("end", f"  {os.path.basename(p)}")
        self._pl_count.config(text=f"({len(self._playlist)})")
        if self._fp and self._fp in self._playlist:
            idx = self._playlist.index(self._fp)
            self._pl_list.selection_clear(0, "end")
            self._pl_list.selection_set(idx)
            self._pl_list.see(idx)

    def _refresh_history(self):
        self._hist_list.delete(0, "end")
        for p in self._history:
            self._hist_list.insert("end", f"  {os.path.basename(p)}")

    def _add_to_history(self, path: str):
        if path in self._history:
            self._history.remove(path)
        self._history.insert(0, path)
        if len(self._history) > 50:
            self._history = self._history[:50]
        self._refresh_history()

    # ===== 文件/文件夹添加 =====
    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择音频文件",
            filetypes=[("音频文件", "*.wav *.mp3 *.ogg *.flac *.wma *.m4a"), ("所有文件", "*.*")])
        if paths:
            for p in paths:
                if p not in self._playlist:
                    self._playlist.append(p)
            self._refresh_playlist()

    def _add_folder(self):
        d = filedialog.askdirectory(title="选择音乐文件夹")
        if not d:
            return
        exts = (".wav", ".mp3", ".ogg", ".flac", ".wma", ".m4a")
        for root, _, files in os.walk(d):
            for f in sorted(files):
                if f.lower().endswith(exts):
                    fp = os.path.join(root, f)
                    if fp not in self._playlist:
                        self._playlist.append(fp)
        self._refresh_playlist()

    def _clear_playlist(self):
        self._playlist.clear()
        self._refresh_playlist()

    def _clear_history(self):
        self._history.clear()
        self._refresh_history()

    # ===== 列表双击 =====
    def _on_playlist_dclick(self, e):
        sel = self._pl_list.curselection()
        if sel:
            self._stop_mci()
            self._fp = self._playlist[sel[0]]
            self._do_play()

    def _on_history_dclick(self, e):
        sel = self._hist_list.curselection()
        if sel:
            path = self._history[sel[0]]
            if os.path.exists(path):
                self._stop_mci()
                self._fp = path
                self._do_play()

    # ===== 播放控制 =====
    def _stop_mci(self):
        """停止 MCI 并取消进度轮询。"""
        if self._update_id:
            try:
                if self._root_frame:
                    self._root_frame.after_cancel(self._update_id)
            except Exception:
                pass
            self._update_id = None
        try:
            self._mci(f"close {self.MCI_ALIAS}")
        except Exception:
            pass

    def _do_play(self):
        """执行 MCI 播放。"""
        if not self._fp or not os.path.exists(self._fp):
            return
        # 本地歌曲无歌词
        self._current_song_item = None
        self._current_plugin_name = ""
        self._hide_lyrics()
        self._stop_mci()
        try:
            ext = os.path.splitext(self._fp)[1].lower()
            if ext == ".wav":
                mci_type = "waveaudio"
            elif ext in (".mp3", ".m4a", ".wma"):
                mci_type = "mpegvideo"
            else:
                mci_type = "mpegvideo"

            cmd = f'open "{self._fp}" type {mci_type} alias {self.MCI_ALIAS}'
            if self._mci(cmd) != 0:
                self._mci(f'open "{self._fp}" alias {self.MCI_ALIAS}')

            self._mci(f"setaudio {self.MCI_ALIAS} volume to {self._volume}")
            self._mci(f"play {self.MCI_ALIAS}")

            s = self._mci_str(f"status {self.MCI_ALIAS} length")
            self._total_ms = int(s) if s.isdigit() else 0
        except Exception:
            try:
                os.startfile(self._fp)
            except Exception:
                self._lbl.config(text="播放失败")
                return

        self._playing = True
        self._paused = False
        self._pp_btn.config(text="\u23f8")
        self._lbl.config(text=os.path.basename(self._fp))
        self._add_to_history(self._fp)
        self._refresh_playlist()
        self._start_progress_poll()

    def _toggle_play(self):
        if not self._fp:
            self._pick_and_play()
            return
        if self._playing and not self._paused:
            self._pause()
        elif self._paused:
            self._resume()
        else:
            self._do_play()

    def _pick_and_play(self):
        path = filedialog.askopenfilename(
            title="选择音频文件",
            filetypes=[("音频文件", "*.wav *.mp3 *.ogg *.flac *.wma *.m4a"), ("所有文件", "*.*")])
        if path:
            if path not in self._playlist:
                self._playlist.append(path)
            self._fp = path
            self._do_play()

    def _pause(self):
        try:
            self._mci(f"pause {self.MCI_ALIAS}")
            self._paused = True
            self._pp_btn.config(text="\u25b6")
        except Exception:
            pass

    def _resume(self):
        try:
            self._mci(f"resume {self.MCI_ALIAS}")
            self._paused = False
            self._pp_btn.config(text="\u23f8")
        except Exception:
            pass

    def _stop(self):
        self._stop_mci()
        self._playing = False
        self._paused = False
        self._pp_btn.config(text="\u25b6")
        try:
            self._lbl.config(text="已停止")
        except Exception:
            pass
        try:
            self._time_lbl.config(text="--:-- / --:--")
        except Exception:
            pass
        self._draw_pb(0)

    def _play_next(self):
        if not self._playlist:
            return
        if self._fp and self._fp in self._playlist:
            idx = (self._playlist.index(self._fp) + 1) % len(self._playlist)
        else:
            idx = 0
        self._stop_mci()
        self._fp = self._playlist[idx]
        self._do_play()

    def _play_prev(self):
        if not self._playlist:
            return
        if self._fp and self._fp in self._playlist:
            idx = (self._playlist.index(self._fp) - 1) % len(self._playlist)
        else:
            idx = len(self._playlist) - 1
        self._stop_mci()
        self._fp = self._playlist[idx]
        self._do_play()

    # ===== 进度轮询 =====
    def _start_progress_poll(self):
        if self._update_id and self._root_frame:
            try:
                self._root_frame.after_cancel(self._update_id)
            except Exception:
                pass
        self._poll_progress()

    def _poll_progress(self):
        if not self._playing or not self._fp or self._closed:
            return
        try:
            if not self._seek_dragging:
                s = self._mci_str(f"status {self.MCI_ALIAS} position")
                if s.isdigit():
                    pos = int(s)
                    if self._total_ms > 0:
                        ratio = pos / self._total_ms
                        self._draw_pb(ratio)
                        self._time_lbl.config(
                            text=f"{self._ms_to_str(pos)} / {self._ms_to_str(self._total_ms)}")
                    # 歌词同步：每次轮询都刷新，传入本次读取的 pos 避免二次 MCI 查询导致漂移
                    if self._has_lyrics and not self._seek_dragging:
                        self._update_lyrics(pos)
            mode = self._mci_str(f"status {self.MCI_ALIAS} mode")
            if mode == "stopped" and self._playing and not self._paused:
                self._root_frame.after(200, self._play_next)
                return
        except Exception:
            pass
        if self._root_frame and not self._closed:
            self._update_id = self._root_frame.after(400, self._poll_progress)

    # ===== 音量 =====
    def _on_volume(self, val):
        try:
            self._volume = int(val)
            self._mci(f"setaudio {self.MCI_ALIAS} volume to {self._volume}")
        except Exception:
            pass

    # ===== 音效 / 均衡器 =====
    EQ_PRESETS = {
        "默认": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        "流行": [3, 2, -1, -2, -1, 2, 4, 3, 1, 0],
        "摇滚": [5, 4, -2, -3, 0, 3, 5, 4, 3, 2],
        "古典": [2, 1, 0, -1, -2, -1, 1, 2, 2, 1],
        "爵士": [4, 3, 0, -1, -2, -1, 1, 3, 2, 1],
        "电子": [6, 5, -3, -4, -2, 0, 3, 5, 5, 4],
        "人声": [-3, -2, 1, 4, 5, 3, 0, -1, -2, -3],
        "自调节": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    }
    EQ_LABELS = ["32", "64", "125", "250", "500", "1K", "2K", "4K", "8K", "16K"]

    def _on_eq_click(self):
        """弹出音效菜单。"""
        t = self._palette
        menu = tk.Menu(self._root_frame, tearoff=0,
                       bg=t.get("bg_panel"), fg=t.get("fg"),
                       font=font(size=10),
                       activebackground=t.get("accent"),
                       activeforeground="white")
        # 预设列表
        menu.add_command(label="\U0001f39a 预设音效", state="disabled")
        for name in self.EQ_PRESETS:
            check = "\u2714 " if self._eq_mode == name else "    "
            menu.add_command(label=f"{check}{name}",
                             command=lambda n=name: self._apply_eq_preset(n))
        menu.add_separator()
        menu.add_command(label="\U0001f3da 均衡器...", command=self._toggle_eq_panel)
        # 高亮当前预设
        try:
            x = self._eq_btn.winfo_rootx()
            y = self._eq_btn.winfo_rooty() + self._eq_btn.winfo_height()
            menu.tk_popup(x, y)
        except Exception:
            pass

    def _apply_eq_preset(self, name: str):
        """应用一个 EQ 预设。"""
        if name == "自调节":
            self._eq_auto = True
            self._eq_mode = "自调节"
        else:
            self._eq_auto = False
            self._eq_mode = name
            self._eq_bands = list(self.EQ_PRESETS[name])
        if self._eq_panel_visible:
            self._refresh_eq_panel()

    def _toggle_eq_panel(self):
        """展开/收起均衡器面板。"""
        if self._eq_panel_visible:
            self._hide_eq_panel()
        else:
            self._show_eq_panel()

    def _show_eq_panel(self):
        """用 place 在进度条下方展开均衡器面板。"""
        if self._eq_panel_visible:
            return
        t = self._palette
        parent = self._root_frame
        self._eq_panel = tk.Frame(parent, bg=t.get("bg_panel"),
                                   highlightbackground=t.get("border"),
                                   highlightthickness=1, bd=0)

        # ---- 预设按钮行 ----
        preset_f = tk.Frame(self._eq_panel, bg=t.get("bg_panel"))
        preset_f.pack(fill="x", padx=6, pady=(6, 2))
        for nm in self.EQ_PRESETS:
            is_cur = (nm == self._eq_mode)
            btn = tk.Label(preset_f, text=nm,
                           bg=t.get("accent") if is_cur else t.get("bg_toolbar"),
                           fg="white" if is_cur else t.get("fg"),
                           font=font(size=9), padx=6, pady=2, cursor="hand2")
            btn.pack(side="left", padx=2, pady=1)
            btn.bind("<Button-1>", lambda e, n=nm: self._apply_eq_preset(n))
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg=t.get("accent"), fg="white") if not is_cur else None)
            btn.bind("<Leave>", lambda e, b=btn: b.config(
                bg=t.get("accent") if self._eq_mode == nm else t.get("bg_toolbar"),
                fg="white" if self._eq_mode == nm else t.get("fg")))

        # ---- 10 段滑块 ----
        sliders_f = tk.Frame(self._eq_panel, bg=t.get("bg_panel"))
        sliders_f.pack(fill="x", padx=8, pady=(4, 6))
        self._eq_sliders: list[tk.Scale] = []
        for i, label in enumerate(self.EQ_LABELS):
            col = tk.Frame(sliders_f, bg=t.get("bg_panel"), width=42)
            col.pack(side="left", fill="y", padx=1)
            col.pack_propagate(False)
            s = tk.Scale(col, from_=20, to=-20, orient="vertical",
                         bg=t.get("bg_panel"), fg=t.get("fg"), relief="flat",
                         highlightthickness=0, length=90, width=10,
                         showvalue=False, command=lambda v, idx=i: self._on_eq_slider(idx, int(v)))
            s.set(self._eq_bands[i])
            s.pack()
            tk.Label(col, text=label, bg=t.get("bg_panel"), fg=t.get("fg_dim"),
                     font=font(size=7)).pack()
            self._eq_sliders.append(s)

        # ---- 自调节开关 + 关闭 ----
        bot = tk.Frame(self._eq_panel, bg=t.get("bg_panel"))
        bot.pack(fill="x", padx=8, pady=(0, 6))
        auto_btn = tk.Label(bot, text=("\u2714 " if self._eq_auto else "") + "\U0001f504 自调节",
                            bg=t.get("accent") if self._eq_auto else t.get("bg_toolbar"),
                            fg="white" if self._eq_auto else t.get("fg"),
                            font=font(size=9), padx=8, pady=3, cursor="hand2")
        auto_btn.pack(side="left")
        auto_btn.bind("<Button-1>", lambda e: self._apply_eq_preset("自调节"))
        auto_btn.bind("<Enter>", lambda e, b=auto_btn:
                      b.config(bg=t.get("accent"), fg="white") if not self._eq_auto else None)
        auto_btn.bind("<Leave>", lambda e, b=auto_btn:
                      b.config(bg=t.get("accent") if self._eq_auto else t.get("bg_toolbar"),
                               fg="white" if self._eq_auto else t.get("fg")))
        tk.Button(bot, text="\u2715 收起", bg=t.get("bg_toolbar"), fg=t.get("fg"),
                  relief="flat", font=font(size=9), padx=8, pady=2, cursor="hand2",
                  command=self._hide_eq_panel).pack(side="right")

        # 用 place 定位到可视化区域附近
        self._eq_panel.place(relx=0.0, rely=0.0, x=14, y=90, relwidth=1.0, width=-28)
        self._eq_panel.lift()
        self._eq_panel_visible = True

    def _hide_eq_panel(self):
        if hasattr(self, '_eq_panel') and self._eq_panel:
            self._eq_panel.place_forget()
            self._eq_panel.destroy()
            self._eq_panel = None
        self._eq_panel_visible = False

    def _refresh_eq_panel(self):
        """刷新已展开面板的滑块和按钮状态。"""
        if not self._eq_panel_visible or not hasattr(self, '_eq_panel') or not self._eq_panel:
            return
        self._hide_eq_panel()
        self._show_eq_panel()

    def _on_eq_slider(self, idx: int, val: int):
        """用户拖动 EQ 滑块 → 切到自定义模式。"""
        self._eq_bands[idx] = val
        self._eq_auto = False
        self._eq_mode = "自定义"

    def _auto_adjust_eq(self):
        """自调节模式：根据频谱柱高度生成 EQ 值。"""
        if not self._eq_auto:
            return
        try:
            n = len(self._eq_bands)
            viz_n = len(self._viz_heights)
            for i in range(n):
                # 将 24 根频谱柱映射到 10 个频段
                start = int(i * viz_n / n)
                end = int((i + 1) * viz_n / n)
                avg_h = sum(self._viz_heights[start:end]) / max(end - start, 1)
                # 映射高度到 dB 范围 (-15 ~ +12)
                ratio = avg_h / max(self.VIZ_H * 0.7, 1)
                self._eq_bands[i] = int(-15 + ratio * 27)
            # 刷新面板滑块
            if self._eq_panel_visible and hasattr(self, '_eq_sliders'):
                for i, s in enumerate(self._eq_sliders):
                    try:
                        s.set(self._eq_bands[i])
                    except Exception:
                        pass
        except Exception:
            pass

# ================================================================
# 21. 地图 —— 中国领土轮廓及省份划分
# ================================================================
class MapApp(App):
    """直接加载 zh.jpg 显示中国地图。"""

    _MAP_FILE = resource_path("zh.jpg")

    def __init__(self, bus, theme):
        super().__init__("map", "地图", 800, 600, bus, theme)
        self._orig_img: Image.Image | None = None
        self._photo: ImageTk.PhotoImage | None = None

    def _fill(self, parent: tk.Frame):
        t = self._palette
        self._canvas = tk.Canvas(parent, bg=t.get("bg"), highlightthickness=0,
                                  bd=0, takefocus=0,
                                  highlightbackground=t.get("bg"),
                                  highlightcolor=t.get("bg"))
        self._canvas.pack(fill="both", expand=True)

        # 加载原图
        try:
            self._orig_img = Image.open(self._MAP_FILE).convert("RGB")
        except Exception:
            self._canvas.create_text(400, 280, text="地图图片加载失败",
                                      fill=t.get("fg_dim"), font=font(size=14))
            return

        # 随窗口大小缩放
        self._canvas.bind("<Configure>", self._on_resize)
        self._redraw()

    def _on_resize(self, ev=None):
        self._canvas.after(80, self._redraw)

    def _redraw(self):
        if self._orig_img is None:
            return
        w = self._canvas.winfo_width()
        h = self._canvas.winfo_height()
        if w < 20 or h < 20:
            return  # 尚未完成布局，等 Configure 事件触发再画

        pad = 8
        cw, ch = w - pad * 2, h - pad * 2
        iw, ih = self._orig_img.size
        scale = min(cw / iw, ch / ih)
        nw, nh = int(iw * scale), int(ih * scale)

        img = self._orig_img.resize((nw, nh), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)

        self._canvas.delete("all")
        self._canvas.create_image(w // 2, h // 2, image=self._photo, anchor="center")


# ================================================================
# 22. 主系统 —— 组装所有模块
# ================================================================
class System:
    def __init__(self, root: tk.Tk):
        self._root = root
        root.title("GerOS")
        root.geometry("1280x720")
        root.configure(bg="black")
        root.minsize(800, 600)
        # 抑制根窗口/Canvas焦点高亮
        try: root.config(takefocus=0)
        except Exception: pass

        self._bus = EventBus()
        self._palette = Palette("dark")
        self._toast = Toast(root, self._palette)
        self._dz_id = None   # Dock 调整防抖 ID
        self._rz_id = None   # 壁纸渲染防抖 ID

        self._boot()

    # ---------- 启动 ----------
    def _boot(self):
        for w in self._root.winfo_children():
            w.destroy()

        self._bc = tk.Canvas(self._root, bg="#0a0a0f", highlightthickness=0,
                              takefocus=0, highlightbackground="#0a0a0f",
                              highlightcolor="#0a0a0f")
        self._bc.pack(fill="both", expand=True)

        self._root.update_idletasks()
        self._bw, self._bh = self._root.winfo_width(), self._root.winfo_height()
        if self._bw < 100:
            self._bw, self._bh = 1280, 720

        self._anim_tick = 0
        self._boot_phase = 0   # 0=fade in, 1=loading, 2=done

        # ---------- 绘制渐变背景 ----------
        self._make_boot_bg()

        # ---------- Logo ----------
        self._boot_logo = None
        lp = resource_path("logo.jpg")
        if os.path.exists(lp):
            try:
                img = Image.open(lp).convert("RGBA")
                img = img.resize((130, 130), Image.LANCZOS)
                # 圆角遮罩
                mask = Image.new("L", (130, 130), 0)
                ImageDraw.Draw(mask).rounded_rectangle((0,0,130,130), radius=24, fill=255)
                img.putalpha(mask)
                self._boot_logo = ImageTk.PhotoImage(img)
            except Exception:
                pass

        if self._boot_logo:
            self._bc.create_image(self._bw // 2, self._bh // 2 - 130,
                                   image=self._boot_logo, anchor="c", tags="boot_g")
        else:
            self._bc.create_text(self._bw // 2, self._bh // 2 - 130,
                                  text="\uf8ff", fill="#888", font=icon_font(64), tags="boot_g")

        # ---------- 标题 ----------
        self._bc.create_text(self._bw // 2, self._bh // 2 - 10,
                              text="GerOS", fill="#e0e0e0",
                              font=font(bold=True, size=38), tags="boot_g")
        self._bc.create_text(self._bw // 2, self._bh // 2 + 30,
                              text="Python Desktop System", fill="#5a5a6e",
                              font=font(size=12), tags="boot_g")
        self._bc.create_text(self._bw // 2, self._bh // 2 + 55,
                              text="Version 0.5.2", fill="#3a3a50",
                              font=font(size=10), tags="boot_g")

        # ---------- 状态文字 ----------
        self._boot_status = self._bc.create_text(
            self._bw // 2, self._bh // 2 + 110,
            text="", fill="#8888aa", font=font(size=11), tags="boot_g")

        # ---------- 自定义进度条 ----------
        self._pb_w, self._pb_h = 340, 6
        self._pb_x, self._pb_y = (self._bw - self._pb_w) // 2, self._bh // 2 + 145
        self._pb_val = 0
        self._pb_glow = None

        self._root.bind("<Configure>", lambda e: self._center_boot())
        self._root.after(50, self._anim)

    def _make_boot_bg(self):
        """PIL 生成深色渐变 + 细微粒子纹理作为启动背景"""
        w, h = self._bw, self._bh
        img = Image.new("RGBA", (w, h), (10, 10, 15, 255))
        draw = ImageDraw.Draw(img)
        # 从上到下渐变
        for y in range(h):
            t = y / h
            r = int(10 + 8 * t)
            g = int(10 + 5 * t)
            b = int(15 + 10 * t)
            draw.line((0, y, w, y), fill=(r, g, b))
        # 零星散布微弱光点
        import random
        rng = random.Random(42)
        for _ in range(80):
            px, py = rng.randint(0, w-1), rng.randint(0, h-1)
            alpha = rng.randint(15, 55)
            draw.point((px, py), fill=(100, 110, 140, alpha))
        self._boot_bg_img = ImageTk.PhotoImage(img)
        self._bc.create_image(0, 0, image=self._boot_bg_img, anchor="nw", tags="boot_bg")
        self._bc.tag_lower("boot_bg")

    def _center_boot(self):
        """窗口尺寸变化时重新生成背景"""
        try:
            nw, nh = self._root.winfo_width(), self._root.winfo_height()
            if nw < 100 or (nw == self._bw and nh == self._bh):
                return
            self._bw, self._bh = nw, nh
            self._make_boot_bg()
            self._reposition_boot_elements()
            self._draw_pb()
        except Exception:
            pass

    def _reposition_boot_elements(self):
        """将各启动元素移至新的窗口中心"""
        self._bc.delete("boot_g")
        cx, cy = self._bw // 2, self._bh // 2
        opts = {"anchor": "c"}

        if self._boot_logo:
            self._bc.create_image(cx, cy - 130, image=self._boot_logo, **opts, tags="boot_g")
        else:
            self._bc.create_text(cx, cy - 130, text="\uf8ff", fill="#888",
                                  font=icon_font(64), tags="boot_g")
        self._bc.create_text(cx, cy - 10, text="GerOS", fill="#e0e0e0",
                              font=font(bold=True, size=38), tags="boot_g")
        self._bc.create_text(cx, cy + 30, text="Python Desktop System", fill="#5a5a6e",
                              font=font(size=12), tags="boot_g")
        self._bc.create_text(cx, cy + 55, text="Version 0.5.2", fill="#3a3a50",
                              font=font(size=10), tags="boot_g")
        self._boot_status = self._bc.create_text(cx, cy + 110, text="", fill="#8888aa",
                                                  font=font(size=11), tags="boot_g")
        self._pb_x = (self._bw - self._pb_w) // 2
        self._pb_y = cy + 145

    def _draw_pb(self):
        """绘制自定义圆角进度条（轨道+发光+填充+高亮）"""
        self._bc.delete("pb")
        x, y = self._pb_x, self._pb_y
        w, h = self._pb_w, self._pb_h
        r = h // 2

        # 先画轨道底（深色凹槽）
        self._bc.create_line(x + r, y, x + w - r, y,
                              width=h + 2, capstyle="round",
                              fill="#15151f", tags="pb")
        # 轨道表面
        self._bc.create_line(x + r, y, x + w - r, y,
                              width=h, capstyle="round",
                              fill="#1e1e2e", tags="pb")

        if self._pb_val <= 0:
            return

        fill_w = max(r * 2, (w - 4) * self._pb_val / 100 + 4)
        # 发光外晕
        self._bc.create_line(x + r + 1, y, x - r + fill_w, y,
                              width=h + 8, capstyle="round",
                              fill="#1a3a80", tags="pb")
        # 实心填充
        self._bc.create_line(x + r + 1, y, x - r + fill_w, y,
                              width=h, capstyle="round",
                              fill="#4a7cf0", tags="pb")
        # 顶部高亮条
        self._bc.create_line(x + r + 1, y - 1, x - r + fill_w, y - 1,
                              width=max(1, h // 3), capstyle="round",
                              fill="#a0c4ff", tags="pb")

    def _anim(self):
        self._anim_tick += 1
        phase = self._boot_phase

        if phase == 0:
            # 淡入阶段
            p = min(self._pb_val + 3, 45)
            self._pb_val = p
            self._bc.itemconfig(self._boot_status, text="正在初始化内核...")
        elif phase == 1:
            p = min(self._pb_val + 1.5, 90)
            self._pb_val = p
            msgs = ["加载桌面组件...", "初始化窗口管理器...", "配置 Dock 面板...",
                    "挂载文件系统...", "启动事件总线..."]
            idx = int(p / 18) % len(msgs)
            self._bc.itemconfig(self._boot_status, text=msgs[idx])

            # loading 小点闪烁
            dots = "." * ((self._anim_tick % 4) + 1)
            cur = self._bc.itemcget(self._boot_status, "text")
            self._bc.itemconfig(self._boot_status, text=cur.replace(".", "") + dots)
        elif phase == 2:
            p = min(self._pb_val + 5, 100)
            self._pb_val = p
            self._bc.itemconfig(self._boot_status, text="即将就绪 ✓")

        self._draw_pb()

        if self._pb_val >= 100:
            self._root.after(300, self._start)
            return

        if self._pb_val >= 90:
            self._boot_phase = 2
        elif self._pb_val >= 45:
            self._boot_phase = 1

        self._root.after(50, self._anim)

    def _start(self):
        for w in self._root.winfo_children():
            w.destroy()
        self._root.unbind("<Configure>")
        self._root.configure(bg="black")
        # 抑制根窗口焦点高亮
        try:
            self._root.config(takefocus=0)
        except Exception:
            pass

        if os_name == "nt":
            try:
                from ctypes import windll
                windll.shcore.SetProcessDpiAwareness(1)
            except Exception: pass

        Sound.play("Ring10.wav")

        # 主题管理器
        self._tman = ThemeManager(self._bus)

        # 菜单栏（必须在 Desktop 之前创建，确保在顶部）
        self._menubar = MenuBar(self._root, self._bus, self._palette, self)
        # 启动时设置系统音量为 50%
        self._menubar._set_vol(50)
        self._menubar._update_vol_label()

        # 桌面
        self._desktop = Desktop(self._root, self._bus, self._palette, self, theme_manager=self._tman)

        # 窗口管理器
        self._wm = WindowManager(self._desktop.canvas(), self._bus, self._palette)

        # Dock
        self._dock = Dock(self._root, self._bus, self._palette)
        self._init_dock()

        # 桌面图标
        self._desktop.add_icon("\U0001f4c1", "文件夹", self.open_app("docs"))
        self._desktop.add_icon("\U0001f4bb", "应用程序", self.open_app("apps"))
        self._desktop.add_icon("\U0001f4e5", "下载", self.open_app("downloads"))
        self._desktop.add_icon("\u2699\ufe0f", "系统设置", self.open_app("settings"))
        self._desktop.add_icon("\U0001f4dd", "备忘录", self.open_app("notepad"))

        # 锁屏
        self._lock = LockScreen(self._root, self._palette, self._bus)

        # 全局快捷键
        self._root.bind("<Control-w>", lambda e: self._wm.close_active())
        self._root.bind("<Control-m>", lambda e: self._wm.minimize_active())
        self._root.bind("<Control-f>", lambda e: self.open_app("finder")())
        self._root.bind("<Control-t>", lambda e: self.open_app("terminal")())
        self._root.bind("<Control-n>", lambda e: self.open_app("notepad")())
        self._root.bind("<Control-comma>", lambda e: self.open_app("settings")())
        self._root.bind("<Escape>", lambda e: self.lock())
        self._root.bind("<F11>", lambda e: self.toggle_fullscreen())
        self._root.bind("<Control-Right>", lambda e: self.next_wallpaper())
        self._root.bind("<Control-Left>", lambda e: self.prev_wallpaper())

        self._root.bind("<Configure>", self._on_resize)
        self._root.protocol("WM_DELETE_WINDOW", self.shutdown)

        # 订阅事件
        self._bus.on("app:open", self._on_app_open)
        self._bus.on("app:new_folder", self.new_folder)

        print("=" * 50)
        print("  GerOS V0.5.2 — 已启动")
        print("  架构：EventBus 事件驱动，模块零耦合")
        print("=" * 50)

        self._root.after(500, lambda: self._toast.show("欢迎", "GerOS V0.5.2 已就绪"))

    def _on_resize(self, ev):
        if ev.widget == self._root:
            if self._dz_id:
                self._root.after_cancel(self._dz_id)
            if self._rz_id:
                self._root.after_cancel(self._rz_id)
            self._dz_id = self._root.after(100, self._dock.adjust)
            self._rz_id = self._root.after(200, self._desktop.resize_wallpaper)

    # ---------- Dock 初始化 ----------
    def _init_dock(self):
        apps = [
            ("\U0001f50d", "文件管理", "finder", self.open_app("finder")),
            ("\U0001f4e7", "邮件",     "mail",   self.open_app("mail")),
            ("\U0001f4ac", "信息",     "msg",    self.open_app("msg")),
            ("\U0001f5fa", "地图",     "map",    self.open_app("map")),
            ("\U0001f3b5", "音乐",     "music",  self.open_app("music")),
            ("\U0001f4dd", "备忘录",   "notepad",self.open_app("notepad")),
            ("\u2699\ufe0f", "设置",   "settings", self.open_app("settings")),
        ]
        for icon, name, aid, cmd in apps:
            self._dock.add_icon(icon, name, aid, cmd)

    # ---------- 应用工厂 ----------
    def _make_app(self, app_id: str, title: str, w: int, h: int, factory):
        """通用应用打开逻辑。"""
        if self._wm.has_app(app_id):
            self._wm.focus(app_id)
            if self._wm.get_minimized(app_id):
                self._wm.restore_app(app_id)
            return

        app = factory()
        win = self._wm.open(app_id, title, lambda c: app.build(parent=c), w, h)
        # 存储 app 引用防止 GC
        if not hasattr(self, "_app_instances"):
            self._app_instances = {}
        self._app_instances[app_id] = app

    def open_app(self, app_id: str):
        """返回一个无参 callable，用于按钮绑定。"""
        def _open():
            if app_id == "finder":
                self._make_app("finder", "文件管理", 920, 560, lambda: FileExplorerApp(self._bus, self._palette))
            elif app_id == "terminal":
                self._make_app("terminal", "终端", 750, 460, lambda: TerminalApp(self._bus, self._palette))
            elif app_id == "calculator":
                self._make_app("calculator", "计算器", 300, 400, lambda: CalculatorApp(self._bus, self._palette))
            elif app_id == "notepad":
                self._make_app("notepad", "备忘录", 680, 460, lambda: NotepadApp(self._bus, self._palette))
            elif app_id == "settings":
                self._make_app("settings", "系统设置", 660, 480, lambda: SettingsApp(self._bus, self._palette, self))
            elif app_id == "calendar":
                self._make_app("calendar", "日历", 400, 400, lambda: CalendarApp(self._bus, self._palette))
            elif app_id == "clock":
                self._make_app("clock", "时钟", 400, 380, lambda: ClockApp(self._bus, self._palette))
            elif app_id == "music":
                self._make_app("music", "音乐", 860, 580, lambda: MusicPlayerApp(self._bus, self._palette))
            elif app_id == "docs":
                self._placeholder("\U0001f4c2 文稿", 480, 320, [
                    "项目计划.txt", "会议记录.doc", "数据分析.xlsx", "演示文稿.pptx"
                ])
            elif app_id == "apps":
                self._apps_grid()
            elif app_id == "downloads":
                self._placeholder("\U0001f4e5 下载", 500, 300, [
                    ("Python安装包.exe", "已完成", "125 MB"),
                    ("系统镜像.iso", "78%", "4.2 GB"),
                    ("开发工具.zip", "已完成", "256 MB"),
                ])
            elif app_id == "mail":
                self._placeholder("\U0001f4e7 邮件", 420, 300, ["(功能开发中)"])
            elif app_id == "msg":
                self._placeholder("\U0001f4ac 信息", 400, 300, ["(功能开发中)"])
            elif app_id == "map":
                self._make_app("map", "地图", 800, 600, lambda: MapApp(self._bus, self._palette))
        return _open

    def _on_app_open(self, app_id: str, *args):
        """通过 EventBus 打开应用，如 event=app:open, app_id=imageviewer, path=..."""
        if app_id == "imageviewer" and args:
            path = args[0]
            self._make_app(f"imageviewer_{path}", "图片查看器", 780, 560,
                           lambda: ImageViewerApp(self._bus, self._palette, path))

    def _placeholder(self, title: str, w: int, h: int, items: list):
        def _build(parent: tk.Frame):
            content = tk.Frame(parent, bg=self._palette.get("bg"))
            tk.Label(content, text=title, bg=self._palette.get("bg"), fg=self._palette.get("fg"),
                     font=font(bold=True, size=16)).pack(pady=18)
            for item in items:
                if isinstance(item, str):
                    tk.Label(content, text=item, bg=self._palette.get("bg"), fg=self._palette.get("fg_dim"),
                             font=font(size=11)).pack(anchor="w", padx=40, pady=3)
                elif isinstance(item, tuple):
                    r = tk.Frame(content, bg=self._palette.get("bg"))
                    r.pack(fill="x", padx=30, pady=3)
                    for i, txt in enumerate(item):
                        tk.Label(r, text=txt, bg=self._palette.get("bg"),
                                 fg=self._palette.get("fg") if i == 0 else self._palette.get("fg_dim"),
                                 font=font(size=10), width=22 if i == 0 else 10,
                                 anchor="w").pack(side="left")
            content.pack(fill="both", expand=True)
        self._wm.open(f"placeholder_{title}", title, _build, w, h)

    def _apps_grid(self):
        def _build(parent: tk.Frame):
            content = tk.Frame(parent, bg=self._palette.get("bg"))
            tk.Label(content, text="应用程序", bg=self._palette.get("bg"), fg=self._palette.get("fg"),
                     font=font(bold=True, size=17)).pack(pady=14)
            grid = tk.Frame(content, bg=self._palette.get("bg"))
            grid.pack(pady=8)
            apps = [
                ("文件管理", "\U0001f50d", self.open_app("finder")),
                ("终端",     "\U0001f4bb", self.open_app("terminal")),
                ("计算器",   "\U0001f9ee", self.open_app("calculator")),
                ("备忘录",   "\U0001f4dd", self.open_app("notepad")),
                ("日历",     "\U0001f4c5", self.open_app("calendar")),
                ("时钟",     "\U0001f550", self.open_app("clock")),
                ("图片",     "\U0001f5bc", lambda: self._bus.emit("app:open", "imageviewer", "")),
                ("音乐",     "\U0001f3b5", self.open_app("music")),
                ("设置",     "\u2699\ufe0f", self.open_app("settings")),
            ]
            for i, (nm, ic, cmd) in enumerate(apps):
                f = tk.Frame(grid, bg=self._palette.get("bg"), cursor="hand2")
                f.grid(row=i // 4, column=i % 4, padx=18, pady=14)
                tk.Label(f, text=ic, bg=self._palette.get("bg"), font=icon_font(28)).pack()
                tk.Label(f, text=nm, bg=self._palette.get("bg"), fg=self._palette.get("fg_dim"),
                         font=font(size=10)).pack()
                for w in f.winfo_children():
                    w.bind("<Button-1>", lambda e, c=cmd: c())
                f.bind("<Button-1>", lambda e, c=cmd: c())
                f.bind("<Enter>", lambda ee, ff=f: ff.config(bg=self._palette.get("bg_hover")))
                f.bind("<Leave>", lambda ee, ff=f: ff.config(bg=self._palette.get("bg")))
            content.pack(fill="both", expand=True)
        self._wm.open("apps_grid", "应用程序", _build, 560, 330)

    # ---------- 系统操作 ----------
    def close_active(self):
        self._wm.close_active()

    def show_all_minimized(self):
        self._wm.show_all_minimized()

    def toggle_palette(self):
        mode = self._palette.toggle()
        self._menubar.refresh_palette()
        self._wm.refresh_all_palettes()
        self._dock.refresh_palette()
        self._toast.show("色调", f"已切换到{'暗色' if mode == 'dark' else '亮色'}模式")

    def lock(self):
        self._lock.lock()

    def toggle_fullscreen(self):
        cur = self._root.attributes("-fullscreen")
        self._root.attributes("-fullscreen", not cur)
        self._menubar._build_view_menu()

    def change_wallpaper(self):
        path = filedialog.askopenfilename(
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tiff *.tif *.ico")])
        if path:
            self._desktop.change_wallpaper_file(path)
            self._toast.show("壁纸", "已更新")

    # ---------- 主题系统操作 ----------
    def next_wallpaper(self):
        """切换到当前主题的下一张壁纸。"""
        if not self._tman.get_current_theme_info():
            # 没有加载主题，尝试加载第一个内置主题
            builtin = self._tman.scan_builtin_themes()
            if builtin:
                self._tman.set_theme(builtin[0])
            else:
                self._toast.show("提示", "没有可用的主题，请先加载或创建主题")
                return
        info = self._tman.get_current_theme_info()
        total = len(info["images"])
        self._desktop.next_wallpaper()
        idx = info.get("current_index", 0) + 1
        self._toast.show(info["name"], f"第 {min(idx, total)} / {total} 张")

    def prev_wallpaper(self):
        """切换到当前主题的上一张壁纸。"""
        if not self._tman.get_current_theme_info():
            builtin = self._tman.scan_builtin_themes()
            if builtin:
                self._tman.set_theme(builtin[0])
            else:
                self._toast.show("提示", "没有可用的主题，请先加载或创建主题")
                return
        info = self._tman.get_current_theme_info()
        total = len(info["images"])
        self._desktop.prev_wallpaper()
        idx = info.get("current_index", 0)
        self._toast.show(info["name"], f"第 {idx + 1 if idx >= 0 else total} / {total} 张")

    def save_theme_dialog(self):
        """将当前主题保存为 .ite 文件。"""
        info = self._tman.get_current_theme_info()
        if not info:
            # 尝试用当前单张壁纸创建主题
            if self._desktop._orig_img is None:
                messagebox.showinfo("提示", "当前没有可保存的壁纸")
                return
            name = simpledialog.askstring("保存主题", "请输入主题名称:")
            if not name:
                return
            path = filedialog.asksaveasfilename(
                title="保存主题",
                defaultextension=".ite",
                initialfile=f"{name}.ite",
                filetypes=[("Ger主题文件", "*.ite")])
            if not path:
                return
            # 单张图片也保存为.ite
            import tempfile
            tmp = tempfile.gettempdir()
            tmp_img = os.path.join(tmp, f"_ger_temp_wp_{int(time.time())}.png")
            try:
                self._desktop._orig_img.save(tmp_img)
                ThemeManager.save_ite_file(name, [tmp_img], path)
                os.remove(tmp_img)
                self._toast.show("主题", f"'{name}' 已保存为 .ite")
            except Exception as e:
                messagebox.showerror("错误", f"保存失败: {e}")
            return

        name = simpledialog.askstring("保存主题", "主题名称:", initialvalue=info["name"])
        if not name:
            return
        path = filedialog.asksaveasfilename(
            title="保存主题",
            defaultextension=".ite",
            initialfile=f"{name}.ite",
            filetypes=[("Ger主题文件", "*.ite")])
        if not path:
            return
        try:
            ThemeManager.save_ite_file(name, info["images"], path)
            self._toast.show("主题", f"'{name}' 已保存为 .ite")
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")

    def load_theme_dialog(self):
        """加载 .ite 主题文件。"""
        path = filedialog.askopenfilename(
            title="加载主题",
            filetypes=[("Ger主题文件", "*.ite"), ("所有文件", "*.*")])
        if not path:
            return
        theme = self._tman.load_ite_file(path)
        if theme:
            self._tman.set_theme(theme)
            self._apply_theme(theme)

    def _apply_theme(self, theme: dict):
        """应用一个主题到桌面。"""
        img = self._tman.get_current_image()
        if img and os.path.exists(img):
            self._desktop.apply_wallpaper_path(img)
            self._toast.show(theme["name"], f"共 {len(theme['images'])} 张壁纸")
        else:
            self._toast.show("警告", "主题中没有可显示的图片")

    def manage_themes(self):
        """打开系统设置 → 个性化。"""
        self.open_app("settings")()
        if "settings" in getattr(self, "_app_instances", {}):
            inst = self._app_instances["settings"]
            if hasattr(inst, 'navigate_to'):
                self._root.after(50, lambda: inst.navigate_to("个性化"))

    def new_folder(self):
        name = simpledialog.askstring("新建文件夹", "名称:")
        if name:
            try:
                os.makedirs(os.path.join(os.getcwd(), name), exist_ok=False)
                self._toast.show("完成", f"'{name}' 已创建")
            except FileExistsError:
                messagebox.showerror("错误", "已存在")
            except Exception as e:
                messagebox.showerror("错误", str(e))

    def new_txt(self):
        name = simpledialog.askstring("新建文本文档", "文件名(不含扩展名):")
        if name:
            try:
                p = os.path.join(os.getcwd(), name + ".txt")
                if os.path.exists(p): raise FileExistsError
                with open(p, "w", encoding="utf-8") as f:
                    f.write("")
                self._toast.show("完成", f"'{name}.txt' 已创建")
            except FileExistsError:
                messagebox.showerror("错误", "已存在")
            except Exception as e:
                messagebox.showerror("错误", str(e))

    def open_file_dialog(self):
        path = filedialog.askopenfilename(filetypes=[("所有", "*.*")])
        if path:
            ext = os.path.splitext(path)[1].lower()
            if ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
                self._bus.emit("app:open", "imageviewer", path)
            elif ext in (".txt", ".py", ".md", ".json", ".csv", ".log"):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        text = f.read()
                    def _build(parent: tk.Frame):
                        content = tk.Frame(parent, bg=self._palette.get("bg"))
                        e = scrolledtext.ScrolledText(content, bg=self._palette.get("bg"),
                                                       fg=self._palette.get("fg"),
                                                       font=font(size=11), relief="flat",
                                                       bd=0, highlightthickness=0)
                        e.pack(fill="both", expand=True, padx=14, pady=14)
                        e.insert("1.0", text)
                        content.pack(fill="both", expand=True)
                    self._wm.open(f"editor_{path}", os.path.basename(path), _build, 680, 420)
                except Exception as ex:
                    messagebox.showerror("错误", str(ex))
            else:
                try:
                    if os_name == "nt": os.startfile(path)
                    else: subprocess.Popen(["xdg-open", path])
                except Exception as ex:
                    messagebox.showerror("错误", str(ex))

    def clear_bin(self):
        if messagebox.askyesno("确认", "清空回收站？"):
            try:
                if os_name == "nt":
                    subprocess.run(["PowerShell", "-Command", "Clear-RecycleBin", "-Force"],
                                    capture_output=True)
                self._toast.show("回收站", "已清空")
            except Exception as e:
                messagebox.showerror("错误", str(e))

    def show_about(self):
        messagebox.showinfo("关于本机", "GerOS V0.5.2\nEventBus 事件驱动架构\n模块零耦合设计")

    def show_sysinfo(self):
        info = (f"GerOS V0.5.2\n\n"
                f"CPU: {psutil.cpu_count()} 核心\n"
                f"RAM: {psutil.virtual_memory().total >> 30} GB\n"
                f"磁盘: {psutil.disk_usage(os.sep).total >> 30} GB\n"
                f"架构: EventBus 事件总线")
        messagebox.showinfo("系统信息", info)

    def show_shortcuts(self):
        messagebox.showinfo("快捷键",
            "Ctrl+W  关闭窗口\nCtrl+M  最小化\n"
            "Ctrl+F  文件管理\nCtrl+T  终端\n"
            "Ctrl+N  备忘录\nCtrl+,  设置\n"
            "Ctrl+→  下一张壁纸\nCtrl+←  上一张壁纸\n"
            "Esc     锁屏\nF11     全屏")

    def shutdown(self):
        # 1. 停止音乐播放器（如果有实例在运行）
        try:
            if hasattr(self, '_app_instances') and 'music' in self._app_instances:
                self._app_instances['music']._stop()
        except Exception:
            pass
        # 2. 关闭所有应用窗口
        try:
            if hasattr(self, '_wm'):
                self._wm.close_all()
        except Exception:
            pass
        # 3. 清理主题临时文件
        try:
            if hasattr(self, '_tman'):
                self._tman.cleanup()
        except Exception:
            pass
        # 3. 刷新界面，让所有窗口关闭效果立即呈现
        try:
            self._root.update()
        except Exception:
            pass
        # 4. 播放关机声音（等待播放完）
        try:
            Sound.play("Windows Logoff Sound.wav", wait=True)
        except Exception:
            pass
        # 5. 销毁并退出
        self._root.destroy()
        sys.exit(0)


# ================================================================
# 22. NodeEnv — 自集成 Node.js 运行环境（免安装、免下载、跨电脑）
# ================================================================
class NodeEnv:
    """从 node.zip 自动提取并管理内建 Node.js 环境。
    系统启动时自动加载，无需用户安装，任何电脑都能用。"""
    _ready = False
    _node_path: str | None = None
    _node_root: str | None = None
    _node_modules_path: str | None = None
    _lock = threading.Lock()
    _DEPS = ["axios", "he", "crypto-js", "webdav", "cheerio", "dayjs"]  # 插件常用依赖

    @classmethod
    def get_bundled_node(cls) -> str:
        """返回内建 node.exe 的绝对路径。"""
        return os.path.join(app_dir(), "nodejs", "node.exe")

    @classmethod
    def get_zip_path(cls) -> str:
        """返回 node.zip 的路径。"""
        return os.path.join(app_dir(), "node.zip")

    @classmethod
    def is_ready(cls) -> bool:
        return cls._ready and cls._node_path is not None and os.path.isfile(cls._node_path)

    @classmethod
    def _extract_full(cls, zip_path: str, dest_dir: str, status_cb=None) -> str | None:
        """完整解压 node.zip 到 dest_dir，返回 node.exe 绝对路径。"""
        try:
            os.makedirs(dest_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                entries = [e for e in zf.namelist() if not e.endswith("/") and not e.endswith("\\")]
                total = len(entries)
                for i, entry in enumerate(entries):
                    zf.extract(entry, dest_dir)
                    if status_cb and total > 0 and i % 100 == 0:
                        pct = min(99, int(i / total * 100))
                        status_cb(f"[..] 解压 Node.js...{pct}%")
            # 查找解压后的 node.exe
            for root, dirs, files in os.walk(dest_dir):
                if "node.exe" in files:
                    return os.path.join(root, "node.exe")
            return None
        except Exception as e:
            print(f"[NodeEnv] 解压失败: {e}")
            return None

    @classmethod
    def _find_extracted_node(cls) -> str | None:
        """查找已解压的 node.exe。"""
        nodejs_dir = os.path.join(app_dir(), "nodejs")
        if not os.path.isdir(nodejs_dir):
            return None
        for root, dirs, files in os.walk(nodejs_dir):
            if "node.exe" in files:
                return os.path.join(root, "node.exe")
        return None

    @classmethod
    def _install_package_py(cls, pkg_name: str) -> bool:
        """纯 Python 方式从 npm 镜像下载并安装指定 npm 包，无需依赖 npm 命令行工具。

    通过 registry.npmjs.org 获取包的最新版本信息，下载对应 tarball，
    解压至 node_modules 目录，并处理包内的文件、目录及符号链接。

    Args:
        pkg_name (str): 需要安装的 npm 包名称

    Returns:
        bool: 安装成功返回 True，失败返回 False
    """
        import tarfile, io, gzip
        try:
            # 获取最新版本
            info_url = f"https://registry.npmjs.org/{pkg_name}"
            req = urllib.request.Request(info_url, headers={"User-Agent": "GerOS"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                info = json.loads(resp.read().decode())
            version = info.get("dist-tags", {}).get("latest")
            if not version:
                print(f"[NodeEnv] 无法获取 {pkg_name} 版本信息")
                return False
            # 下载 tarball
            tarball_url = info["versions"][version]["dist"]["tarball"]
            print(f"[NodeEnv] 下载 {pkg_name}@{version} ...")
            req = urllib.request.Request(tarball_url, headers={"User-Agent": "GerOS"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            # 解压到 node_modules/{pkg_name}
            dest = os.path.join(cls._node_modules_path, pkg_name)
            os.makedirs(dest, exist_ok=True)
            with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                for member in tar.getmembers():
                    # npm 包总是解压到 ./package/ 前缀
                    name = member.name
                    if name.startswith("package/"):
                        name = name[8:]
                    if not name or name in (".", ".."):
                        continue
                    target = os.path.join(dest, name)
                    if member.isdir():
                        os.makedirs(target, exist_ok=True)
                    elif member.isfile():
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with tar.extractfile(member) as src:
                            with open(target, "wb") as dst:
                                dst.write(src.read())
                    elif member.issym() or member.islnk():
                        try:
                            lnk = member.linkname
                            if lnk.startswith("package/"):
                                lnk = lnk[8:]
                            lnk_target = os.path.join(dest, lnk)
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            if os.path.exists(target):
                                os.remove(target)
                            os.symlink(lnk_target, target)
                        except Exception:
                            pass
            return True
        except Exception as e:
            print(f"[NodeEnv] Python安装 {pkg_name} 失败: {e}")
            return False

    @classmethod
    def _ensure_deps(cls, status_cb=None) -> None:
        """确保常用插件依赖已安装到 node_modules。"""
        if not cls._node_root or not cls._node_modules_path:
            return
        # 检查缺失的依赖
        missing = []
        for dep in cls._DEPS:
            marker = os.path.join(cls._node_modules_path, dep)
            if not os.path.exists(marker):
                missing.append(dep)
        if not missing:
            return
        if status_cb:
            status_cb(f"[..] 安装插件依赖: {', '.join(missing)}...")
        os.makedirs(cls._node_modules_path, exist_ok=True)

        # 1) 尝试用内置 npm 安装
        npm_cli = os.path.join(cls._node_root, "node_modules", "npm", "bin", "npm-cli.js")
        if not os.path.isfile(npm_cli):
            npm_cmd = os.path.join(cls._node_root, "npm.cmd")
            if os.path.isfile(npm_cmd):
                npm_cli = npm_cmd

        npm_ok = False
        if os.path.isfile(npm_cli):
            try:
                new_env = {**os.environ, "PATH": cls._node_root + os.pathsep + os.environ.get("PATH", "")}
                r = subprocess.run(
                    [cls._node_path, npm_cli, "install", "--no-save", "--no-audit", "--no-fund"] + missing,
                    capture_output=True, text=True, encoding="utf-8", timeout=120,
                    cwd=cls._node_root, env=new_env
                )
                if r.returncode == 0:
                    npm_ok = True
                    if status_cb:
                        status_cb("[OK] 依赖安装完成")
                else:
                    err = r.stderr[:200] if r.stderr else "未知"
                    print(f"[NodeEnv] npm安装失败: {err}")
            except Exception as e:
                print(f"[NodeEnv] npm异常: {e}")

        # 2) npm 不可用 → Python 直接下载安装
        if not npm_ok:
            if status_cb:
                status_cb("[..] 通过镜像下载依赖...")
            ok_count = 0
            for dep in missing:
                if status_cb:
                    status_cb(f"[..] 下载 {dep}...")
                if cls._install_package_py(dep):
                    ok_count += 1
            if status_cb:
                if ok_count == len(missing):
                    status_cb("[OK] 依赖安装完成")
                else:
                    status_cb(f"[WARN] 已安装 {ok_count}/{len(missing)} 个依赖")

    @classmethod
    def get_node_modules_path(cls) -> str | None:
        """返回 Node.js 内置 node_modules 路径。"""
        if cls._node_modules_path and os.path.isdir(cls._node_modules_path):
            return cls._node_modules_path
        # 回退：搜索 nodejs 目录下的 node_modules
        nodejs_dir = os.path.join(app_dir(), "nodejs")
        if os.path.isdir(nodejs_dir):
            for root, dirs, files in os.walk(nodejs_dir):
                if "node_modules" in dirs:
                    return os.path.join(root, "node_modules")
        return None

    @classmethod
    def ensure(cls, status_cb=None) -> bool:
        """确保 Node.js 环境就绪: 内建优先 > 系统PATH。
        首次运行时从 node.zip 自动解压并安装依赖，后续启动秒加载。"""
        with cls._lock:
            if cls._ready:
                return True

            # 1) 已解压的 node.exe 存在 → 直接使用
            extracted = cls._find_extracted_node()
            if extracted and os.path.isfile(extracted):
                cls._node_path = extracted
                cls._node_root = os.path.dirname(extracted)
                cls._node_modules_path = os.path.join(cls._node_root, "node_modules")
                cls._ensure_deps(status_cb)
                cls._ready = True
                if status_cb:
                    status_cb("[OK] 内建 Node.js 就绪")
                return True

            # 2) 从 node.zip 完整解压
            zip_path = cls.get_zip_path()
            if os.path.isfile(zip_path):
                if status_cb:
                    status_cb("[..] 正在初始化 Node.js 环境...")
                dest_dir = os.path.join(app_dir(), "nodejs")
                node_path = cls._extract_full(zip_path, dest_dir, status_cb)
                if node_path and os.path.isfile(node_path):
                    cls._node_path = node_path
                    cls._node_root = os.path.dirname(node_path)
                    cls._node_modules_path = os.path.join(cls._node_root, "node_modules")
                    # 安装依赖
                    cls._ensure_deps(status_cb)
                    cls._ready = True
                    if status_cb:
                        status_cb("[OK] Node.js 内建完成, JS 插件可用")
                    return True
                if status_cb:
                    status_cb("[FAIL] Node.js 解压失败, 尝试系统 PATH...")

            # 3) 回退到系统 PATH
            for cmd in ["node", "nodejs"]:
                try:
                    r = subprocess.run([cmd, "--version"], capture_output=True, text=True, encoding="utf-8", timeout=5)
                    if r.returncode == 0:
                        cls._ready = True
                        cls._node_path = cmd
                        ver = (r.stdout or "").strip()
                        if status_cb:
                            status_cb(f"[OK] 使用系统 Node.js ({ver})")
                        return True
                except Exception:
                    continue

            if status_cb:
                status_cb("[WARN] Node.js 不可用, JS 插件功能受限")
            return False

    @classmethod
    def get_path(cls) -> str | None:
        """获取可用的 node 路径。"""
        if cls._ready and cls._node_path:
            return cls._node_path
        return None

    @classmethod
    def get_version(cls) -> str:
        """获取 Node.js 版本字符串。"""
        path = cls.get_path()
        if not path:
            return "N/A"
        try:
            r = subprocess.run([path, "--version"], capture_output=True, text=True, encoding="utf-8", timeout=5)
            return (r.stdout or "").strip() if r.returncode == 0 else "N/A"
        except Exception:
            return "N/A"


# ================================================================
# 23. 入口
# ================================================================
def main():
    try:
        print("")
        print("╔══════════════════════════════════════════════════════════════════╗")
        print("║                                                                  ║")
        print("║      ██████╗ ███████╗██████╗   ██████╗ ███████╗                  ║")
        print("║     ██╔════╝ ██╔════╝██╔══██╗ ██╔═══██╗██╔════╝                  ║")
        print("║     ██║  ███╗█████╗  ██████╔╝ ██║   ██║███████╗                  ║")
        print("║     ██║   ██║██╔══╝  ██╔══██╗ ██║   ██║╚════██║                  ║")
        print("║     ╚██████╔╝███████╗██║  ██║ ╚██████╔╝███████║                  ║")
        print("║      ╚═════╝ ╚══════╝╚═╝  ╚═╝  ╚═════╝ ╚══════╝                  ║")
        print("║                                                                  ║")
        print("║     Python Desktop Operating System  ·  Version 0.5.2            ║")
        print("║                                                                  ║")
        print("╚══════════════════════════════════════════════════════════════════╝")
        print("")

        # ---- 启动前：初始化内建 Node.js 环境 ----
        def _node_status(msg):
            print(f"  [NodeEnv] {msg}")
        print("  → 正在初始化运行环境...")
        _node_ok = NodeEnv.ensure(status_cb=_node_status)
        if _node_ok:
            ver = NodeEnv.get_version()
            print(f"  → Node.js {ver} 就绪，JS 插件已激活")
        else:
            print("  → Node.js 不可用，JS 插件将不可用（JSON 插件正常）")
        print("")

        print("  → GerOS 系统启动成功！")
        root = tk.Tk()
        System(root)
        root.mainloop()
    except Exception as e:
        import traceback
        traceback.print_exc()
        messagebox.showerror("启动失败", str(e))


if __name__ == "__main__":
    main()
