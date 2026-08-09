"""Tk desktop interface for safe homebrew management."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # Browse remains available in source-only environments.
    DND_FILES = None
    TkinterDnD = None

from .device import DeviceSession
from .elevation import invoking_user_home
from .catalog import load_hbi
from .resources import launcher_bytes
from .service import (
    DMODE_PATH,
    HB_DIRECTORY,
    ManagerError,
    RemoteEntry,
    SYSTEM_BACKUP_NAME,
    add_homebrew,
    delete_homebrew,
    discover_system_path,
    install_or_update_launcher,
    list_catalog,
    list_homebrew,
    rename_file,
    set_developer_mode,
    uninstall_homebrew,
)


RootClass = TkinterDnD.Tk if TkinterDnD is not None else tk.Tk


class HomebrewManager(RootClass):
    def __init__(self) -> None:
        super().__init__()
        self.title("MobiGo 2 Homebrew Manager")
        self.geometry("860x610")
        self.minsize(720, 500)
        self.configure(bg="#dff6ff")
        self.busy = False
        self._busy_widget_states: list[tuple[ttk.Widget, bool]] = []
        self.first_refresh = True
        self._style()
        self._header()
        self._body()
        self._loading_screen()
        self._status("Plug in your MobiGo 2 in USB mode, then choose Refresh.")
        self.after(250, self.refresh)

    def _style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#dff6ff")
        style.configure("TLabel", background="#dff6ff", foreground="#123b58")
        style.configure("Title.TLabel", font=("Arial", 18, "bold"))
        style.configure("TButton", padding=(10, 6))
        style.configure("Treeview", rowheight=28, fieldbackground="#f7fdff")
        style.configure("Treeview.Heading", font=("Arial", 10, "bold"))

    def _header(self) -> None:
        canvas = tk.Canvas(self, height=105, bg="#38b9ef", highlightthickness=0)
        canvas.pack(fill="x")
        canvas.create_polygon(
            0, 64, 70, 48, 145, 68, 225, 43, 320, 63, 410, 39,
            510, 61, 610, 44, 720, 67, 860, 45, 860, 105, 0, 105,
            fill="#8be7f5", outline="",
        )
        canvas.create_polygon(
            0, 81, 90, 67, 180, 91, 280, 65, 390, 87, 500, 66,
            620, 91, 750, 67, 860, 82, 860, 105, 0, 105,
            fill="#e6fbff", outline="",
        )
        canvas.create_text(
            24, 27, anchor="w", text="MobiGo 2 Homebrew Manager",
            fill="white", font=("Arial", 20, "bold"),
        )
        canvas.create_text(
            26, 52, anchor="w", text="Install, back up, and organize .MBA apps",
            fill="#063f60", font=("Arial", 10),
        )

    def _body(self) -> None:
        toolbar = ttk.Frame(self, padding=(12, 10, 12, 5))
        toolbar.pack(fill="x")
        self.refresh_button = ttk.Button(toolbar, text="Refresh", command=self.refresh)
        self.refresh_button.pack(side="left")
        self.device_label = ttk.Label(toolbar, text="Not connected")
        self.device_label.pack(side="left", padx=12)

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=6)
        self.home_tab = ttk.Frame(self.tabs, padding=10)
        self.advanced_tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(self.home_tab, text="Homebrew")
        self.tabs.add(self.advanced_tab, text="Advanced")
        self._home_tab()
        self._advanced_tab()

        self.status = ttk.Label(self, anchor="w", padding=(12, 8))
        self.status.pack(fill="x")

    def _loading_screen(self) -> None:
        self.loading_overlay = tk.Frame(
            self, bg="#cbd3d9", highlightbackground="#9aa8b2", highlightthickness=1
        )
        card = tk.Frame(
            self.loading_overlay, bg="#eef2f4", padx=42, pady=32,
            highlightbackground="#aab6bf", highlightthickness=1,
        )
        card.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(
            card, text="MobiGo 2 Homebrew Manager", bg="#eef2f4",
            fg="#51606b", font=("Arial", 16, "bold"),
        ).pack(pady=(0, 14))
        self.loading_label = tk.Label(
            card, text="Working…", bg="#eef2f4", fg="#263944",
            font=("Arial", 11), wraplength=440, justify="center",
        )
        self.loading_label.pack(pady=(0, 18))
        self.loading_progress = ttk.Progressbar(card, mode="indeterminate", length=330)
        self.loading_progress.pack()
        tk.Label(
            card, text="Do not unplug or power off the MobiGo during a transfer.",
            bg="#eef2f4", fg="#65747e", font=("Arial", 9),
        ).pack(pady=(16, 0))
        self.loading_overlay.bind("<Button>", lambda _event: "break")
        self.loading_overlay.place_forget()

    @staticmethod
    def _children(widget):
        for child in widget.winfo_children():
            yield child
            yield from HomebrewManager._children(child)

    def _show_loading(self, label: str) -> None:
        self.loading_label.configure(text=label)
        self._busy_widget_states = []
        interactive = (
            ttk.Button, ttk.Checkbutton, ttk.Radiobutton, ttk.Combobox,
            ttk.Entry, ttk.Notebook, ttk.Treeview,
        )
        for widget in self._children(self):
            if isinstance(widget, interactive):
                state = widget.state()
                was_disabled = "disabled" in state
                self._busy_widget_states.append((widget, was_disabled))
                widget.state(["disabled"])
        self.configure(cursor="watch")
        self.loading_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.loading_overlay.lift()
        self.loading_progress.start(12)
        self.loading_overlay.focus_set()
        try:
            self.loading_overlay.grab_set()
        except tk.TclError:
            pass

    def _hide_loading(self) -> None:
        self.loading_progress.stop()
        try:
            if self.grab_current() == self.loading_overlay:
                self.loading_overlay.grab_release()
        except tk.TclError:
            pass
        self.loading_overlay.place_forget()
        self.configure(cursor="")
        for widget, was_disabled in self._busy_widget_states:
            try:
                if not was_disabled and widget.winfo_exists():
                    widget.state(["!disabled"])
            except tk.TclError:
                pass
        self._busy_widget_states = []

    def _home_tab(self) -> None:
        self.apps = ttk.Treeview(
            self.home_tab,
            columns=("name", "title", "size"),
            show="headings",
            selectmode="browse",
        )
        self.apps.heading("name", text="File in /HB")
        self.apps.heading("title", text="Launcher name")
        self.apps.heading("size", text="Size")
        self.apps.column("name", width=180)
        self.apps.column("title", width=390)
        self.apps.column("size", width=100, anchor="e")
        self.apps.pack(fill="both", expand=True)
        buttons = ttk.Frame(self.home_tab, padding=(0, 10, 0, 0))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Add .MBA…", command=self.choose_add).pack(side="left")
        ttk.Button(buttons, text="Delete", command=self.delete_selected).pack(side="left", padx=7)
        ttk.Button(
            buttons, text="Delete all homebrew and exit",
            command=self.delete_all_and_exit,
        ).pack(side="left", padx=7)
        ttk.Label(
            buttons,
            text="Drop .MBA files here" if DND_FILES else "Drag/drop available in packaged builds",
        ).pack(side="right")
        if DND_FILES:
            self.apps.drop_target_register(DND_FILES)
            self.apps.dnd_bind("<<Drop>>", self._drop)

    def _advanced_tab(self) -> None:
        controls = ttk.Frame(self.advanced_tab)
        controls.pack(fill="x", pady=(0, 8))
        self.dmode = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            controls,
            text="Developer mode (/ETC/DMODE)",
            variable=self.dmode,
            command=self.toggle_dmode,
        ).pack(side="left")
        ttk.Button(controls, text="Upload…", command=self.advanced_upload).pack(side="right")
        ttk.Button(controls, text="Download…", command=self.advanced_download).pack(side="right", padx=6)
        ttk.Button(controls, text="Rename…", command=self.advanced_rename).pack(side="right")
        ttk.Button(controls, text="Delete…", command=self.advanced_delete).pack(side="right", padx=6)

        self.tree = ttk.Treeview(
            self.advanced_tab, columns=("path", "size"), show="headings", selectmode="browse"
        )
        self.tree.heading("path", text="Full device path")
        self.tree.heading("size", text="Size")
        self.tree.column("path", width=600)
        self.tree.column("size", width=120, anchor="e")
        self.tree.pack(fill="both", expand=True)
        ttk.Label(
            self.advanced_tab,
            text=(
                "Advanced changes can make the console unbootable. SY is directly editable; "
                f"/HB/{SYSTEM_BACKUP_NAME} is deleted only by the full uninstall."
            ),
        ).pack(fill="x", pady=(8, 0))

    def _status(self, text: str) -> None:
        self.status.configure(text=text)

    def _job(self, label: str, worker, complete=None) -> None:
        if self.busy:
            return
        self.busy = True
        self._status(label)
        self._show_loading(label)

        def thread() -> None:
            try:
                result = worker()
            except Exception as error:
                # Exception targets are cleared at the end of an except block;
                # bind it now before Tk executes the callback on the UI thread.
                self.after(0, lambda error=error: self._finish_error(error))
            else:
                self.after(0, lambda: self._finish(result, complete))

        threading.Thread(target=thread, daemon=True).start()

    def _finish_error(self, error: Exception) -> None:
        self.busy = False
        self._hide_loading()
        self.device_label.configure(text="Not connected")
        self._status(str(error))
        print(f"MobiGo Manager: {error}", file=sys.stderr, flush=True)
        if os.environ.get("MOBIGO_MANAGER_EXIT_AFTER_REFRESH") == "1":
            self.after(0, self.destroy)
            return
        if not self.first_refresh:
            messagebox.showerror("MobiGo 2 Homebrew Manager", str(error), parent=self)

    def _finish(self, result, complete) -> None:
        self.busy = False
        self._hide_loading()
        if complete:
            complete(result)

    @staticmethod
    def _walk(fs, path="/") -> list[tuple[str, RemoteEntry]]:
        output: list[tuple[str, RemoteEntry]] = []
        for item in fs.listdir(path):
            child = (path.rstrip("/") + "/" + item.name) or "/"
            output.append((child, item))
            if item.is_directory:
                output.extend(HomebrewManager._walk(fs, child))
        return output

    def refresh(self) -> None:
        def worker():
            launcher = launcher_bytes()
            with DeviceSession() as fs:
                apps = list_homebrew(fs)
                catalog = list_catalog(fs)
                tree = self._walk(fs)
                dmode = fs.stat_size(DMODE_PATH) is not None
                system_path = discover_system_path(fs)
                system = fs.read_file(system_path)
            return apps, catalog, tree, dmode, system == launcher

        def complete(result) -> None:
            apps, catalog, tree, dmode, installed = result
            details = {
                PurePosixPath(item.path.replace("\\", "/")).name.casefold(): item
                for item in catalog
            }
            self.apps.delete(*self.apps.get_children())
            for item in apps:
                detail = details.get(item.name.casefold())
                self.apps.insert(
                    "",
                    "end",
                    values=(
                        item.name,
                        detail.title if detail else PurePosixPath(item.name).stem,
                        self._size(item.size),
                    ),
                )
            self.tree.delete(*self.tree.get_children())
            for path, item in tree:
                self.tree.insert(
                    "", "end", values=(path + ("/" if item.is_directory else ""),
                                         "folder" if item.is_directory else self._size(item.size))
                )
            self.dmode.set(dmode)
            self.device_label.configure(text="MobiGo 2 connected")
            self._status(f"Ready — {len(apps)} homebrew .MBA file(s)")
            print(
                f"MobiGo Manager: connected, {len(apps)} homebrew app(s), "
                f"launcher={'installed' if installed else 'not installed'}",
                flush=True,
            )
            prompt = self.first_refresh and not installed
            self.first_refresh = False
            if os.environ.get("MOBIGO_MANAGER_EXIT_AFTER_REFRESH") == "1":
                self.after(0, self.destroy)
                return
            if prompt and messagebox.askyesno(
                "Install Homebrew Launcher?",
                "HomebrewLauncher.MBA is not installed or has an update.\n\n"
                "Install it now? The Manager will preserve and verify the original "
                f"system menu at /HB/{SYSTEM_BACKUP_NAME} before replacing SY.",
                parent=self,
            ):
                self.install()

        self._job("Connecting and reading device…", worker, complete)

    @staticmethod
    def _size(value: int) -> str:
        if value < 1024:
            return f"{value} B"
        if value < 1024 * 1024:
            return f"{value / 1024:.1f} KiB"
        return f"{value / (1024 * 1024):.1f} MiB"

    def install(self) -> None:
        backups = invoking_user_home() / "Documents" / "MobiGo 2 Backups"
        def worker():
            with DeviceSession() as fs:
                return install_or_update_launcher(fs, launcher_bytes(), backups)
        def complete(result) -> None:
            messagebox.showinfo(
                "Launcher ready",
                f"HomebrewLauncher.MBA is installed and verified.\n\nLocal recovery backup:\n{result.local_backup}",
                parent=self,
            )
            self.refresh()
        self._job("Preserving recovery SY and installing HomebrewLauncher.MBA…", worker, complete)

    def choose_add(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self, title="Add homebrew", filetypes=[("MobiGo apps", "*.MBA"), ("All files", "*")]
        )
        if paths:
            self.add_paths([Path(item) for item in paths])

    def _drop(self, event) -> None:
        self.add_paths([Path(item) for item in self.tk.splitlist(event.data)])

    def add_paths(self, paths: list[Path]) -> None:
        invalid = [path for path in paths if path.suffix.lower() != ".mba" or not path.is_file()]
        if invalid:
            messagebox.showerror("Invalid app", f"Every dropped file must be an .MBA:\n{invalid[0]}")
            return
        def worker():
            with DeviceSession() as fs:
                for path in paths:
                    hbi = path.with_suffix(".HBI")
                    if not hbi.is_file():
                        hbi = path.with_suffix(".hbi")
                    metadata = load_hbi(hbi, fallback_title=path.stem)
                    add_homebrew(
                        fs,
                        path.name,
                        path.read_bytes(),
                        overwrite=False,
                        metadata=metadata,
                    )
            return len(paths)
        def complete(count) -> None:
            self._status(f"Added {count} .MBA file(s)")
            self.refresh()
        self._job("Uploading and verifying .MBA file(s)…", worker, complete)

    def _selected_name(self) -> str | None:
        selected = self.apps.selection()
        return str(self.apps.item(selected[0], "values")[0]) if selected else None

    def delete_selected(self) -> None:
        name = self._selected_name()
        if not name:
            return
        if name.casefold() == SYSTEM_BACKUP_NAME.casefold():
            messagebox.showinfo(
                "System menu recovery copy",
                f"{SYSTEM_BACKUP_NAME} is removed only by 'Delete all homebrew and exit', "
                "which restores it to SY first.",
                parent=self,
            )
            return
        if not messagebox.askyesno("Delete homebrew?", f"Delete /HB/{name}?", parent=self):
            return
        def worker():
            with DeviceSession() as fs:
                delete_homebrew(fs, name)
        self._job(f"Deleting {name}…", worker, lambda _: self.refresh())

    def delete_all_and_exit(self) -> None:
        if not messagebox.askyesno(
            "Restore system menu and remove homebrew?",
            f"This will restore /HB/{SYSTEM_BACKUP_NAME} to the active SY slot, "
            "verify it, delete the entire /HB folder, and close the Manager.\n\n"
            "Continue?",
            parent=self,
        ):
            return
        backups = invoking_user_home() / "Documents" / "MobiGo 2 Backups"
        def worker():
            with DeviceSession() as fs:
                return uninstall_homebrew(fs, backups)
        def complete(result) -> None:
            messagebox.showinfo(
                "Homebrew removed",
                "The original system menu was restored and verified, and /HB was "
                f"removed.\n\nRecovery backup:\n{result.local_backup}",
                parent=self,
            )
            self.destroy()
        self._job("Restoring the original system menu and removing /HB…", worker, complete)

    def toggle_dmode(self) -> None:
        enabled = self.dmode.get()
        def worker():
            with DeviceSession() as fs:
                return set_developer_mode(fs, enabled)
        def complete(reboot_required) -> None:
            if reboot_required:
                self._status("D-mode marker written — unplug USB and reboot the MobiGo 2")
                messagebox.showinfo(
                    "Reboot required",
                    "D-mode was written. Unplug USB and reboot the MobiGo 2 before "
                    "using the Manager again.",
                    parent=self,
                )
            else:
                self.refresh()
        self._job(f"{'Enabling' if enabled else 'Disabling'} developer mode…", worker,
                  complete)

    def _selected_path(self) -> str | None:
        selected = self.tree.selection()
        if not selected:
            return None
        return str(self.tree.item(selected[0], "values")[0]).rstrip("/")

    def advanced_download(self) -> None:
        source = self._selected_path()
        if not source:
            return
        destination = filedialog.asksaveasfilename(parent=self, initialfile=PurePosixPath(source).name)
        if not destination:
            return
        def worker():
            with DeviceSession() as fs:
                data = fs.read_file(source)
            Path(destination).write_bytes(data)
            if Path(destination).read_bytes() != data:
                raise ManagerError("download verification failed")
        self._job(f"Downloading {source}…", worker, lambda _: self._status(f"Saved {destination}"))

    def advanced_upload(self) -> None:
        local = filedialog.askopenfilename(parent=self)
        if not local:
            return
        destination = simpledialog.askstring(
            "Remote path", "Absolute device destination:",
            initialvalue="/HB/" + Path(local).name, parent=self
        )
        if not destination:
            return
        data = Path(local).read_bytes()
        def worker():
            with DeviceSession() as fs:
                fs.write_file(destination, data)
                if fs.read_file(destination) != data:
                    raise ManagerError("advanced upload did not verify")
        self._job(f"Uploading {destination}…", worker, lambda _: self.refresh())

    def advanced_rename(self) -> None:
        source = self._selected_path()
        if not source:
            return
        destination = simpledialog.askstring(
            "Rename", "New absolute device path:", initialvalue=source, parent=self
        )
        if not destination or destination == source:
            return
        def worker():
            with DeviceSession() as fs:
                rename_file(fs, source, destination)
        self._job(f"Renaming {source}…", worker, lambda _: self.refresh())

    def advanced_delete(self) -> None:
        path = self._selected_path()
        if not path:
            return
        if path.upper() == ("/HB/" + SYSTEM_BACKUP_NAME).upper():
            messagebox.showinfo(
                "System menu recovery copy",
                f"Use 'Delete all homebrew and exit' to remove {SYSTEM_BACKUP_NAME} safely.",
                parent=self,
            )
            return
        if not messagebox.askyesno("Advanced delete", f"Permanently delete {path}?", parent=self):
            return
        def worker():
            with DeviceSession() as fs:
                fs.delete(path)
                if fs.stat_size(path) is not None:
                    raise ManagerError("delete did not verify")
        self._job(f"Deleting {path}…", worker, lambda _: self.refresh())


def run() -> None:
    HomebrewManager().mainloop()
