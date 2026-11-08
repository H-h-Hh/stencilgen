"""Tkinter front end: pick (or drop) a gerber package, press Generate, get files.

The UI is bilingual. It follows the system language, and can be forced with
    set STENCILGEN_LANG=en      (Windows)
    export STENCILGEN_LANG=zh   (macOS / Linux)
"""
from __future__ import annotations

import json
import locale
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .generator import Options, StencilError
from .pipeline import run

try:  # optional drag & drop
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception:  # pragma: no cover
    HAS_DND = False

THICKNESS = ["0.08", "0.10", "0.12", "0.15", "0.20", "0.25"]

TEXT = {
    "zh": {
        "title": "钢网生成器  ·  Gerber → 钢网 3D + 固定架",
        "source": "1. 制版文件（Gerber 压缩包，或解压后的文件夹）",
        "browse": "浏览…",
        "generate": "生成",
        "hint_dnd": "可以直接把 .zip 拖到窗口里",
        "hint_nodnd": "提示：pip install tkinterdnd2 后可直接拖放文件",
        "options": "2. 参数",
        "thick": "钢网厚度 (mm)",
        "margin": "四周加边 (mm)",
        "board_t": "PCB 厚度 (mm)",
        "fixture": "同时生成固定底座",
        "fixture_border": "底座边框 (mm)",
        "fixture_height": "底座高度 (mm)",
        "auto_out": "保存到源文件旁边",
        "pick": "选择…",
        "pick_title": "选择输出文件夹",
        "log": "3. 日志",
        "open_out": "打开输出文件夹",
        "ready": "就绪",
        "generating": "生成中…",
        "done": "完成",
        "failed": "失败",
        "warn_file_title": "缺少文件",
        "warn_file": "先选一个 Gerber 压缩包或文件夹",
        "warn_param_title": "参数错误",
        "warn_param": "厚度/尺寸必须是数字",
        "warn_out_title": "输出位置",
        "warn_out": "请先选一个存在的输出文件夹",
        "err_title": "生成失败",
        "out_dir": "输出目录: {p}",
        "error": "错误: {m}",
        "dlg_source": "选择制版文件",
        "ft_zip": "Gerber 压缩包",
        "ft_all": "所有文件",
    },
    "en": {
        "title": "Stencil Generator  ·  Gerber → stencil 3D + fixture",
        "source": "1. Manufacturing package (Gerber zip, or an extracted folder)",
        "browse": "Browse…",
        "generate": "Generate",
        "hint_dnd": "Tip: you can drag a .zip straight into this window",
        "hint_nodnd": "Tip: pip install tkinterdnd2 to enable drag & drop",
        "options": "2. Options",
        "thick": "Stencil thickness (mm)",
        "margin": "Border (mm)",
        "board_t": "PCB thickness (mm)",
        "fixture": "Also build the holder fixture",
        "fixture_border": "Fixture border (mm)",
        "fixture_height": "Fixture height (mm)",
        "auto_out": "Save next to the source file",
        "pick": "Browse…",
        "pick_title": "Choose output folder",
        "log": "3. Log",
        "open_out": "Open output folder",
        "ready": "Ready",
        "generating": "Generating…",
        "done": "Done",
        "failed": "Failed",
        "warn_file_title": "No input file",
        "warn_file": "Pick a Gerber .zip or an extracted folder first",
        "warn_param_title": "Bad value",
        "warn_param": "Thickness / sizes must be numbers",
        "warn_out_title": "Output folder",
        "warn_out": "Choose an existing output folder first",
        "err_title": "Generation failed",
        "out_dir": "Output: {p}",
        "error": "Error: {m}",
        "dlg_source": "Select the manufacturing package",
        "ft_zip": "Gerber archive",
        "ft_all": "All files",
    },
}


def detect_lang():
    """zh / en, from STENCILGEN_LANG, then the system locale."""
    forced = os.environ.get("STENCILGEN_LANG", "").strip().lower()
    if forced.startswith("zh"):
        return "zh"
    if forced.startswith("en"):
        return "en"

    try:
        loc = locale.getlocale()[0] or ""
    except Exception:
        loc = ""
    low = loc.lower()
    if low.startswith("zh") or low.startswith("chinese"):
        return "zh"

    if sys.platform == "win32":
        try:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if (lcid & 0xFF) == 0x02:      # Chinese primary language
                return "zh"
        except Exception:
            pass

    return "en"


LANG = detect_lang()


def t(key, **kw):
    s = TEXT.get(LANG, TEXT["en"]).get(key, key)
    return s.format(**kw) if kw else s


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.outdir = None
        self.busy = False

        root.title(t("title"))
        root.geometry("880x640")
        root.minsize(760, 540)

        self._build_ui()
        self._enable_dnd()
        self._load_settings()
        self.root.after(120, self._drain)

    # ------------------------------------------------------------------ ui
    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        top = ttk.LabelFrame(self.root, text=t("source"))
        top.pack(fill="x", **pad)

        self.path_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.path_var)
        entry.pack(side="left", fill="x", expand=True, padx=(10, 6), pady=10)
        ttk.Button(top, text=t("browse"), command=self._browse).pack(
            side="left", padx=(0, 6), pady=10)
        self.btn_go = ttk.Button(top, text=t("generate"), command=self._start)
        self.btn_go.pack(side="left", padx=(0, 10), pady=10)

        ttk.Label(top, text=t("hint_dnd") if HAS_DND else t("hint_nodnd"),
                  foreground="#888").pack(anchor="w", padx=12, pady=(0, 8))

        opt = ttk.LabelFrame(self.root, text=t("options"))
        opt.pack(fill="x", **pad)

        row1 = ttk.Frame(opt)
        row1.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(row1, text=t("thick")).pack(side="left")
        self.thick = tk.StringVar(value="0.12")
        ttk.Combobox(row1, textvariable=self.thick, values=THICKNESS,
                     width=7).pack(side="left", padx=(6, 18))

        ttk.Label(row1, text=t("margin")).pack(side="left")
        self.margin = tk.StringVar(value="0")
        ttk.Spinbox(row1, from_=0, to=30, increment=1, textvariable=self.margin,
                    width=6).pack(side="left", padx=(6, 18))

        ttk.Label(row1, text=t("board_t")).pack(side="left")
        self.board_t = tk.StringVar(value="1.6")
        ttk.Spinbox(row1, from_=0.4, to=6.0, increment=0.1, textvariable=self.board_t,
                    width=6).pack(side="left", padx=(6, 0))

        row2 = ttk.Frame(opt)
        row2.pack(fill="x", padx=10, pady=(2, 10))
        self.fixture = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text=t("fixture"), variable=self.fixture).pack(side="left")

        ttk.Label(row2, text="   " + t("fixture_border")).pack(side="left")
        self.fb = tk.StringVar(value="6")
        ttk.Spinbox(row2, from_=3, to=20, increment=1, textvariable=self.fb,
                    width=5).pack(side="left", padx=(4, 16))

        ttk.Label(row2, text=t("fixture_height")).pack(side="left")
        self.fh = tk.StringVar(value="4.0")
        ttk.Spinbox(row2, from_=2.5, to=12, increment=0.5, textvariable=self.fh,
                    width=5).pack(side="left", padx=(4, 0))

        row3 = ttk.Frame(opt)
        row3.pack(fill="x", padx=10, pady=(0, 10))
        self.auto_out = tk.BooleanVar(value=True)
        ttk.Checkbutton(row3, text=t("auto_out"), variable=self.auto_out,
                        command=self._toggle_out).pack(side="left")
        self.out_var = tk.StringVar(value="")
        self.out_entry = ttk.Entry(row3, textvariable=self.out_var, state="disabled")
        self.out_entry.pack(side="left", fill="x", expand=True, padx=(12, 6))
        self.btn_out = ttk.Button(row3, text=t("pick"), command=self._pick_out,
                                  state="disabled")
        self.btn_out.pack(side="left")

        logf = ttk.LabelFrame(self.root, text=t("log"))
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, height=14, wrap="word", state="disabled",
                           font=("Consolas", 9))
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)

        bottom = ttk.Frame(self.root)
        bottom.pack(fill="x", padx=10, pady=(0, 10))
        self.btn_open = ttk.Button(bottom, text=t("open_out"), command=self._open_out,
                                   state="disabled")
        self.btn_open.pack(side="right")
        self.status = ttk.Label(bottom, text=t("ready"), foreground="#555")
        self.status.pack(side="left")

    def _enable_dnd(self):
        if not HAS_DND:
            return
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    # -------------------------------------------------------------- actions
    def _browse(self):
        p = filedialog.askopenfilename(
            title=t("dlg_source"),
            filetypes=[(t("ft_zip"), "*.zip"), (t("ft_all"), "*.*")])
        if p:
            self.path_var.set(p)

    def _on_drop(self, event):
        raw = event.data.strip()
        paths = []
        while raw:
            if raw.startswith("{"):
                end = raw.find("}")
                paths.append(raw[1:end])
                raw = raw[end + 1:].strip()
            else:
                parts = raw.split(" ", 1)
                paths.append(parts[0])
                raw = parts[1].strip() if len(parts) > 1 else ""
        if paths:
            self.path_var.set(paths[0])

    def _toggle_out(self):
        auto = bool(self.auto_out.get())
        state = "disabled" if auto else "normal"
        self.out_entry.configure(state=state)
        self.btn_out.configure(state=state)
        if not auto and not self.out_var.get().strip():
            self._pick_out()

    def _pick_out(self):
        init = self.out_var.get().strip() or os.path.expanduser("~")
        d = filedialog.askdirectory(title=t("pick_title"), initialdir=init,
                                    mustexist=True)
        if d:
            self.out_var.set(os.path.normpath(d))
        elif not self.out_var.get().strip():
            self.auto_out.set(True)
            self._toggle_out()
        self._save_settings()

    # ------------------------------------------------------------ settings
    def _settings_file(self):
        if sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        elif sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Application Support")
        else:
            base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        return os.path.join(base, "StencilGen", "settings.json")

    def _load_settings(self):
        try:
            with open(self._settings_file(), encoding="utf-8") as f:
                s = json.load(f)
            self.out_var.set(s.get("outdir", ""))
            self.auto_out.set(bool(s.get("auto_out", True)))
            self.thick.set(s.get("thick", "0.12"))
            self.board_t.set(s.get("board_t", "1.6"))
        except Exception:
            pass
        self._toggle_out()

    def _save_settings(self):
        try:
            p = self._settings_file()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"outdir": self.out_var.get().strip(),
                           "auto_out": bool(self.auto_out.get()),
                           "thick": self.thick.get(),
                           "board_t": self.board_t.get()}, f,
                          ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _start(self):
        if self.busy:
            return
        src = self.path_var.get().strip().strip('"')
        if not src or not os.path.exists(src):
            messagebox.showwarning(t("warn_file_title"), t("warn_file"))
            return
        try:
            opts = Options(
                thickness=float(self.thick.get()),
                margin=float(self.margin.get() or 0),
                board_thickness=float(self.board_t.get() or 1.6),
                fixture=bool(self.fixture.get()),
                fixture_border=float(self.fb.get() or 6),
                fixture_height=float(self.fh.get() or 4.0),
            )
        except ValueError:
            messagebox.showwarning(t("warn_param_title"), t("warn_param"))
            return

        outdir = None
        if not self.auto_out.get():
            root = self.out_var.get().strip()
            if not root or not os.path.isdir(root):
                messagebox.showwarning(t("warn_out_title"), t("warn_out"))
                return
            stem = os.path.splitext(os.path.basename(src.rstrip("\\/")))[0]
            outdir = os.path.join(root, f"{stem}_stencil")

        self._save_settings()
        self.busy = True
        self.btn_go.configure(state="disabled")
        self.btn_open.configure(state="disabled")
        self.status.configure(text=t("generating"))
        self._clear_log()
        threading.Thread(target=self._work, args=(src, opts, outdir), daemon=True).start()

    def _work(self, src, opts, outdir=None):
        try:
            rep, outdir = run(src, opts, outdir=outdir,
                              log=lambda m: self.q.put(("log", str(m))))
            self.q.put(("done", (rep, outdir)))
        except StencilError as e:
            self.q.put(("error", str(e)))
        except Exception as e:  # noqa: BLE001 - surfaced to the user
            import traceback
            self.q.put(("log", traceback.format_exc()))
            self.q.put(("error", f"{type(e).__name__}: {e}"))

    # ---------------------------------------------------------------- queue
    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append(payload)
                elif kind == "done":
                    rep, outdir = payload
                    self.outdir = outdir
                    self._append("")
                    self._append(rep.as_text())
                    self._append("")
                    self._append(t("out_dir", p=outdir))
                    self.status.configure(text=t("done"))
                    self.btn_go.configure(state="normal")
                    self.btn_open.configure(state="normal")
                    self.busy = False
                elif kind == "error":
                    self._append(t("error", m=payload))
                    self.status.configure(text=t("failed"))
                    self.btn_go.configure(state="normal")
                    self.busy = False
                    messagebox.showerror(t("err_title"), payload)
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    def _append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _open_out(self):
        if self.outdir and os.path.isdir(self.outdir):
            if sys.platform.startswith("win"):
                os.startfile(self.outdir)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.run(["open", self.outdir], check=False)
            else:
                subprocess.run(["xdg-open", self.outdir], check=False)


def main():
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.2)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
