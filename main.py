from __future__ import annotations

import ctypes
import os
import platform
import queue
import subprocess
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from tkinter import messagebox, ttk

import psutil

APP_NAME = "Cubes Monitor"
BG = "#10101b"
PANEL = "#1b1b2b"
PANEL_ALT = "#24243a"
BORDER = "#34344e"
PURPLE = "#a78bfa"
GREEN = "#72e0b2"
ORANGE = "#ffc477"
TEXT = "#f3f0ff"
MUTED = "#aaa6c5"
RED = "#ff718b"


def is_admin() -> bool:
    if os.name != "nt":
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def format_bytes(value: float) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def format_rate(value: float) -> str:
    return format_bytes(value) + "/s"


def get_gpu_name() -> str:
    if os.name != "nt":
        return "GPU details are Windows-specific"
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_VideoController | ForEach-Object Name) -join '; '"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        names = result.stdout.strip()
        return names or "GPU name unavailable"
    except (OSError, subprocess.SubprocessError):
        return "GPU name unavailable"


def get_gpu_usage() -> float | None:
    """Best-effort total GPU engine utilization; unavailable on some systems."""
    if os.name != "nt":
        return None
    script = (
        "$c=Get-Counter '\\GPU Engine(*)\\Utilization Percentage' "
        "-ErrorAction Stop; "
        "$v=($c.CounterSamples | Measure-Object -Property CookedValue -Sum).Sum; "
        "if ($null -eq $v) { 'null' } else { [math]::Min(100,[math]::Max(0,$v)) }"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip().lower() != "null":
            return max(0.0, min(100.0, float(result.stdout.strip())))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


class MetricCard(tk.Frame):
    def __init__(self, master: tk.Misc, title: str):
        super().__init__(master, bg=PANEL, highlightthickness=1,
                         highlightbackground=BORDER)
        tk.Label(self, text=title.upper(), bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=14, pady=(12, 2))
        self.value = tk.Label(self, text="—", bg=PANEL, fg=TEXT,
                              font=("Segoe UI Semibold", 24))
        self.value.pack(anchor="w", padx=14)
        self.detail = tk.Label(self, text="Collecting data…", bg=PANEL, fg=MUTED,
                               font=("Segoe UI", 9))
        self.detail.pack(anchor="w", padx=14, pady=(0, 12))

    def set(self, value: str, detail: str) -> None:
        self.value.configure(text=value)
        self.detail.configure(text=detail)


class CubesMonitor:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1080x760")
        self.root.minsize(880, 650)
        self.root.configure(bg=BG)

        self.cpu_history = deque([0.0] * 60, maxlen=60)
        self.ram_history = deque([0.0] * 60, maxlen=60)
        self.previous_net = psutil.net_io_counters()
        self.previous_disk = psutil.disk_io_counters()
        self.previous_time = time.monotonic()
        self.process_sort = tk.StringVar(value="CPU")
        self.process_filter = tk.StringVar()
        self.gpu_name = get_gpu_name()
        self.gpu_usage: float | None = None
        self.gpu_queue: queue.Queue[float | None] = queue.Queue()
        self.gpu_probe_running = False
        self.last_gpu_probe = 0.0
        self.process_cpu_initialized = False

        self._configure_styles()
        self._build_ui()
        psutil.cpu_percent(interval=None)
        self.root.after(350, self.update_metrics)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _configure_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", background=PANEL_ALT, foreground=TEXT,
                        fieldbackground=PANEL_ALT, rowheight=27, borderwidth=0,
                        font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#34344e", foreground=TEXT,
                        relief="flat", font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#51417f")],
                  foreground=[("selected", "#ffffff")])

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 12))
        heading = tk.Frame(header, bg=BG)
        heading.pack(side="left")
        tk.Label(heading, text="CUBES, STUDIO.", bg=BG, fg=PURPLE,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(heading, text="Monitor", bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 27)).pack(anchor="w")
        self.status = tk.Label(header, text="● STARTING", bg=PANEL, fg=GREEN,
                               padx=13, pady=8, font=("Segoe UI", 9, "bold"))
        self.status.pack(side="right", anchor="center")

        cards = tk.Frame(self.root, bg=BG)
        cards.pack(fill="x", padx=24, pady=5)
        for col in range(4):
            cards.grid_columnconfigure(col, weight=1, uniform="metric")
        self.cpu_card = MetricCard(cards, "Processor")
        self.ram_card = MetricCard(cards, "Memory")
        self.gpu_card = MetricCard(cards, "Graphics")
        self.disk_card = MetricCard(cards, "System drive")
        for col, card in enumerate((self.cpu_card, self.ram_card,
                                    self.gpu_card, self.disk_card)):
            card.grid(row=0, column=col, sticky="nsew",
                      padx=(0 if col == 0 else 8, 0))

        middle = tk.Frame(self.root, bg=BG)
        middle.pack(fill="both", expand=True, padx=24, pady=(16, 9))
        middle.grid_columnconfigure(0, weight=3)
        middle.grid_columnconfigure(1, weight=2)
        middle.grid_rowconfigure(0, weight=1)

        graph_panel = tk.Frame(middle, bg=PANEL, highlightthickness=1,
                               highlightbackground=BORDER)
        graph_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        tk.Label(graph_panel, text="PERFORMANCE HISTORY", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=14, pady=(12, 5))
        self.graph = tk.Canvas(graph_panel, bg=PANEL, highlightthickness=0,
                               height=230)
        self.graph.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        info_panel = tk.Frame(middle, bg=PANEL, highlightthickness=1,
                              highlightbackground=BORDER)
        info_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        tk.Label(info_panel, text="SYSTEM INFORMATION", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=14, pady=(12, 7))
        self.info = tk.Text(info_panel, bg=PANEL, fg=TEXT, relief="flat",
                            wrap="word", font=("Segoe UI", 9), padx=14, pady=5,
                            insertbackground=TEXT, spacing3=5)
        self.info.pack(fill="both", expand=True)
        self.info.configure(state="disabled")

        process_panel = tk.Frame(self.root, bg=BG)
        process_panel.pack(fill="both", expand=True, padx=24, pady=(8, 12))
        tk.Label(process_panel, text="TOP PROCESSES", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 7))
        process_tools = tk.Frame(process_panel, bg=BG)
        process_tools.pack(fill="x", pady=(0, 6))
        tk.Label(process_tools, text="Filter:", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        filter_entry = tk.Entry(process_tools, textvariable=self.process_filter,
                                bg=PANEL_ALT, fg=TEXT, insertbackground=TEXT,
                                relief="flat", font=("Segoe UI", 9), width=25)
        filter_entry.pack(side="left", padx=(7, 14), ipady=4)
        tk.Label(process_tools, text="Sort by:", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        sort_box = ttk.Combobox(process_tools, textvariable=self.process_sort,
                                values=("CPU", "Memory"), state="readonly",
                                width=10)
        sort_box.pack(side="left", padx=7)
        sort_box.bind("<<ComboboxSelected>>", lambda _event: self._update_processes())

        columns = ("name", "pid", "cpu", "memory")
        self.processes = ttk.Treeview(process_panel, columns=columns,
                                      show="headings", height=7)
        specs = (("name", "PROCESS", 390), ("pid", "PID", 90),
                 ("cpu", "CPU %", 110), ("memory", "MEMORY", 140))
        for key, label, width in specs:
            self.processes.heading(key, text=label)
            self.processes.column(key, width=width,
                                  anchor="w" if key == "name" else "center")
        self.processes.pack(fill="both", expand=True)

        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=24, pady=(0, 17))
        tk.Button(footer, text="Administrator details",
                  command=self.show_admin_info, bg=PANEL_ALT, fg=TEXT,
                  activebackground="#393252", activeforeground=TEXT,
                  relief="flat", padx=12, pady=7,
                  font=("Segoe UI", 9)).pack(side="left")
        tk.Label(footer, text="LOCAL ONLY  •  NO ACCOUNT  •  NO TELEMETRY",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(side="right")

    def show_admin_info(self) -> None:
        if is_admin():
            message = ("Cubes Monitor is running with administrator privileges. "
                       "Windows granted elevation through its normal UAC process.")
        else:
            message = (
                "Cubes Monitor is running as a standard user. CPU, memory, storage, "
                "and network monitoring do not normally require elevation. Windows "
                "may hide details for protected processes.\n\n"
                "For an elevated edition, package this project with CubesEPA's "
                "--require-admin option. CubesEPA should display its safety warning "
                "and then request consent through the Windows UAC prompt. This app "
                "does not bypass or silently dismiss UAC."
            )
        messagebox.showinfo("Administrator permissions", message, parent=self.root)

    def _start_gpu_probe(self) -> None:
        if os.name != "nt" or self.gpu_probe_running:
            return
        self.gpu_probe_running = True

        def worker() -> None:
            self.gpu_queue.put(get_gpu_usage())

        threading.Thread(target=worker, name="GPU-counter-probe", daemon=True).start()

    def _draw_graph(self) -> None:
        canvas = self.graph
        canvas.delete("all")
        width = max(canvas.winfo_width(), 320)
        height = max(canvas.winfo_height(), 180)
        left, right, top, bottom = 36, width - 14, 22, height - 25
        for value in (0, 25, 50, 75, 100):
            y = bottom - (bottom - top) * value / 100
            canvas.create_line(left, y, right, y, fill=BORDER)
            canvas.create_text(left - 8, y, text=str(value), fill=MUTED,
                               anchor="e", font=("Segoe UI", 8))

        def draw(values: deque[float], color: str) -> None:
            points: list[float] = []
            for index, value in enumerate(values):
                x = left + (right - left) * index / (len(values) - 1)
                y = bottom - (bottom - top) * max(0, min(100, value)) / 100
                points.extend((x, y))
            canvas.create_line(*points, fill=color, width=2, smooth=True)

        draw(self.cpu_history, PURPLE)
        draw(self.ram_history, GREEN)
        canvas.create_text(right, 8, text="CPU", fill=PURPLE, anchor="ne",
                           font=("Segoe UI", 8, "bold"))
        canvas.create_text(right - 42, 8, text="RAM", fill=GREEN, anchor="ne",
                           font=("Segoe UI", 8, "bold"))

    def _update_info(self, download: float, upload: float,
                     disk_read: float, disk_write: float) -> None:
        boot = datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M")
        frequency = psutil.cpu_freq()
        frequency_text = f"{frequency.current / 1000:.2f} GHz" if frequency and frequency.current else "Unavailable"
        lines = [
            f"Device: {platform.node()}",
            f"Operating system: {platform.system()} {platform.release()}",
            f"Processor: {platform.processor() or 'Unknown'}",
            f"Logical CPUs: {psutil.cpu_count(logical=True) or 'Unknown'}",
            f"Physical cores: {psutil.cpu_count(logical=False) or 'Unknown'}",
            f"Current CPU frequency: {frequency_text}",
            f"Boot time: {boot}",
            f"Administrator: {'Yes' if is_admin() else 'No'}",
            "",
            f"Network download: {format_rate(download)}",
            f"Network upload: {format_rate(upload)}",
            f"Disk read: {format_rate(disk_read)}",
            f"Disk write: {format_rate(disk_write)}",
        ]
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        self.info.insert("1.0", "\n".join(lines))
        self.info.configure(state="disabled")

    def _update_processes(self) -> None:
        rows = []
        for process in psutil.process_iter(
            ["pid", "name", "memory_info"], ad_value=None
        ):
            try:
                info = process.info
                cpu = process.cpu_percent(interval=None)
                memory_info = info.get("memory_info")
                memory = memory_info.rss if memory_info else 0
                rows.append((info.get("name") or "Unknown",
                             info.get("pid") or 0, cpu, memory))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        needle = self.process_filter.get().strip().casefold()
        if needle:
            rows = [row for row in rows if needle in row[0].casefold()]
        sort_index = 3 if self.process_sort.get() == "Memory" else 2
        rows.sort(key=lambda item: item[sort_index], reverse=True)
        self.processes.delete(*self.processes.get_children())
        for name, pid, cpu, memory in rows[:30]:
            self.processes.insert("", "end", values=(
                name[:52], pid, f"{cpu:.1f}", format_bytes(memory)
            ))

    def update_metrics(self) -> None:
        try:
            cpu = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage(os.path.abspath(os.sep))
            network = psutil.net_io_counters()
            disk_io = psutil.disk_io_counters()
            now = time.monotonic()
            elapsed = max(now - self.previous_time, 0.001)
            download = max(0, network.bytes_recv - self.previous_net.bytes_recv) / elapsed
            upload = max(0, network.bytes_sent - self.previous_net.bytes_sent) / elapsed
            disk_read = disk_write = 0.0
            if disk_io is not None and self.previous_disk is not None:
                disk_read = max(0, disk_io.read_bytes - self.previous_disk.read_bytes) / elapsed
                disk_write = max(0, disk_io.write_bytes - self.previous_disk.write_bytes) / elapsed
            self.previous_net = network
            self.previous_disk = disk_io
            self.previous_time = now

            while True:
                try:
                    self.gpu_usage = self.gpu_queue.get_nowait()
                    self.gpu_probe_running = False
                except queue.Empty:
                    break
            if now - self.last_gpu_probe >= 10:
                self.last_gpu_probe = now
                self._start_gpu_probe()

            self.cpu_history.append(cpu)
            self.ram_history.append(memory.percent)
            self.cpu_card.set(f"{cpu:.0f}%", platform.processor()[:38] or "CPU utilization")
            self.ram_card.set(f"{memory.percent:.0f}%",
                              f"{format_bytes(memory.used)} / {format_bytes(memory.total)}")
            gpu_value = f"{self.gpu_usage:.0f}%" if self.gpu_usage is not None else "N/A"
            gpu_detail = self.gpu_name if self.gpu_usage is not None else (
                self.gpu_name + " • utilization unavailable"
            )
            self.gpu_card.set(gpu_value, gpu_detail[:58])
            self.disk_card.set(f"{disk.percent:.0f}%",
                               f"{format_bytes(disk.used)} / {format_bytes(disk.total)}")
            self.status.configure(
                text="● LIVE  •  ADMIN" if is_admin() else "● LIVE  •  STANDARD",
                fg=GREEN if is_admin() else PURPLE,
            )
            self._update_info(download, upload, disk_read, disk_write)
            self._draw_graph()
            self._update_processes()
        except (OSError, psutil.Error, tk.TclError) as error:
            self.status.configure(text=f"MONITORING ISSUE: {error}", fg=RED)
        finally:
            if self.root.winfo_exists():
                self.root.after(1200, self.update_metrics)

    def close(self) -> None:
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    CubesMonitor(root)
    root.mainloop()


if __name__ == "__main__":
    main()
