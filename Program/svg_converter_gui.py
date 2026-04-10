#!/usr/bin/env python3
"""
SVG → PNG Converter + Sprite Atlas Builder — GUI
Запуск: python svg_converter_gui.py
"""

import sys
import math
import re
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

def svg_to_png(svg_path: Path, out_path: Path, dpi: int, scale: float = 1.0) -> None:
    cairosvg.svg2png(url=str(svg_path), write_to=str(out_path), dpi=dpi, scale=scale)


def optimize_png(png_path: Path, quality: int = 80) -> tuple:
    """
    quality: 10..100
      - pngquant (lossy): quality range = (quality-15)..(quality)
      - fallback Pillow:  compress_level mapped 9→1 as quality 10→100
                          + quantize to reduce palette when quality < 90
    """
    original_size = png_path.stat().st_size
    q_min = max(10, quality - 15)
    q_max = quality

    pngquant_bin = shutil.which("pngquant")
    if pngquant_bin:
        tmp = png_path.with_suffix(".tmp.png")
        result = subprocess.run(
            [pngquant_bin, f"--quality={q_min}-{q_max}", "--force",
             "--output", str(tmp), str(png_path)],
            capture_output=True,
        )
        if result.returncode == 0 and tmp.exists():
            tmp.replace(png_path)
    else:
        # Fallback: Pillow — compress_level 9 (max) at low quality, 1 (fast) at high
        compress_level = max(1, min(9, round(9 - (quality - 10) * 8 / 90)))
        img = Image.open(png_path).convert("RGBA")
        if quality < 90:
            # Reduce to N colours proportional to quality
            n_colors = max(16, round(quality * 2.5))
            img = img.quantize(colors=n_colors, method=Image.Quantize.FASTOCTREE).convert("RGBA")
        img.save(png_path, format="PNG", optimize=True, compress_level=compress_level)

    oxipng_bin = shutil.which("oxipng")
    if oxipng_bin:
        subprocess.run([oxipng_bin, "--opt", "4", "--strip", "all", str(png_path)], capture_output=True)

    return original_size, png_path.stat().st_size


def generate_normal_map(png_path: Path, out_path: Path, strength: float, invert: bool = False) -> None:
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
    # invert=False → выпуклое (светлые области выступают вперёд)
    # invert=True  → вогнутое (светлые области уходят вглубь)
    sign = -1.0 if invert else 1.0
    nx = sign * (-gx / length)
    ny = sign * (-gy / length)
    nz = gz / length  # Z всегда смотрит на камеру
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
        tab3 = tk.Frame(nb, bg=BG)
        tab4 = tk.Frame(nb, bg=BG)
        nb.add(tab1, text="  SVG → PNG  ")
        nb.add(tab2, text="  Атлас спрайтов  ")
        nb.add(tab3, text="  Переименование  ")
        nb.add(tab4, text="  Normal Map  ")

        self._build_converter_tab(tab1)
        self._build_atlas_tab(tab2)
        self._build_rename_tab(tab3)
        self._build_normalmap_tab(tab4)

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

        # Масштаб (scale) — главный параметр размера PNG
        ttk.Label(parent, text="Масштаб:").grid(row=3, column=0, sticky="w", **PAD)
        self.cv_scale_var = tk.DoubleVar(value=1.0)
        scale_frame = tk.Frame(parent, bg=BG)
        scale_frame.grid(row=3, column=1, sticky="w", **PAD)
        self.cv_scale_lbl = tk.Label(scale_frame, text="1.0×", width=5,
                                     bg=BG, fg=ACCENT, font=("Segoe UI", 10, "bold"))
        self.cv_scale_lbl.pack(side="right")
        tk.Scale(scale_frame, from_=0.5, to=8.0, resolution=0.5,
                 orient="horizontal", variable=self.cv_scale_var, length=220,
                 bg=BG, fg=FG, troughcolor=ENTRY_BG, highlightthickness=0,
                 activebackground=ACCENT, sliderrelief="flat",
                 command=lambda v: self.cv_scale_lbl.config(text=f"{float(v):.1f}×")
                 ).pack(side="left")
        tk.Label(parent, text="← множитель размера: 2× = вдвое крупнее PNG",
                 bg=BG, fg="#6c7086", font=("Segoe UI", 8)).grid(
            row=3, column=2, sticky="w", padx=(0, 12))

        # DPI
        ttk.Label(parent, text="DPI:").grid(row=4, column=0, sticky="w", **PAD)
        self.cv_dpi_var = tk.IntVar(value=96)
        dpi_frame = tk.Frame(parent, bg=BG)
        dpi_frame.grid(row=4, column=1, sticky="w", **PAD)
        for val in (72, 96, 144, 192):
            tk.Radiobutton(dpi_frame, text=str(val), variable=self.cv_dpi_var, value=val,
                           bg=BG, fg=FG, selectcolor=ENTRY_BG,
                           activebackground=BG, activeforeground=ACCENT,
                           font=("Segoe UI", 10)).pack(side="left", padx=6)
        tk.Label(parent, text="← для SVG с физическими единицами (mm/pt); обычно 96",
                 bg=BG, fg="#6c7086", font=("Segoe UI", 8)).grid(
            row=4, column=2, sticky="w", padx=(0, 12))

        # Сила нормали
        ttk.Label(parent, text="Сила нормали:").grid(row=5, column=0, sticky="w", **PAD)
        self.cv_strength_var = tk.DoubleVar(value=4.0)
        sl_frame = tk.Frame(parent, bg=BG)
        sl_frame.grid(row=5, column=1, sticky="w", **PAD)
        self.cv_strength_lbl = tk.Label(sl_frame, text="4.0", width=4,
                                        bg=BG, fg=ACCENT, font=("Segoe UI", 10, "bold"))
        self.cv_strength_lbl.pack(side="right")
        tk.Scale(sl_frame, from_=0.5, to=20.0, resolution=0.5,
                 orient="horizontal", variable=self.cv_strength_var, length=220,
                 bg=BG, fg=FG, troughcolor=ENTRY_BG, highlightthickness=0,
                 activebackground=ACCENT, sliderrelief="flat",
                 command=lambda v: self.cv_strength_lbl.config(text=f"{float(v):.1f}")
                 ).pack(side="left")
        tk.Label(parent, text="← резкость рельефа на карте нормали",
                 bg=BG, fg="#6c7086", font=("Segoe UI", 8)).grid(
            row=5, column=2, sticky="w", padx=(0, 12))

        # Направление нормали: выпуклое / вогнутое
        ttk.Label(parent, text="Рельеф:").grid(row=6, column=0, sticky="w", **PAD)
        self.cv_invert_var = tk.BooleanVar(value=False)
        relief_frame = tk.Frame(parent, bg=BG)
        relief_frame.grid(row=6, column=1, sticky="w", **PAD)
        tk.Radiobutton(relief_frame, text="Выпуклое  (светлое = вперёд)",
                       variable=self.cv_invert_var, value=False,
                       bg=BG, fg=FG, selectcolor=ENTRY_BG,
                       activebackground=BG, activeforeground=ACCENT,
                       font=("Segoe UI", 10)).pack(side="left", padx=(0, 12))
        tk.Radiobutton(relief_frame, text="Вогнутое  (светлое = вглубь)",
                       variable=self.cv_invert_var, value=True,
                       bg=BG, fg=FG, selectcolor=ENTRY_BG,
                       activebackground=BG, activeforeground=ACCENT,
                       font=("Segoe UI", 10)).pack(side="left")

        # Качество сжатия
        ttk.Label(parent, text="Качество сжатия:").grid(row=7, column=0, sticky="w", **PAD)
        self.cv_quality_var = tk.IntVar(value=80)
        self.cv_no_compress_var = tk.BooleanVar(value=False)
        q_frame = tk.Frame(parent, bg=BG)
        q_frame.grid(row=7, column=1, sticky="w", **PAD)
        self.cv_quality_lbl = tk.Label(q_frame, text="80", width=4,
                                       bg=BG, fg=ACCENT, font=("Segoe UI", 10, "bold"))
        self.cv_quality_lbl.pack(side="right")
        self.cv_quality_scale = tk.Scale(q_frame, from_=10, to=100, resolution=5,
                 orient="horizontal", variable=self.cv_quality_var, length=220,
                 bg=BG, fg=FG, troughcolor=ENTRY_BG, highlightthickness=0,
                 activebackground=ACCENT, sliderrelief="flat",
                 command=lambda v: self.cv_quality_lbl.config(text=str(int(float(v))))
                 )
        self.cv_quality_scale.pack(side="left")
        tk.Label(parent, text="← 10 = мелкий файл / хуже  |  100 = крупный / лучше",
                 bg=BG, fg="#6c7086", font=("Segoe UI", 8)).grid(
            row=7, column=2, sticky="w", padx=(0, 12))

        def _toggle_compress():
            state = "disabled" if self.cv_no_compress_var.get() else "normal"
            self.cv_quality_scale.configure(state=state)
            self.cv_quality_lbl.configure(fg="#6c7086" if self.cv_no_compress_var.get() else ACCENT)

        no_compress_frame = tk.Frame(parent, bg=BG)
        no_compress_frame.grid(row=7, column=2, sticky="e", padx=(0, 12))
        ttk.Checkbutton(no_compress_frame, text="Без сжатия",
                        variable=self.cv_no_compress_var,
                        command=_toggle_compress).pack(side="right")

        # Удалить исходник
        self.cv_delete_src_var = tk.BooleanVar(value=False)
        del_frame = tk.Frame(parent, bg=BG)
        del_frame.grid(row=8, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 4))
        ttk.Checkbutton(del_frame, text="Удалить исходный SVG после конвертации",
                        variable=self.cv_delete_src_var).pack(side="left")

        self.cv_progress = ttk.Progressbar(parent, length=400, mode="determinate")
        self.cv_progress.grid(row=9, column=0, columnspan=3, padx=16, pady=(6, 4))

        self.cv_log = self._make_log(parent, row=10)

        self.cv_run_btn = ttk.Button(parent, text="▶  Конвертировать",
                                     style="Accent.TButton", command=self._cv_start)
        self.cv_run_btn.grid(row=11, column=0, columnspan=3, pady=(8, 16), ipadx=20, ipady=6)

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
                         args=(svgs, output_dir, self.cv_dpi_var.get(),
                               self.cv_scale_var.get(), self.cv_strength_var.get(),
                               self.cv_quality_var.get(), self.cv_invert_var.get(),
                               self.cv_no_compress_var.get(), self.cv_delete_src_var.get()),
                         daemon=True).start()

    def _cv_pipeline(self, svgs, output_dir, dpi, scale, strength, quality, invert, no_compress, delete_src):
        errors = 0
        for i, svg in enumerate(svgs, 1):
            try:
                out_dir = output_dir or svg.parent
                out_dir.mkdir(parents=True, exist_ok=True)
                png_path    = out_dir / svg.with_suffix(".png").name
                normal_path = out_dir / (svg.stem + "_normal.png")
                self._log(self.cv_log, f"▶ {svg.name}")
                svg_to_png(svg, png_path, dpi, scale)
                img_size = Image.open(png_path).size
                self._log(self.cv_log, f"  ✓ конвертирован → {png_path.name}  ({img_size[0]}×{img_size[1]}px)")
                if no_compress:
                    self._log(self.cv_log, f"  — сжатие пропущено")
                else:
                    orig, new = optimize_png(png_path, quality)
                    pct = (orig - new) / orig * 100 if orig else 0
                    self._log(self.cv_log, f"  ✓ оптимизирован  {orig//1024}KB → {new//1024}KB  (−{pct:.1f}%)")
                generate_normal_map(png_path, normal_path, strength, invert)
                self._log(self.cv_log, f"  ✓ normal map     → {normal_path.name}")
                if delete_src:
                    svg.unlink()
                    self._log(self.cv_log, f"  🗑 исходник удалён: {svg.name}")
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
    # Вкладка 3: Переименование файлов
    # ══════════════════════════════════════════════════════════════════════════

    def _build_rename_tab(self, parent):
        PAD = {"padx": 12, "pady": 6}

        tk.Label(parent, text="Переименование файлов",
                 bg=BG, fg=ACCENT, font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=3, pady=(14, 6), padx=16)

        # Источник: файлы или папка
        self.rn_input_var = tk.StringVar()
        make_entry_row(parent, 1, "Файлы или папка:", self.rn_input_var,
                       [("Файлы", self._rn_pick_files), ("Папка", self._rn_pick_folder)], PAD)

        # Фильтр расширений
        ttk.Label(parent, text="Расширения:").grid(row=2, column=0, sticky="w", **PAD)
        self.rn_ext_var = tk.StringVar(value="*")
        ext_entry = tk.Entry(parent, textvariable=self.rn_ext_var, width=20,
                             bg=ENTRY_BG, fg=FG, insertbackground=FG,
                             relief="flat", font=("Segoe UI", 10))
        ext_entry.grid(row=2, column=1, sticky="w", **PAD)
        tk.Label(parent, text="напр.: .png .svg  или  * для всех",
                 bg=BG, fg="#6c7086", font=("Segoe UI", 8)).grid(
            row=2, column=2, sticky="w", padx=(0, 12))

        # Шаблон: найти
        ttk.Label(parent, text="Найти:").grid(row=3, column=0, sticky="w", **PAD)
        self.rn_find_var = tk.StringVar()
        tk.Entry(parent, textvariable=self.rn_find_var, width=40,
                 bg=ENTRY_BG, fg=FG, insertbackground=FG,
                 relief="flat", font=("Segoe UI", 10)).grid(row=3, column=1, **PAD)

        # Шаблон: заменить
        ttk.Label(parent, text="Заменить на:").grid(row=4, column=0, sticky="w", **PAD)
        self.rn_replace_var = tk.StringVar()
        tk.Entry(parent, textvariable=self.rn_replace_var, width=40,
                 bg=ENTRY_BG, fg=FG, insertbackground=FG,
                 relief="flat", font=("Segoe UI", 10)).grid(row=4, column=1, **PAD)

        # Опции
        opts_frame = tk.Frame(parent, bg=BG)
        opts_frame.grid(row=5, column=0, columnspan=3, sticky="w", padx=12, pady=4)
        self.rn_regex_var = tk.BooleanVar(value=False)
        self.rn_case_var  = tk.BooleanVar(value=False)
        self.rn_prefix_var = tk.StringVar()
        self.rn_suffix_var = tk.StringVar()
        self.rn_counter_var = tk.BooleanVar(value=False)
        self.rn_counter_start_var = tk.IntVar(value=1)

        ttk.Checkbutton(opts_frame, text="Regex", variable=self.rn_regex_var).pack(side="left", padx=6)
        ttk.Checkbutton(opts_frame, text="Учитывать регистр", variable=self.rn_case_var).pack(side="left", padx=6)
        ttk.Checkbutton(opts_frame, text="Нумерация", variable=self.rn_counter_var,
                        command=self._rn_update_preview).pack(side="left", padx=6)
        tk.Label(opts_frame, text="с:", bg=BG, fg=FG, font=("Segoe UI", 10)).pack(side="left")
        tk.Spinbox(opts_frame, textvariable=self.rn_counter_start_var,
                   from_=0, to=9999, width=5,
                   bg=ENTRY_BG, fg=FG, insertbackground=FG,
                   buttonbackground=BTN_BG, relief="flat",
                   font=("Segoe UI", 10)).pack(side="left", padx=4)

        # Префикс / суффикс
        ps_frame = tk.Frame(parent, bg=BG)
        ps_frame.grid(row=6, column=0, columnspan=3, sticky="w", padx=12, pady=2)
        tk.Label(ps_frame, text="Префикс:", bg=BG, fg=FG, font=("Segoe UI", 10)).pack(side="left")
        tk.Entry(ps_frame, textvariable=self.rn_prefix_var, width=14,
                 bg=ENTRY_BG, fg=FG, insertbackground=FG,
                 relief="flat", font=("Segoe UI", 10)).pack(side="left", padx=(4, 16))
        tk.Label(ps_frame, text="Суффикс:", bg=BG, fg=FG, font=("Segoe UI", 10)).pack(side="left")
        tk.Entry(ps_frame, textvariable=self.rn_suffix_var, width=14,
                 bg=ENTRY_BG, fg=FG, insertbackground=FG,
                 relief="flat", font=("Segoe UI", 10)).pack(side="left", padx=4)

        # Кнопка предпросмотра
        btn_row = tk.Frame(parent, bg=BG)
        btn_row.grid(row=7, column=0, columnspan=3, pady=(6, 2))
        ttk.Button(btn_row, text="🔍  Предпросмотр", command=self._rn_update_preview).pack(side="left", padx=6)
        ttk.Button(btn_row, text="✖  Очистить", command=self._rn_clear).pack(side="left", padx=6)

        # Таблица предпросмотра
        preview_frame = tk.Frame(parent, bg=ENTRY_BG)
        preview_frame.grid(row=8, column=0, columnspan=3, padx=16, pady=4)

        cols = ("old", "new")
        self.rn_tree = ttk.Treeview(preview_frame, columns=cols, show="headings",
                                    height=8, selectmode="none")
        self.rn_tree.heading("old", text="Было")
        self.rn_tree.heading("new", text="Станет")
        self.rn_tree.column("old", width=240, anchor="w")
        self.rn_tree.column("new", width=240, anchor="w")

        tree_style = ttk.Style()
        tree_style.configure("Treeview", background=ENTRY_BG, foreground=FG,
                              fieldbackground=ENTRY_BG, font=("Consolas", 9))
        tree_style.configure("Treeview.Heading", background=BTN_BG, foreground=ACCENT,
                              font=("Segoe UI", 9, "bold"))
        tree_style.map("Treeview", background=[("selected", "#45475a")])

        vsb = tk.Scrollbar(preview_frame, orient="vertical", command=self.rn_tree.yview,
                           bg=BTN_BG)
        self.rn_tree.configure(yscrollcommand=vsb.set)
        self.rn_tree.pack(side="left")
        vsb.pack(side="right", fill="y")

        # Статус
        self.rn_status_var = tk.StringVar(value="")
        tk.Label(parent, textvariable=self.rn_status_var,
                 bg=BG, fg="#a6e3a1", font=("Segoe UI", 9)).grid(
            row=9, column=0, columnspan=3, pady=(2, 0))

        # Кнопка запуска
        self.rn_run_btn = ttk.Button(parent, text="▶  Переименовать",
                                     style="Accent.TButton", command=self._rn_start)
        self.rn_run_btn.grid(row=10, column=0, columnspan=3, pady=(6, 16), ipadx=20, ipady=6)

        # Привязываем авто-обновление предпросмотра при изменении полей
        for var in (self.rn_find_var, self.rn_replace_var,
                    self.rn_prefix_var, self.rn_suffix_var,
                    self.rn_ext_var, self.rn_input_var):
            var.trace_add("write", lambda *_: self._rn_update_preview())

    def _rn_pick_files(self):
        files = filedialog.askopenfilenames(filetypes=[("Все файлы", "*.*")])
        if files:
            self.rn_input_var.set(";".join(files))

    def _rn_pick_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.rn_input_var.set(folder)

    def _rn_collect_files(self):
        raw = self.rn_input_var.get().strip()
        if not raw:
            return []
        ext_filter = [e.strip().lower() for e in self.rn_ext_var.get().split()
                      if e.strip() and e.strip() != "*"]
        result = []
        for p in raw.split(";"):
            path = Path(p)
            if path.is_dir():
                for f in sorted(path.iterdir()):
                    if f.is_file():
                        if not ext_filter or f.suffix.lower() in ext_filter:
                            result.append(f)
            elif path.is_file():
                if not ext_filter or path.suffix.lower() in ext_filter:
                    result.append(path)
        return result

    def _rn_compute_new_name(self, path: Path, index: int) -> str:
        stem = path.stem
        find    = self.rn_find_var.get()
        replace = self.rn_replace_var.get()
        use_regex = self.rn_regex_var.get()
        case_sens = self.rn_case_var.get()

        if find:
            try:
                if use_regex:
                    flags = 0 if case_sens else re.IGNORECASE
                    stem = re.sub(find, replace, stem, flags=flags)
                else:
                    if case_sens:
                        stem = stem.replace(find, replace)
                    else:
                        stem = re.sub(re.escape(find), replace, stem, flags=re.IGNORECASE)
            except re.error:
                pass  # невалидный regex — не трогаем

        prefix = self.rn_prefix_var.get()
        suffix = self.rn_suffix_var.get()

        if self.rn_counter_var.get():
            n = self.rn_counter_start_var.get() + index
            counter = f"_{n:03d}"
        else:
            counter = ""

        return f"{prefix}{stem}{suffix}{counter}{path.suffix}"

    def _rn_update_preview(self):
        for row in self.rn_tree.get_children():
            self.rn_tree.delete(row)
        files = self._rn_collect_files()
        if not files:
            self.rn_status_var.set("")
            return
        changed = 0
        for i, f in enumerate(files):
            new_name = self._rn_compute_new_name(f, i)
            tag = "changed" if new_name != f.name else "same"
            if new_name != f.name:
                changed += 1
            self.rn_tree.insert("", "end", values=(f.name, new_name), tags=(tag,))
        self.rn_tree.tag_configure("changed", foreground="#a6e3a1")
        self.rn_tree.tag_configure("same",    foreground="#6c7086")
        self.rn_status_var.set(f"Файлов: {len(files)}  |  Будет переименовано: {changed}")

    def _rn_clear(self):
        self.rn_find_var.set("")
        self.rn_replace_var.set("")
        self.rn_prefix_var.set("")
        self.rn_suffix_var.set("")
        self.rn_counter_var.set(False)
        for row in self.rn_tree.get_children():
            self.rn_tree.delete(row)
        self.rn_status_var.set("")

    def _rn_start(self):
        files = self._rn_collect_files()
        if not files:
            messagebox.showwarning("Нет файлов", "Выберите файлы или папку.")
            return
        pairs = [(f, f.parent / self._rn_compute_new_name(f, i))
                 for i, f in enumerate(files)]
        to_rename = [(old, new) for old, new in pairs if old.name != new.name]
        if not to_rename:
            messagebox.showinfo("Нет изменений", "Все файлы уже имеют нужные имена.")
            return
        if not messagebox.askyesno("Подтверждение",
                                   f"Переименовать {len(to_rename)} файл(ов)?"):
            return
        errors = 0
        for old, new in to_rename:
            try:
                if new.exists() and new != old:
                    raise FileExistsError(f"Файл уже существует: {new.name}")
                old.rename(new)
            except Exception as e:
                errors += 1
                messagebox.showerror("Ошибка", str(e))
        done = len(to_rename) - errors
        self.rn_status_var.set(f"✓ Переименовано: {done}  |  Ошибок: {errors}")
        self._rn_update_preview()

    # ══════════════════════════════════════════════════════════════════════════
    # Вкладка 4: Normal Map из PNG
    # ══════════════════════════════════════════════════════════════════════════

    def _build_normalmap_tab(self, parent):
        PAD = {"padx": 12, "pady": 6}

        tk.Label(parent, text="Генерация Normal Map из PNG",
                 bg=BG, fg=ACCENT, font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=3, pady=(14, 6), padx=16)

        # Входные файлы
        self.nm_input_var = tk.StringVar()
        make_entry_row(parent, 1, "PNG файлы или папка:", self.nm_input_var,
                       [("Файлы", self._nm_pick_files), ("Папка", self._nm_pick_folder)], PAD)

        # Выходная папка
        self.nm_output_var = tk.StringVar(value="(рядом с исходниками)")
        make_entry_row(parent, 2, "Сохранить в:", self.nm_output_var,
                       [("Выбрать", self._nm_pick_output)], PAD)

        # Сила нормали
        ttk.Label(parent, text="Сила нормали:").grid(row=3, column=0, sticky="w", **PAD)
        self.nm_strength_var = tk.DoubleVar(value=4.0)
        nm_sl_frame = tk.Frame(parent, bg=BG)
        nm_sl_frame.grid(row=3, column=1, sticky="w", **PAD)
        self.nm_strength_lbl = tk.Label(nm_sl_frame, text="4.0", width=4,
                                        bg=BG, fg=ACCENT, font=("Segoe UI", 10, "bold"))
        self.nm_strength_lbl.pack(side="right")
        tk.Scale(nm_sl_frame, from_=0.5, to=20.0, resolution=0.5,
                 orient="horizontal", variable=self.nm_strength_var, length=220,
                 bg=BG, fg=FG, troughcolor=ENTRY_BG, highlightthickness=0,
                 activebackground=ACCENT, sliderrelief="flat",
                 command=lambda v: self.nm_strength_lbl.config(text=f"{float(v):.1f}")
                 ).pack(side="left")
        tk.Label(parent, text="← резкость рельефа",
                 bg=BG, fg="#6c7086", font=("Segoe UI", 8)).grid(
            row=3, column=2, sticky="w", padx=(0, 12))

        # Рельеф
        ttk.Label(parent, text="Рельеф:").grid(row=4, column=0, sticky="w", **PAD)
        self.nm_invert_var = tk.BooleanVar(value=False)
        nm_relief_frame = tk.Frame(parent, bg=BG)
        nm_relief_frame.grid(row=4, column=1, sticky="w", **PAD)
        tk.Radiobutton(nm_relief_frame, text="Выпуклое  (светлое = вперёд)",
                       variable=self.nm_invert_var, value=False,
                       bg=BG, fg=FG, selectcolor=ENTRY_BG,
                       activebackground=BG, activeforeground=ACCENT,
                       font=("Segoe UI", 10)).pack(side="left", padx=(0, 12))
        tk.Radiobutton(nm_relief_frame, text="Вогнутое  (светлое = вглубь)",
                       variable=self.nm_invert_var, value=True,
                       bg=BG, fg=FG, selectcolor=ENTRY_BG,
                       activebackground=BG, activeforeground=ACCENT,
                       font=("Segoe UI", 10)).pack(side="left")

        # Суффикс выходного файла
        ttk.Label(parent, text="Суффикс имени:").grid(row=5, column=0, sticky="w", **PAD)
        self.nm_suffix_var = tk.StringVar(value="_normal")
        tk.Entry(parent, textvariable=self.nm_suffix_var, width=20,
                 bg=ENTRY_BG, fg=FG, insertbackground=FG,
                 relief="flat", font=("Segoe UI", 10)).grid(row=5, column=1, sticky="w", **PAD)
        tk.Label(parent, text="← напр. _normal → image_normal.png",
                 bg=BG, fg="#6c7086", font=("Segoe UI", 8)).grid(
            row=5, column=2, sticky="w", padx=(0, 12))

        self.nm_progress = ttk.Progressbar(parent, length=400, mode="determinate")
        self.nm_progress.grid(row=6, column=0, columnspan=3, padx=16, pady=(10, 4))

        self.nm_log = self._make_log(parent, row=7)

        self.nm_run_btn = ttk.Button(parent, text="▶  Генерировать Normal Map",
                                     style="Accent.TButton", command=self._nm_start)
        self.nm_run_btn.grid(row=8, column=0, columnspan=3, pady=(8, 16), ipadx=20, ipady=6)

    def _nm_pick_files(self):
        files = filedialog.askopenfilenames(
            filetypes=[("PNG files", "*.png"), ("All images", "*.png *.jpg *.jpeg *.bmp")])
        if files:
            self.nm_input_var.set(";".join(files))

    def _nm_pick_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.nm_input_var.set(folder)

    def _nm_pick_output(self):
        folder = filedialog.askdirectory()
        if folder:
            self.nm_output_var.set(folder)

    def _nm_start(self):
        raw = self.nm_input_var.get().strip()
        if not raw:
            messagebox.showwarning("Нет файлов", "Выберите PNG файлы или папку.")
            return
        exts = {".png", ".jpg", ".jpeg", ".bmp"}
        pngs = []
        for p in raw.split(";"):
            path = Path(p)
            if path.is_dir():
                for ext in exts:
                    pngs.extend(sorted(path.glob(f"**/*{ext}")))
            elif path.suffix.lower() in exts:
                pngs.append(path)
        if not pngs:
            messagebox.showwarning("Нет файлов", "PNG-файлы не найдены.")
            return
        out_raw = self.nm_output_var.get().strip()
        output_dir = None if out_raw == "(рядом с исходниками)" else Path(out_raw)
        self.nm_run_btn.configure(state="disabled")
        self.nm_progress["value"] = 0
        self.nm_progress["maximum"] = len(pngs)
        self._clear_log(self.nm_log)
        threading.Thread(
            target=self._nm_pipeline,
            args=(pngs, output_dir, self.nm_strength_var.get(),
                  self.nm_invert_var.get(), self.nm_suffix_var.get()),
            daemon=True).start()

    def _nm_pipeline(self, pngs, output_dir, strength, invert, suffix):
        errors = 0
        for i, png in enumerate(pngs, 1):
            try:
                out_dir = output_dir or png.parent
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / (png.stem + suffix + ".png")
                self._log(self.nm_log, f"▶ {png.name}")
                generate_normal_map(png, out_path, strength, invert)
                self._log(self.nm_log, f"  ✓ → {out_path.name}")
            except Exception as e:
                self._log(self.nm_log, f"  ✗ Ошибка: {e}")
                errors += 1
            self.nm_progress["value"] = i
        self._log(self.nm_log, f"\n✓ Готово! {len(pngs)-errors}/{len(pngs)}" +
                  (f"  ({errors} ошибок)" if errors else ""))
        self.nm_run_btn.configure(state="normal")

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
