#!/usr/bin/env python3
"""
SVG → PNG Converter + Sprite Atlas Builder — GUI
Запуск: python svg_converter_gui.py
"""

import sys
import math
import threading
import shutil
import subprocess
import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ─── Auto-detect local GTK3 runtime ───────────────────────────────────────────
_gtk_local = Path(__file__).parent / "GTK3-Runtime Win64" / "bin"
if _gtk_local.exists():
    os.environ["PATH"] = str(_gtk_local) + os.pathsep + os.environ.get("PATH", "")

try:
    import cairosvg
except ImportError:
    messagebox.showerror("Ошибка", "Установите cairosvg:\npip install cairosvg")
    sys.exit(1)

try:
    from PIL import Image
    import numpy as np
except ImportError:
    messagebox.showerror("Ошибка", "Установите Pillow и numpy:\npip install Pillow numpy")
    sys.exit(1)


# ─── Общие константы стиля ────────────────────────────────────────────────────
BG       = "#1e1e2e"
FG       = "#cdd6f4"
ACCENT   = "#89b4fa"
ENTRY_BG = "#313244"
BTN_BG   = "#45475a"


# ─── Логика SVG конвертации ───────────────────────────────────────────────────

def svg_to_png(svg_path: Path, out_path: Path, dpi: int) -> None:
    cairosvg.svg2png(url=str(svg_path), write_to=str(out_path), dpi=dpi)


def optimize_png(png_path: Path) -> tuple:
    original_size = png_path.stat().st_size
    pngquant_bin = shutil.which("pngquant")
    if pngquant_bin:
        tmp = png_path.with_suffix(".tmp.png")
        result = subprocess.run(
            [pngquant_bin, "--quality=65-90", "--force", "--output", str(tmp), str(png_path)],
            capture_output=True,
        )
        if result.returncode == 0 and tmp.exists():
            tmp.replace(png_path)
    oxipng_bin = shutil.which("oxipng")
    if oxipng_bin:
        subprocess.run([oxipng_bin, "--opt", "4", "--strip", "all", str(png_path)], capture_output=True)
    else:
        img = Image.open(png_path)
        img.save(png_path, format="PNG", optimize=True, compress_level=9)
    return original_size, png_path.stat().st_size


def generate_normal_map(png_path: Path, out_path: Path, strength: float) -> None:
    img = Image.open(png_path).convert("L")
    h = np.array(img, dtype=np.float32) / 255.0
    kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)
    ky = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=np.float32)

    def conv(arr, k):
        pad = np.pad(arr, 1, mode="edge")
        out = np.zeros_like(arr)
        for i in range(3):
            for j in range(3):
                out += pad[i:i+arr.shape[0], j:j+arr.shape[1]] * k[i, j]
        return out

    gx, gy = conv(h, kx) * strength, conv(h, ky) * strength
    gz = np.ones_like(gx)
    length = np.sqrt(gx**2 + gy**2 + gz**2)
    nx, ny, nz = -gx/length, -gy/length, gz/length
    r = ((nx * 0.5 + 0.5) * 255).clip(0, 255).astype(np.uint8)
    g = ((ny * 0.5 + 0.5) * 255).clip(0, 255).astype(np.uint8)
    b = ((nz * 0.5 + 0.5) * 255).clip(0, 255).astype(np.uint8)
    Image.fromarray(np.stack([r, g, b], axis=-1), mode="RGB").save(out_path)


def collect_svgs(paths):
    result = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            result.extend(sorted(path.glob("**/*.svg")))
        elif path.suffix.lower() == ".svg":
            result.append(path)
    return result


# ─── Логика атласа спрайтов ───────────────────────────────────────────────────

def collect_images(paths):
    """Собирает PNG/JPG/SVG файлы из списка путей и папок."""
    exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".svg"}
    result = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for ext in exts:
                result.extend(sorted(path.glob(f"**/*{ext}")))
        elif path.suffix.lower() in exts:
            result.append(path)
    # убираем дубли, сохраняем порядок
    seen = set()
    unique = []
    for p in result:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def build_atlas(image_paths, out_path: Path, padding: int, columns: int,
                bg_color: tuple, log_fn=None) -> None:
    """
    Собирает атлас из списка изображений.
    Каждый спрайт сохраняет оригинальный размер.
    Раскладка: по сетке columns × rows.
    """
    images = []
    for p in image_paths:
        if p.suffix.lower() == ".svg":
            # SVG → временный PNG в памяти
            import io
            data = cairosvg.svg2png(url=str(p))
            img = Image.open(io.BytesIO(data)).convert("RGBA")
        else:
            img = Image.open(p).convert("RGBA")
        images.append((p.stem, img))
        if log_fn:
            log_fn(f"  загружен: {p.name}  {img.size[0]}×{img.size[1]}")

    if not images:
        raise ValueError("Нет изображений для атласа")

    cols = max(1, columns)
    rows = math.ceil(len(images) / cols)

    # размер ячейки = максимальный спрайт
    cell_w = max(img.size[0] for _, img in images)
    cell_h = max(img.size[1] for _, img in images)

    atlas_w = cols * cell_w + (cols + 1) * padding
    atlas_h = rows * cell_h + (rows + 1) * padding

    atlas = Image.new("RGBA", (atlas_w, atlas_h), bg_color)

    for idx, (name, img) in enumerate(images):
        col = idx % cols
        row = idx // cols
        x = padding + col * (cell_w + padding)
        y = padding + row * (cell_h + padding)
        # центрируем спрайт в ячейке
        ox = (cell_w - img.size[0]) // 2
        oy = (cell_h - img.size[1]) // 2
        atlas.paste(img, (x + ox, y + oy), img)

    atlas.save(out_path, format="PNG", optimize=True)
    if log_fn:
        log_fn(f"\n✓ Атлас сохранён: {out_path.name}")
        log_fn(f"  Размер: {atlas_w}×{atlas_h}px  |  {len(images)} спрайтов  |  {cols}×{rows} сетка")
        log_fn(f"  Файл: {out_path.stat().st_size // 1024} KB")


# ─── Вспомогательный виджет: поле + кнопки ───────────────────────────────────

def make_entry_row(parent, row, label, var, btn_configs, PAD):
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", **PAD)
    tk.Entry(parent, textvariable=var, width=40,
             bg=ENTRY_BG, fg=FG, insertbackground=FG,
             relief="flat", font=("Segoe UI", 10)).grid(row=row, column=1, **PAD)
    frame = tk.Frame(parent, bg=BG)
    frame.grid(row=row, column=2, padx=(0, 12))
    for text, cmd in btn_configs:
        ttk.Button(frame, text=text, command=cmd).pack(side="left", padx=2)


# ─── Главное окно с вкладками ─────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SVG Tools")
        self.resizable(False, False)
        self.configure(bg=BG)
        self._setup_styles()
        self._build_ui()

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TLabel",    background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("TButton",   background=BTN_BG, foreground=FG, font=("Segoe UI", 10), borderwidth=0)
        style.map("TButton",         background=[("active", "#585b70")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#1e1e2e",
                        font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton",  background=[("active", "#74c7ec")])
        style.configure("TProgressbar", troughcolor=ENTRY_BG, background=ACCENT, borderwidth=0)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BTN_BG, foreground=FG,
                        font=("Segoe UI", 10), padding=(14, 6))
        style.map("TNotebook.Tab",   background=[("selected", ENTRY_BG)],
                  foreground=[("selected", ACCENT)])
        style.configure("TCheckbutton", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.map("TCheckbutton",    background=[("active", BG)])
        style.configure("TSpinbox",  fieldbackground=ENTRY_BG, foreground=FG,
                        background=BTN_BG, font=("Segoe UI", 10))

    def _build_ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        tab1 = tk.Frame(nb, bg=BG)
        tab2 = tk.Frame(nb, bg=BG)
        nb.add(tab1, text="  SVG → PNG  ")
        nb.add(tab2, text="  Атлас спрайтов  ")

        self._build_converter_tab(tab1)
        self._build_atlas_tab(tab2)

    # ══════════════════════════════════════════════════════════════════════════
    # Вкладка 1: SVG конвертер
    # ══════════════════════════════════════════════════════════════════════════

    def _build_converter_tab(self, parent):
        PAD = {"padx": 12, "pady": 6}

        tk.Label(parent, text="SVG → PNG + Оптимизация + Normal Map",
                 bg=BG, fg=ACCENT, font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=3, pady=(14, 6), padx=16)

        self.cv_input_var = tk.StringVar()
        make_entry_row(parent, 1, "Файлы или папка:", self.cv_input_var,
                       [("Файлы", self._cv_pick_files), ("Папка", self._cv_pick_folder)], PAD)

        self.cv_output_var = tk.StringVar(value="(рядом с исходниками)")
        make_entry_row(parent, 2, "Сохранить в:", self.cv_output_var,
                       [("Выбрать", self._cv_pick_output)], PAD)

        # DPI
        ttk.Label(parent, text="DPI:").grid(row=3, column=0, sticky="w", **PAD)
        self.cv_dpi_var = tk.IntVar(value=96)
        dpi_frame = tk.Frame(parent, bg=BG)
        dpi_frame.grid(row=3, column=1, sticky="w", **PAD)
        for val in (72, 96, 144, 192):
            tk.Radiobutton(dpi_frame, text=str(val), variable=self.cv_dpi_var, value=val,
                           bg=BG, fg=FG, selectcolor=ENTRY_BG,
                           activebackground=BG, activeforeground=ACCENT,
                           font=("Segoe UI", 10)).pack(side="left", padx=6)

        # Сила нормали
        ttk.Label(parent, text="Сила нормали:").grid(row=4, column=0, sticky="w", **PAD)
        self.cv_strength_var = tk.DoubleVar(value=4.0)
        sl_frame = tk.Frame(parent, bg=BG)
        sl_frame.grid(row=4, column=1, sticky="w", **PAD)
        self.cv_strength_lbl = tk.Label(sl_frame, text="4.0", width=4,
                                        bg=BG, fg=ACCENT, font=("Segoe UI", 10, "bold"))
        self.cv_strength_lbl.pack(side="right")
        tk.Scale(sl_frame, from_=0.5, to=20.0, resolution=0.5,
                 orient="horizontal", variable=self.cv_strength_var, length=220,
                 bg=BG, fg=FG, troughcolor=ENTRY_BG, highlightthickness=0,
                 activebackground=ACCENT, sliderrelief="flat",
                 command=lambda v: self.cv_strength_lbl.config(text=f"{float(v):.1f}")
                 ).pack(side="left")

        self.cv_progress = ttk.Progressbar(parent, length=400, mode="determinate")
        self.cv_progress.grid(row=5, column=0, columnspan=3, padx=16, pady=(10, 4))

        self.cv_log = self._make_log(parent, row=6)

        self.cv_run_btn = ttk.Button(parent, text="▶  Конвертировать",
                                     style="Accent.TButton", command=self._cv_start)
        self.cv_run_btn.grid(row=7, column=0, columnspan=3, pady=(8, 16), ipadx=20, ipady=6)

    def _cv_pick_files(self):
        files = filedialog.askopenfilenames(filetypes=[("SVG files", "*.svg")])
        if files:
            self.cv_input_var.set(";".join(files))

    def _cv_pick_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.cv_input_var.set(folder)

    def _cv_pick_output(self):
        folder = filedialog.askdirectory()
        if folder:
            self.cv_output_var.set(folder)

    def _cv_start(self):
        raw = self.cv_input_var.get().strip()
        if not raw:
            messagebox.showwarning("Нет файлов", "Выберите файлы или папку.")
            return
        svgs = collect_svgs(raw.split(";"))
        if not svgs:
            messagebox.showwarning("Нет SVG", "SVG-файлы не найдены.")
            return
        out_raw = self.cv_output_var.get().strip()
        output_dir = None if out_raw == "(рядом с исходниками)" else Path(out_raw)
        self.cv_run_btn.configure(state="disabled")
        self.cv_progress["value"] = 0
        self.cv_progress["maximum"] = len(svgs)
        self._clear_log(self.cv_log)
        threading.Thread(target=self._cv_pipeline,
                         args=(svgs, output_dir, self.cv_dpi_var.get(), self.cv_strength_var.get()),
                         daemon=True).start()

    def _cv_pipeline(self, svgs, output_dir, dpi, strength):
        errors = 0
        for i, svg in enumerate(svgs, 1):
            try:
                out_dir = output_dir or svg.parent
                out_dir.mkdir(parents=True, exist_ok=True)
                png_path    = out_dir / svg.with_suffix(".png").name
                normal_path = out_dir / (svg.stem + "_normal.png")
                self._log(self.cv_log, f"▶ {svg.name}")
                svg_to_png(svg, png_path, dpi)
                self._log(self.cv_log, f"  ✓ конвертирован → {png_path.name}")
                orig, new = optimize_png(png_path)
                pct = (orig - new) / orig * 100 if orig else 0
                self._log(self.cv_log, f"  ✓ оптимизирован  {orig//1024}KB → {new//1024}KB  (−{pct:.1f}%)")
                generate_normal_map(png_path, normal_path, strength)
                self._log(self.cv_log, f"  ✓ normal map     → {normal_path.name}")
            except Exception as e:
                self._log(self.cv_log, f"  ✗ Ошибка: {e}")
                errors += 1
            self.cv_progress["value"] = i
        self._log(self.cv_log, f"\n✓ Готово! {len(svgs)-errors}/{len(svgs)}" +
                  (f"  ({errors} ошибок)" if errors else ""))
        self.cv_run_btn.configure(state="normal")

    # ══════════════════════════════════════════════════════════════════════════
    # Вкладка 2: Атлас спрайтов
    # ══════════════════════════════════════════════════════════════════════════

    def _build_atlas_tab(self, parent):
        PAD = {"padx": 12, "pady": 6}

        tk.Label(parent, text="Сборка атласа спрайтов",
                 bg=BG, fg=ACCENT, font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=3, pady=(14, 6), padx=16)

        # Входные файлы
        self.at_input_var = tk.StringVar()
        make_entry_row(parent, 1, "Файлы или папка:", self.at_input_var,
                       [("Файлы", self._at_pick_files), ("Папка", self._at_pick_folder)], PAD)

        # Выходной файл
        ttk.Label(parent, text="Сохранить атлас:").grid(row=2, column=0, sticky="w", **PAD)
        self.at_output_var = tk.StringVar(value="atlas.png")
        tk.Entry(parent, textvariable=self.at_output_var, width=40,
                 bg=ENTRY_BG, fg=FG, insertbackground=FG,
                 relief="flat", font=("Segoe UI", 10)).grid(row=2, column=1, **PAD)
        ttk.Button(parent, text="Выбрать", command=self._at_pick_output).grid(
            row=2, column=2, padx=(0, 12))

        # Отступ между спрайтами
        ttk.Label(parent, text="Отступ (px):").grid(row=3, column=0, sticky="w", **PAD)
        self.at_padding_var = tk.IntVar(value=4)
        pad_frame = tk.Frame(parent, bg=BG)
        pad_frame.grid(row=3, column=1, sticky="w", **PAD)
        self.at_padding_lbl = tk.Label(pad_frame, text="4", width=3,
                                       bg=BG, fg=ACCENT, font=("Segoe UI", 10, "bold"))
        self.at_padding_lbl.pack(side="right")
        tk.Scale(pad_frame, from_=0, to=64, resolution=1,
                 orient="horizontal", variable=self.at_padding_var, length=220,
                 bg=BG, fg=FG, troughcolor=ENTRY_BG, highlightthickness=0,
                 activebackground=ACCENT, sliderrelief="flat",
                 command=lambda v: self.at_padding_lbl.config(text=str(int(float(v))))
                 ).pack(side="left")

        # Количество колонок
        ttk.Label(parent, text="Колонок:").grid(row=4, column=0, sticky="w", **PAD)
        self.at_cols_var = tk.IntVar(value=8)
        cols_frame = tk.Frame(parent, bg=BG)
        cols_frame.grid(row=4, column=1, sticky="w", **PAD)
        self.at_cols_lbl = tk.Label(cols_frame, text="8", width=3,
                                    bg=BG, fg=ACCENT, font=("Segoe UI", 10, "bold"))
        self.at_cols_lbl.pack(side="right")
        tk.Scale(cols_frame, from_=1, to=32, resolution=1,
                 orient="horizontal", variable=self.at_cols_var, length=220,
                 bg=BG, fg=FG, troughcolor=ENTRY_BG, highlightthickness=0,
                 activebackground=ACCENT, sliderrelief="flat",
                 command=lambda v: self.at_cols_lbl.config(text=str(int(float(v))))
                 ).pack(side="left")

        # Цвет фона
        ttk.Label(parent, text="Фон:").grid(row=5, column=0, sticky="w", **PAD)
        bg_frame = tk.Frame(parent, bg=BG)
        bg_frame.grid(row=5, column=1, sticky="w", **PAD)
        self.at_bg_var = tk.StringVar(value="transparent")
        for val, label in [("transparent", "Прозрачный"), ("white", "Белый"), ("black", "Чёрный")]:
            tk.Radiobutton(bg_frame, text=label, variable=self.at_bg_var, value=val,
                           bg=BG, fg=FG, selectcolor=ENTRY_BG,
                           activebackground=BG, activeforeground=ACCENT,
                           font=("Segoe UI", 10)).pack(side="left", padx=6)

        self.at_progress = ttk.Progressbar(parent, length=400, mode="indeterminate")
        self.at_progress.grid(row=6, column=0, columnspan=3, padx=16, pady=(10, 4))

        self.at_log = self._make_log(parent, row=7)

        self.at_run_btn = ttk.Button(parent, text="▶  Собрать атлас",
                                     style="Accent.TButton", command=self._at_start)
        self.at_run_btn.grid(row=8, column=0, columnspan=3, pady=(8, 16), ipadx=20, ipady=6)

    def _at_pick_files(self):
        files = filedialog.askopenfilenames(
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.svg"),
                       ("All files", "*.*")])
        if files:
            self.at_input_var.set(";".join(files))

    def _at_pick_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.at_input_var.set(folder)

    def _at_pick_output(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png")])
        if path:
            self.at_output_var.set(path)

    def _at_start(self):
        raw = self.at_input_var.get().strip()
        if not raw:
            messagebox.showwarning("Нет файлов", "Выберите файлы или папку.")
            return
        images = collect_images(raw.split(";"))
        if not images:
            messagebox.showwarning("Нет изображений", "Изображения не найдены.")
            return
        out = self.at_output_var.get().strip()
        if not out:
            messagebox.showwarning("Нет пути", "Укажите файл для сохранения атласа.")
            return

        bg_map = {"transparent": (0, 0, 0, 0), "white": (255, 255, 255, 255),
                  "black": (0, 0, 0, 255)}
        bg_color = bg_map[self.at_bg_var.get()]

        self.at_run_btn.configure(state="disabled")
        self.at_progress.start(12)
        self._clear_log(self.at_log)
        self._log(self.at_log, f"Найдено файлов: {len(images)}")

        threading.Thread(
            target=self._at_pipeline,
            args=(images, Path(out), self.at_padding_var.get(),
                  self.at_cols_var.get(), bg_color),
            daemon=True,
        ).start()

    def _at_pipeline(self, images, out_path, padding, columns, bg_color):
        try:
            build_atlas(images, out_path, padding, columns, bg_color,
                        log_fn=lambda m: self._log(self.at_log, m))
        except Exception as e:
            self._log(self.at_log, f"✗ Ошибка: {e}")
        finally:
            self.at_progress.stop()
            self.at_progress["value"] = 0
            self.at_run_btn.configure(state="normal")

    # ══════════════════════════════════════════════════════════════════════════
    # Общие утилиты
    # ══════════════════════════════════════════════════════════════════════════

    def _make_log(self, parent, row):
        frame = tk.Frame(parent, bg=ENTRY_BG)
        frame.grid(row=row, column=0, columnspan=3, padx=16, pady=4)
        log = tk.Text(frame, height=9, width=56, bg=ENTRY_BG, fg=FG,
                      font=("Consolas", 9), relief="flat", state="disabled",
                      insertbackground=FG)
        sb = tk.Scrollbar(frame, command=log.yview, bg=BTN_BG)
        log.configure(yscrollcommand=sb.set)
        log.pack(side="left")
        sb.pack(side="right", fill="y")
        return log

    def _log(self, widget, msg: str):
        widget.configure(state="normal")
        widget.insert("end", msg + "\n")
        widget.see("end")
        widget.configure(state="disabled")

    def _clear_log(self, widget):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.configure(state="disabled")


if __name__ == "__main__":
    app = App()
    app.mainloop()
