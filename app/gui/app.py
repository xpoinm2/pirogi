from __future__ import annotations

import ctypes
import os
import re
import traceback
from datetime import UTC, datetime
from concurrent.futures import Future
from pathlib import Path
from typing import Callable
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from tkinter import TclError
from urllib.parse import urlparse

from app.db import Database
from app.exceptions import AppError
from app.gui.async_worker import AsyncWorker
from app.gui.backend import AuthResult, TelegramManagerBackend
from app.logging_setup import setup_logging
from app.models import DialogInfo, ImportMessageItem, ScheduleBatchResult, ScheduledMessageInfo
from app.services.session_manager import SessionManager
from app.settings import PROJECT_ROOT, Settings
from app.utils import format_dt, truncate_text

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # pragma: no cover - fallback for environments without tkinterdnd2
    DND_FILES = "DND_Files"

    class _FallbackTk(tk.Tk):
        pass

    class TkinterDnD:  # type: ignore[no-redef]
        Tk = _FallbackTk


class ClipboardShortcutManager:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._context_widget: tk.Misc | None = None
        self._context_menu = tk.Menu(self.root, tearoff=False)
        self._context_menu.add_command(label="Копировать", command=self._copy_from_menu)
        self._context_menu.add_command(label="Вырезать", command=self._cut_from_menu)
        self._context_menu.add_command(label="Вставить", command=self._paste_from_menu)
        self._context_menu.add_separator()
        self._context_menu.add_command(label="Выделить всё", command=self._select_all_from_menu)
    def install(self) -> None:
        bindings = (
            ("<Control-c>", self._copy),
            ("<Control-C>", self._copy),
            ("<Control-v>", self._paste),
            ("<Control-V>", self._paste),
            ("<Control-x>", self._cut),
            ("<Control-X>", self._cut),
            ("<Control-a>", self._select_all),
            ("<Control-A>", self._select_all),
            ("<Control-Insert>", self._copy),
            ("<Shift-Insert>", self._paste),
            ("<Shift-Delete>", self._cut),
            ("<Command-c>", self._copy),
            ("<Command-v>", self._paste),
            ("<Command-x>", self._cut),
            ("<Command-a>", self._select_all),
        )
        for sequence, handler in bindings:
            self.root.bind_all(sequence, handler, add="+")

        # Keyboard shortcuts with non-latin layouts (e.g. Russian).
        self.root.bind_all("<Control-KeyPress>", self._handle_ctrl_keypress, add="+")
        self.root.bind_all("<Command-KeyPress>", self._handle_ctrl_keypress, add="+")
        self.root.bind_all("<Button-3>", self._show_context_menu, add="+")
        self.root.bind_all("<Control-Button-1>", self._show_context_menu, add="+")

    def _handle_ctrl_keypress(self, event: tk.Event) -> str | None:
        key = str(getattr(event, "keysym", "")).lower()
        # Support shortcuts on Cyrillic layout: c=с, v=м, x=ч, a=ф.
        if key in {"c", "с"}:
            return self._copy(event)
        if key in {"v", "м"}:
            return self._paste(event)
        if key in {"x", "ч"}:
            return self._cut(event)
        if key in {"a", "ф"}:
            return self._select_all(event)
        return None
        
    def _focused_widget(self, event: tk.Event) -> tk.Misc | None:
        focused = self.root.focus_get()
        if focused is not None:
            return focused
        widget = getattr(event, "widget", None)
        if widget in {None, self.root}:
            return None
        return widget

    def _show_context_menu(self, event: tk.Event) -> str | None:
        widget = getattr(event, "widget", None)
        if widget is None:
            return None
        try:
            widget.focus_set()
        except tk.TclError:
            return None

        self._context_widget = widget
        self._sync_context_menu_state(widget)
        try:
            self._context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._context_menu.grab_release()
        return "break"

    def _sync_context_menu_state(self, widget: tk.Misc) -> None:
        can_copy = self._supports_copy(widget)
        can_paste = self._supports_paste(widget)
        can_cut = self._supports_cut(widget)
        can_select_all = self._supports_select_all(widget)

        self._context_menu.entryconfigure("Копировать", state="normal" if can_copy else "disabled")
        self._context_menu.entryconfigure("Вырезать", state="normal" if can_cut else "disabled")
        self._context_menu.entryconfigure("Вставить", state="normal" if can_paste else "disabled")
        self._context_menu.entryconfigure("Выделить всё", state="normal" if can_select_all else "disabled")

    def _menu_event(self) -> tk.Event:
        event = tk.Event()
        event.widget = self._context_widget
        return event

    def _copy_from_menu(self) -> str | None:
        if self._context_widget is None:
            return None
        return self._copy(self._menu_event())

    def _paste_from_menu(self) -> str | None:
        if self._context_widget is None:
            return None
        return self._paste(self._menu_event())

    def _cut_from_menu(self) -> str | None:
        if self._context_widget is None:
            return None
        return self._cut(self._menu_event())

    def _select_all_from_menu(self) -> str | None:
        if self._context_widget is None:
            return None
        return self._select_all(self._menu_event())

    @staticmethod
    def _is_text_like(widget: tk.Misc) -> bool:
        return isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Spinbox, tk.Text, ScrolledText))

    @staticmethod
    def _is_editable(widget: tk.Misc) -> bool:
        try:
            return str(widget.cget("state")) not in {"disabled", "readonly"}
        except tk.TclError:
            return True

    def _supports_copy(self, widget: tk.Misc) -> bool:
        return isinstance(widget, (ttk.Treeview, ttk.Label, tk.Label)) or self._is_text_like(widget)

    def _supports_paste(self, widget: tk.Misc) -> bool:
        return self._is_text_like(widget) and self._is_editable(widget)

    def _supports_cut(self, widget: tk.Misc) -> bool:
        return self._is_text_like(widget) and self._is_editable(widget)

    def _supports_select_all(self, widget: tk.Misc) -> bool:
        return isinstance(widget, ttk.Treeview) or self._is_text_like(widget)


    def _copy(self, event: tk.Event) -> str | None:
        widget = self._focused_widget(event)
        if widget is None:
            return None

        if isinstance(widget, ttk.Treeview):
            selected_rows = [
                "\t".join(str(value) for value in widget.item(item_id, "values"))
                for item_id in widget.selection()
            ]
            if not selected_rows:
                return "break"
            payload = "\n".join(selected_rows)
            self.root.clipboard_clear()
            self.root.clipboard_append(payload)
            return "break"

        if isinstance(widget, (ttk.Label, tk.Label)):
            text = str(widget.cget("text") or "").strip()
            if text:
                self.root.clipboard_clear()
                self.root.clipboard_append(text)
                return "break"
            return None

        try:
            widget.event_generate("<<Copy>>")
            return "break"
        except tk.TclError:
            return None

    def _paste(self, event: tk.Event) -> str | None:
        widget = self._focused_widget(event)
        if widget is None:
            return None
        if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Spinbox)):
            try:
                clipboard_text = self.root.clipboard_get()
            except TclError:
                return "break"
            widget.insert("insert", clipboard_text)
            return "break"

        if isinstance(widget, (tk.Text, ScrolledText)):
            try:
                clipboard_text = self.root.clipboard_get()
            except TclError:
                return "break"
            widget.insert("insert", clipboard_text)
            return "break"
        try:
            widget.event_generate("<<Paste>>")
            return "break"
        except tk.TclError:
            return None

    def _cut(self, event: tk.Event) -> str | None:
        widget = self._focused_widget(event)
        if widget is None:
            return None
        try:
            widget.event_generate("<<Cut>>")
            return "break"
        except tk.TclError:
            return None

    def _select_all(self, event: tk.Event) -> str | None:
        widget = self._focused_widget(event)
        if widget is None:
            return None

        if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Spinbox)):
            widget.selection_range(0, "end")
            widget.icursor("end")
            return "break"

        if isinstance(widget, (tk.Text, ScrolledText)):
            widget.tag_add("sel", "1.0", "end-1c")
            widget.mark_set("insert", "end-1c")
            widget.see("insert")
            return "break"

        if isinstance(widget, ttk.Treeview):
            children = widget.get_children()
            if children:
                widget.selection_set(children)
                widget.focus(children[0])
            return "break"

        return None

class TelegramManagerGui:
    def __init__(self, root: tk.Tk, *, settings: Settings) -> None:
        self.root = root
        self.settings = settings
        self.db = Database(settings.database_path)
        self.db.init()
        self.logger = setup_logging(settings.log_dir, settings.log_level)
        self.worker = AsyncWorker()
        self.backend = TelegramManagerBackend(settings=settings, db=self.db, logger=self.logger)

        self.dialogs: list[DialogInfo] = []
        self.preview_items: list[ImportMessageItem] = []
        self.scheduled_items: list[ScheduledMessageInfo] = []

        self.status_var = tk.StringVar(value="Готово")
        self.phone_var = tk.StringVar(value=settings.default_phone or "")
        self.code_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.file_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.dry_run_var = tk.BooleanVar(value=False)
        self.auth_info_var = tk.StringVar(value="Session ещё не проверялась")
        self.selected_chat_var = tk.StringVar(value="Чат не выбран")
        self.cancel_ids_var = tk.StringVar()

        self.root.title("Telegram Manager Desktop")
        self.root.geometry("1320x860")
        self.root.minsize(1100, 720)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        ClipboardShortcutManager(self.root).install()
        self._build_ui()
        self._register_drop_targets()
        self._append_log(f"Session path: {self.settings.session_path}")
        self._append_log(f"Database path: {self.settings.database_path}")
        self._append_log(f"Log directory: {self.settings.log_dir}")

        ClipboardShortcutManager(self.root).install()
        self._build_ui()
        self._register_drop_targets()
        self._append_log(f"Session path: {self.settings.session_path}")
        self._append_log(f"Database path: {self.settings.database_path}")
        self._append_log(f"Log directory: {self.settings.log_dir}")

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header = ttk.Frame(self.root, padding=(14, 12))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="Telegram Manager Desktop", font=("Segoe UI", 16, "bold")).grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Label(
            header,
            text=(
                f"Session: {self.settings.session_path}    "
                f"SQLite: {self.settings.database_path}    "
                f"Logs: {self.settings.log_dir}"
            ),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(header, textvariable=self.status_var, foreground="#0b5ed7").grid(row=0, column=1, sticky="e")

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        self.auth_tab = ttk.Frame(notebook, padding=12)
        self.schedule_tab = ttk.Frame(notebook, padding=12)
        self.scheduled_tab = ttk.Frame(notebook, padding=12)
        self.local_tab = ttk.Frame(notebook, padding=12)

        notebook.add(self.auth_tab, text="Авторизация")
        notebook.add(self.schedule_tab, text="Импорт и постановка")
        notebook.add(self.scheduled_tab, text="Scheduled messages")
        notebook.add(self.local_tab, text="Локальная SQLite")

        self._build_auth_tab()
        self._build_schedule_tab()
        self._build_scheduled_tab()
        self._build_local_tab()
        self._build_log_panel()

    def _build_auth_tab(self) -> None:
        frame = self.auth_tab
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="API_ID / API_HASH берутся из config/.env", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(frame, text="Phone").grid(row=1, column=0, sticky="w", pady=(12, 4))
        ttk.Entry(frame, textvariable=self.phone_var, width=32).grid(row=1, column=1, sticky="w", pady=(12, 4))
        ttk.Button(
            frame,
            text="Запросить код",
            command=self._button_command("Запросить код", self.request_code),
        ).grid(row=1, column=2, sticky="w", padx=(8, 0))

        ttk.Label(frame, text="Code").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.code_var, width=24).grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(frame, text="2FA password").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.password_var, width=24, show="*").grid(row=3, column=1, sticky="w", pady=4)

        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Button(actions, text="Войти", command=self._button_command("Войти", self.sign_in)).pack(side="left")
        ttk.Button(
            actions,
            text="Проверить session",
            command=self._button_command("Проверить session", self.check_session),
        ).pack(side="left", padx=(8, 0))

        info = ttk.LabelFrame(frame, text="Статус", padding=12)
        info.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        info.columnconfigure(0, weight=1)
        ttk.Label(info, textvariable=self.auth_info_var, wraplength=900).grid(row=0, column=0, sticky="w")

        note = ttk.LabelFrame(frame, text="Подсказка", padding=12)
        note.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        ttk.Label(
            note,
            text=(
                "1. Нажми 'Запросить код'.\n"
                "2. Введи code из Telegram / SMS.\n"
                "3. Если включён облачный пароль 2FA, введи его и нажми 'Войти'."
            ),
            justify="left",
        ).grid(row=0, column=0, sticky="w")

    def _build_schedule_tab(self) -> None:
        frame = self.schedule_tab
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(2, weight=1)

        dialogs_box = ttk.LabelFrame(frame, text="Диалоги", padding=10)
        dialogs_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))
        dialogs_box.columnconfigure(1, weight=1)
        dialogs_box.rowconfigure(1, weight=1)

        ttk.Button(
            dialogs_box,
            text="Загрузить диалоги",
            command=self._button_command("Загрузить диалоги", self.load_dialogs),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(dialogs_box, text="Фильтр").grid(row=0, column=1, sticky="e", padx=(12, 6))
        search_entry = ttk.Entry(dialogs_box, textvariable=self.search_var)
        search_entry.grid(row=0, column=2, sticky="ew")
        search_entry.bind("<KeyRelease>", lambda _event: self._render_dialogs())

        self.dialog_tree = ttk.Treeview(
            dialogs_box,
            columns=("id", "title", "type", "username"),
            show="headings",
            height=15,
            selectmode="browse",
        )
        for column, text, width in (
            ("id", "dialog_id", 150),
            ("title", "title", 320),
            ("type", "type", 170),
            ("username", "username", 160),
        ):
            self.dialog_tree.heading(column, text=text)
            self.dialog_tree.column(column, width=width, anchor="w")
        self.dialog_tree.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
        self.dialog_tree.bind("<<TreeviewSelect>>", lambda _event: self._sync_selected_chat_label())

        dialogs_scroll = ttk.Scrollbar(dialogs_box, orient="vertical", command=self.dialog_tree.yview)
        dialogs_scroll.grid(row=1, column=3, sticky="ns", pady=(10, 0))
        self.dialog_tree.configure(yscrollcommand=dialogs_scroll.set)

        import_box = ttk.LabelFrame(frame, text="Импорт CSV / JSON", padding=10)
        import_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 8))
        import_box.columnconfigure(0, weight=1)

        file_row = ttk.Frame(import_box)
        file_row.grid(row=0, column=0, sticky="ew")
        file_row.columnconfigure(0, weight=1)
        ttk.Entry(file_row, textvariable=self.file_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(
            file_row,
            text="Browse...",
            command=self._button_command("Browse...", self.browse_import_file),
        ).grid(row=0, column=1, padx=(8, 0))

        self.drop_label = ttk.Label(
            import_box,
            text="Перетащи CSV / JSON сюда",
            anchor="center",
            relief="groove",
            padding=18,
        )
        self.drop_label.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        options_row = ttk.Frame(import_box)
        options_row.grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Checkbutton(options_row, text="Dry run", variable=self.dry_run_var).pack(side="left")
        ttk.Button(
            options_row,
            text="Предпросмотр",
            command=self._button_command("Предпросмотр", self.preview_import_file),
        ).pack(side="left", padx=(10, 0))
        ttk.Button(
            options_row,
            text="Поставить в scheduled queue",
            command=self._button_command("Поставить в scheduled queue", self.schedule_file),
        ).pack(side="left", padx=(10, 0))

        ttk.Label(import_box, textvariable=self.selected_chat_var, foreground="#555555").grid(
            row=3, column=0, sticky="w", pady=(10, 0)
        )

        preview_box = ttk.LabelFrame(frame, text="Предпросмотр импортируемых строк", padding=10)
        preview_box.grid(row=2, column=0, columnspan=2, sticky="nsew")
        preview_box.columnconfigure(0, weight=1)
        preview_box.rowconfigure(0, weight=1)

        self.preview_tree = ttk.Treeview(
            preview_box,
            columns=("row", "send_at", "attachment", "disable_preview", "text"),
            show="headings",
            height=14,
        )
        for column, text, width in (
            ("row", "row", 60),
            ("send_at", "send_at", 210),
            ("attachment", "attachment", 250),
            ("disable_preview", "disable_preview", 120),
            ("text", "text", 560),
        ):
            self.preview_tree.heading(column, text=text)
            self.preview_tree.column(column, width=width, anchor="w")
        self.preview_tree.grid(row=0, column=0, sticky="nsew")

        preview_scroll = ttk.Scrollbar(preview_box, orient="vertical", command=self.preview_tree.yview)
        preview_scroll.grid(row=0, column=1, sticky="ns")
        self.preview_tree.configure(yscrollcommand=preview_scroll.set)

    def _build_scheduled_tab(self) -> None:
        frame = self.scheduled_tab
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, textvariable=self.selected_chat_var).pack(side="left")
        ttk.Button(top, text="Обновить", command=self._button_command("Обновить", self.refresh_scheduled)).pack(
            side="left", padx=(12, 0)
        )
        ttk.Button(
            top,
            text="Отменить выбранные",
            command=self._button_command("Отменить выбранные", self.cancel_selected_scheduled),
        ).pack(side="left", padx=(8, 0))
        ttk.Entry(top, textvariable=self.cancel_ids_var, width=30).pack(side="left", padx=(16, 0))
        ttk.Button(
            top,
            text="Отменить IDs",
            command=self._button_command("Отменить IDs", self.cancel_manual_ids),
        ).pack(side="left", padx=(8, 0))

        box = ttk.LabelFrame(frame, text="Scheduled messages в Telegram", padding=10)
        box.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        self.scheduled_tree = ttk.Treeview(
            box,
            columns=("id", "when", "media", "text"),
            show="headings",
            height=18,
            selectmode="extended",
        )
        for column, text, width in (
            ("id", "message_id", 110),
            ("when", "scheduled_at", 220),
            ("media", "media", 80),
            ("text", "text", 760),
        ):
            self.scheduled_tree.heading(column, text=text)
            self.scheduled_tree.column(column, width=width, anchor="w")
        self.scheduled_tree.grid(row=0, column=0, sticky="nsew")

        scheduled_scroll = ttk.Scrollbar(box, orient="vertical", command=self.scheduled_tree.yview)
        scheduled_scroll.grid(row=0, column=1, sticky="ns")
        self.scheduled_tree.configure(yscrollcommand=scheduled_scroll.set)

    def _build_local_tab(self) -> None:
        frame = self.local_tab
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Button(
            top,
            text="Показать записи SQLite",
            command=self._button_command("Показать записи SQLite", self.refresh_local_records),
        ).pack(side="left")

        box = ttk.LabelFrame(frame, text="Локальные записи scheduled_messages", padding=10)
        box.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        self.local_tree = ttk.Treeview(
            box,
            columns=("db_id", "external_id", "chat_id", "status", "send_at", "row", "error"),
            show="headings",
            height=18,
        )
        for column, text, width in (
            ("db_id", "db_id", 80),
            ("external_id", "external_id", 110),
            ("chat_id", "chat_id", 120),
            ("status", "status", 110),
            ("send_at", "send_at", 220),
            ("row", "source_row", 90),
            ("error", "error_message", 520),
        ):
            self.local_tree.heading(column, text=text)
            self.local_tree.column(column, width=width, anchor="w")
        self.local_tree.grid(row=0, column=0, sticky="nsew")

        local_scroll = ttk.Scrollbar(box, orient="vertical", command=self.local_tree.yview)
        local_scroll.grid(row=0, column=1, sticky="ns")
        self.local_tree.configure(yscrollcommand=local_scroll.set)

    def _build_log_panel(self) -> None:
        panel = ttk.LabelFrame(self.root, text="Журнал приложения", padding=10)
        panel.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        panel.columnconfigure(0, weight=1)
        self.log_text = ScrolledText(panel, height=9, wrap="word")
        self.log_text.grid(row=0, column=0, sticky="ew")
        self.log_text.configure(state="disabled")

    def _register_drop_targets(self) -> None:
        for widget in (self.drop_label,):
            if hasattr(widget, "drop_target_register"):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_import_drop)

    def request_code(self) -> None:
        phone = self.phone_var.get().strip()
        future = self.worker.submit(self.backend.request_code(phone))
        self._watch_future(future, self._on_auth_result, action_name="Запрос кода")

    def sign_in(self) -> None:
        future = self.worker.submit(self.backend.sign_in(self.code_var.get(), self.password_var.get()))
        self._watch_future(future, self._on_auth_result, action_name="Вход")

    def check_session(self) -> None:
        future = self.worker.submit(self.backend.check_session())
        self._watch_future(future, self._on_auth_result, action_name="Проверка session")

    def load_dialogs(self) -> None:
        future = self.worker.submit(self.backend.get_dialogs())
        self._watch_future(future, self._on_dialogs_loaded, action_name="Загрузка диалогов")

    def browse_import_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Выбери CSV или JSON",
            filetypes=[
                ("CSV / JSON", "*.csv *.json"),
                ("CSV", "*.csv"),
                ("JSON", "*.json"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.file_var.set(path)
            self._append_log(f"Выбран файл: {path}")

    def preview_import_file(self) -> None:
        file_path = self._require_file_path()
        if file_path is None:
            return
        future = self.worker.submit(self.backend.preview_import_file(file_path))
        self._watch_future(future, self._on_preview_loaded, action_name="Предпросмотр импорта")

    def schedule_file(self) -> None:
        dialog = self._require_selected_dialog()
        file_path = self._require_file_path()
        if dialog is None or file_path is None:
            return
        future = self.worker.submit(
            self.backend.schedule_import_file(
                chat_id=dialog.id,
                file_path=file_path,
                dry_run=self.dry_run_var.get(),
            )
        )
        self._watch_future(future, self._on_schedule_complete, action_name="Постановка в scheduled queue")

    def refresh_scheduled(self) -> None:
        dialog = self._require_selected_dialog()
        if dialog is None:
            return
        future = self.worker.submit(self.backend.get_scheduled_messages(chat_id=dialog.id))
        self._watch_future(future, self._on_scheduled_loaded, action_name="Загрузка scheduled messages")

    def cancel_selected_scheduled(self) -> None:
        dialog = self._require_selected_dialog()
        if dialog is None:
            return
        message_ids = [int(item_id) for item_id in self.scheduled_tree.selection()]
        if not message_ids:
            raise_message("Выбери хотя бы одно scheduled сообщение.")
            return
        future = self.worker.submit(self.backend.cancel_scheduled_messages(chat_id=dialog.id, message_ids=message_ids))
        self._watch_future(future, lambda _result: self._after_cancel(message_ids), action_name="Отмена selected messages")

    def cancel_manual_ids(self) -> None:
        dialog = self._require_selected_dialog()
        if dialog is None:
            return
        raw = self.cancel_ids_var.get().replace(",", " ").split()
        if not raw:
            raise_message("Введи один или несколько message id через пробел или запятую.")
            return
        try:
            message_ids = [int(chunk) for chunk in raw]
        except ValueError:
            raise_message("Message ID должен быть целым числом.")
            return
        future = self.worker.submit(self.backend.cancel_scheduled_messages(chat_id=dialog.id, message_ids=message_ids))
        self._watch_future(future, lambda _result: self._after_cancel(message_ids), action_name="Отмена messages по ID")

    def refresh_local_records(self) -> None:
        selected_dialog = self._selected_dialog()
        chat_id = selected_dialog.id if selected_dialog is not None else None
        future = self.worker.submit(self.backend.get_local_records(chat_id=chat_id))
        self._watch_future(future, self._on_local_records_loaded, action_name="Загрузка SQLite")

    def _watch_future(self, future: Future[object], on_success, *, action_name: str) -> None:
        self.status_var.set(f"{action_name}...")
        self._append_log(f"{action_name}...")

        def _done_callback(done_future: Future[object]) -> None:
            self.root.after(0, self._handle_future_result, done_future, on_success, action_name)

        future.add_done_callback(_done_callback)

    def _handle_future_result(self, future: Future[object], on_success, action_name: str) -> None:
        try:
            result = future.result()
        except Exception as exc:
            self.status_var.set(f"Ошибка: {action_name}")
            self.logger.exception("GUI action failed: %s", action_name)
            self._append_log(f"Ошибка в операции '{action_name}': {exc}")
            details = "".join(traceback.format_exception(exc))
            self._append_log(details.strip())
            messagebox.showerror("Ошибка", str(exc))
            return

        self.status_var.set(f"Готово: {action_name}")
        on_success(result)

    def _on_auth_result(self, result: AuthResult) -> None:
        self.auth_info_var.set(result.message)
        self._append_log(result.message)
        if result.status == "authorized":
            details = f"user_id={result.user_id} username={result.username or '-'}"
            self._append_log(details)

    def _on_dialogs_loaded(self, dialogs: list[DialogInfo]) -> None:
        self.dialogs = dialogs
        self._render_dialogs()
        self._append_log(f"Загружено диалогов: {len(dialogs)}")

    def _render_dialogs(self) -> None:
        search = self.search_var.get().strip().lower()
        current_selection = self.dialog_tree.selection()
        selected_id = int(current_selection[0]) if current_selection else None

        for item in self.dialog_tree.get_children():
            self.dialog_tree.delete(item)

        visible_count = 0
        for dialog in self.dialogs:
            haystack = f"{dialog.title} {dialog.username or ''} {dialog.id}".lower()
            if search and search not in haystack:
                continue
            self.dialog_tree.insert(
                "",
                "end",
                iid=str(dialog.id),
                values=(dialog.id, dialog.title, dialog.entity_type, dialog.username or ""),
            )
            visible_count += 1

        if selected_id is not None and str(selected_id) in self.dialog_tree.get_children():
            self.dialog_tree.selection_set(str(selected_id))

        self._sync_selected_chat_label()
        self._append_log(f"Отображено диалогов: {visible_count}")

    def _on_preview_loaded(self, items: list[ImportMessageItem]) -> None:
        self.preview_items = items
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)

        for row in items:
            self.preview_tree.insert(
                "",
                "end",
                values=(
                    row.source_row,
                    format_dt(row.send_at, self.settings.timezone),
                    str(row.attachment_path) if row.attachment_path else "",
                    str(row.disable_preview),
                    truncate_text(row.text, 140),
                ),
            )

        self._append_log(f"Файл валиден. Строк: {len(items)}")

    def _on_schedule_complete(self, result: ScheduleBatchResult) -> None:
        summary = (
            f"Итог: total={result.total}, scheduled={result.scheduled}, "
            f"failed={result.failed}, dry_run={result.dry_run}"
        )
        self._append_log(summary)
        for error in result.errors:
            self._append_log(error)

        if not result.dry_run:
            self.refresh_scheduled()
            self.refresh_local_records()

        messagebox.showinfo("Готово", summary)

    def _on_scheduled_loaded(self, items: list[ScheduledMessageInfo]) -> None:
        self.scheduled_items = items
        for item in self.scheduled_tree.get_children():
            self.scheduled_tree.delete(item)

        for row in items:
            self.scheduled_tree.insert(
                "",
                "end",
                iid=str(row.id),
                values=(
                    row.id,
                    format_dt(row.schedule_at, self.settings.timezone),
                    "yes" if row.has_media else "no",
                    truncate_text(row.text, 180),
                ),
            )

        self._append_log(f"Scheduled messages: {len(items)}")

    def _on_local_records_loaded(self, rows: list[dict[str, object]]) -> None:
        for item in self.local_tree.get_children():
            self.local_tree.delete(item)

        for row in rows:
            self.local_tree.insert(
                "",
                "end",
                values=(
                    row.get("id"),
                    row.get("external_message_id"),
                    row.get("chat_id"),
                    row.get("status"),
                    row.get("send_at"),
                    row.get("source_row"),
                    truncate_text(str(row.get("error_message") or ""), 140),
                ),
            )

        self._append_log(f"SQLite записей: {len(rows)}")

    def _after_cancel(self, message_ids: list[int]) -> None:
        self._append_log(f"Отменены scheduled messages: {message_ids}")
        self.cancel_ids_var.set("")
        self.refresh_scheduled()
        self.refresh_local_records()

    def _sync_selected_chat_label(self) -> None:
        dialog = self._selected_dialog()
        if dialog is None:
            self.selected_chat_var.set("Чат не выбран")
            return
        username = f" @{dialog.username}" if dialog.username else ""
        self.selected_chat_var.set(f"Текущий чат: {dialog.title} [{dialog.id}]{username}")

    def _selected_dialog(self) -> DialogInfo | None:
        selected = self.dialog_tree.selection()
        if not selected:
            return None
        dialog_id = int(selected[0])
        for dialog in self.dialogs:
            if dialog.id == dialog_id:
                return dialog
        return None

    def _require_selected_dialog(self) -> DialogInfo | None:
        dialog = self._selected_dialog()
        if dialog is None:
            raise_message("Сначала выбери чат в таблице диалогов.")
            return None
        return dialog

    def _require_file_path(self) -> str | None:
        file_path = self.file_var.get().strip()
        if not file_path:
            raise_message("Сначала выбери CSV / JSON файл.")
            return None
        return file_path

    def _on_import_drop(self, event: tk.Event) -> None:
        paths = self.root.tk.splitlist(event.data)
        if not paths:
            return

        file_path = Path(paths[0])
        if file_path.suffix.lower() not in {".csv", ".json"}:
            raise_message("Нужен CSV или JSON файл.")
            return

        self.file_var.set(str(file_path))
        self._append_log(f"Файл добавлен drag-and-drop: {file_path}")

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def on_close(self) -> None:
        try:
            self.worker.submit(self.backend.disconnect()).result(timeout=10)
        except Exception:
            self.logger.exception("Ошибка при отключении клиента")
        finally:
            self.worker.stop()
            self.root.destroy()


def raise_message(text: str) -> None:
    messagebox.showwarning("Внимание", text)


class MissingConfigWindow:
    def __init__(self, root: tk.Tk, error: Exception) -> None:
        root.title("Telegram Manager Desktop")
        root.geometry("780x420")
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        heading_font = tkfont.Font(root=root, family="Segoe UI", size=14, weight="bold")

        ttk.Label(frame, text="Не удалось загрузить config/.env", font=heading_font).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "Скопируй .env.example в config/.env и заполни API_ID / API_HASH.\n"
                "После этого перезапусти приложение."
            ),
            justify="left",
        ).pack(anchor="w", pady=(10, 0))

        text = ScrolledText(frame, height=14, wrap="word")
        text.pack(fill="both", expand=True, pady=(14, 0))
        text.insert("1.0", str(error))
        text.configure(state="disabled")


class MainMenuWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Telegram Manager Desktop")
        self.root.geometry("1100x720")
        self.root.minsize(900, 600)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        ClipboardShortcutManager(self.root).install()
        self.accounts_summary_var = tk.StringVar(value="Аккаунты ещё не добавлены.")
        self.total_accounts_var = tk.StringVar(value="0")
        self.live_accounts_var = tk.StringVar(value="0")
        self.frozen_accounts_var = tk.StringVar(value="0")
        self.deleted_accounts_var = tk.StringVar(value="0")
        self.account_phone_var = tk.StringVar()
        self.account_code_var = tk.StringVar()
        self.account_password_var = tk.StringVar()
        self.account_name_var = tk.StringVar()
        self.account_auth_status_var = tk.StringVar(
            value="Добавь .session через Browse или авторизуйся по номеру телефона."
        )
        self._account_login_backend: TelegramManagerBackend | None = None
        self._account_login_session_file: str | None = None

        self.session_dir = self._resolve_storage_path("SESSION_DIR", "data/sessions")
        self.database_path = self._resolve_storage_path("DATABASE_PATH", "data/telegram_manager.db")
        self.db = Database(self.database_path)
        self.db.init()
        self.session_manager = SessionManager(session_dir=self.session_dir)
        self.worker = AsyncWorker()
        self.settings: Settings | None = None
        self.backend: TelegramManagerBackend | None = None
        self.logger = setup_logging(self._resolve_storage_path("LOG_DIR", "logs"), "INFO")
        self.relay_source_chat_var = tk.StringVar()
        self.relay_message_ids_var = tk.StringVar()
        self.relay_target_ids_var = tk.StringVar()
        self.relay_delay_min_var = tk.StringVar(value="180")
        self.relay_delay_max_var = tk.StringVar(value="360")
        self.relay_dry_run_var = tk.BooleanVar(value=False)
        self.relay_run_id_var = tk.StringVar()
        self.relay_status_var = tk.StringVar(value="Готово к запуску рассылки.")

        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(root)
        notebook.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        main_menu_tab = ttk.Frame(notebook, padding=16)
        relay_tab = ttk.Frame(notebook, padding=16)
        accounts_tab = ttk.Frame(notebook, padding=16)
        notebook.add(main_menu_tab, text="Главное меню")
        notebook.add(relay_tab, text="Рассылка из чата")
        notebook.add(accounts_tab, text="Аккаунты")

        ttk.Label(main_menu_tab, text="Главное меню", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        self._add_selectable_note(main_menu_tab, "Раздел готов к наполнению функциями.", pady=(8, 0))

        self._build_relay_tab(relay_tab)
        self._build_accounts_tab(accounts_tab)
        self.refresh_accounts()

    def _button_command(self, label: str, command: Callable[[], None]) -> Callable[[], None]:
        def _wrapped() -> None:
            self.logger.info("Button clicked: %s", label)
            command()

        return _wrapped

    def _add_selectable_note(
        self,
        parent: tk.Misc,
        text: str,
        *,
        pady: tuple[int, int] | None = None,
        grid: dict[str, object] | None = None,
    ) -> None:
        note = tk.Text(
            parent,
            wrap="word",
            height=max(1, text.count("\n") + 1),
            relief="flat",
            borderwidth=0,
            background=self.root.cget("background"),
            foreground="#555555",
            font=("Segoe UI", 10),
            cursor="xterm",
        )
        note.insert("1.0", text)
        note.configure(state="disabled")

        if grid is not None:
            note.grid(**grid)
        else:
            note.pack(anchor="w", fill="x", pady=pady or (0, 0))

    def _build_accounts_tab(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        canvas = tk.Canvas(frame, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scroll.set)

        content = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def _sync_scroll_region(_event: tk.Event) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_content_width(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        content.bind("<Configure>", _sync_scroll_region)
        canvas.bind("<Configure>", _sync_content_width)

        content.columnconfigure(0, weight=1)
        content.rowconfigure(6, weight=1)

        ttk.Label(content, text="Аккаунты", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        self._add_selectable_note(
            content,
            "Импортируй Telethon-сессии (.session) через Browse, чтобы добавить аккаунт в программу.\n"
            "Или авторизуй аккаунт по номеру телефона, коду и 2FA ниже.\n"
            "После добавления аккаунты доступны для дальнейших операций.",
            grid={"row": 1, "column": 0, "sticky": "ew", "pady": (8, 0)},
        )

        controls = ttk.Frame(content)
        controls.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(
            controls,
            text="Добавить аккаунт (Browse...)",
            command=self._button_command("Добавить аккаунт (Browse...)", self.add_account_via_browse),
        ).pack(side="left")
        ttk.Button(
            controls,
            text="Обновить список",
            command=self._button_command("Обновить список", self.refresh_accounts),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            controls,
            text="Проверить сессию",
            command=self._button_command("Проверить сессию", self.check_selected_account_session),
        ).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(
            controls,
            text="Удалить аккаунт",
            command=self._button_command("Удалить аккаунт", self.delete_selected_account),
        ).pack(side="left", padx=(8, 0))
        ttk.Label(
            controls,
            text=f"Session storage: {self.session_dir}",
            foreground="#666666",
        ).pack(side="left", padx=(16, 0))

        phone_box = ttk.LabelFrame(content, text="Добавление по номеру и коду", padding=10)
        phone_box.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        phone_box.columnconfigure(1, weight=1)

        ttk.Label(phone_box, text="Имя аккаунта (опционально)").grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(phone_box, textvariable=self.account_name_var, width=36).grid(
            row=0, column=1, sticky="ew", pady=(0, 6), padx=(8, 0)
        )

        ttk.Label(phone_box, text="Phone").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(phone_box, textvariable=self.account_phone_var, width=36).grid(
            row=1, column=1, sticky="ew", pady=4, padx=(8, 0)
        )
        ttk.Button(
            phone_box,
            text="Запросить код",
            command=self._button_command("Запросить код (аккаунт)", self.request_account_code),
        ).grid(
            row=1, column=2, sticky="w", padx=(8, 0)
        )

        ttk.Label(phone_box, text="Code").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(phone_box, textvariable=self.account_code_var, width=24).grid(
            row=2, column=1, sticky="w", pady=4, padx=(8, 0)
        )

        ttk.Label(phone_box, text="2FA password").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(phone_box, textvariable=self.account_password_var, width=24, show="*").grid(
            row=3, column=1, sticky="w", pady=4, padx=(8, 0)
        )
        ttk.Button(
            phone_box,
            text="Добавить аккаунт",
            command=self._button_command("Добавить аккаунт", self.complete_account_sign_in),
        ).grid(
            row=3, column=2, sticky="w", padx=(8, 0)
        )

        ttk.Label(
            phone_box,
            textvariable=self.account_auth_status_var,
            foreground="#444444",
            wraplength=880,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))

        summary = ttk.LabelFrame(content, text="Состояние аккаунтов", padding=10)
        summary.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        for index in range(4):
            summary.columnconfigure(index, weight=1)

        self._summary_cell(summary, "Всего", self.total_accounts_var, 0)
        self._summary_cell(summary, "Живые", self.live_accounts_var, 1)
        self._summary_cell(summary, "Замороженные", self.frozen_accounts_var, 2)
        self._summary_cell(summary, "Удалённые", self.deleted_accounts_var, 3)

        ttk.Label(summary, textvariable=self.accounts_summary_var, foreground="#444444").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(10, 0)
        )

        table_box = ttk.LabelFrame(content, text="Список аккаунтов", padding=10)
        table_box.grid(row=6, column=0, sticky="nsew", pady=(12, 0))
        table_box.columnconfigure(0, weight=1)
        table_box.rowconfigure(0, weight=1)

        self.accounts_tree = ttk.Treeview(
            table_box,
            columns=("id", "account_name", "session_file", "status", "updated_at"),
            show="headings",
            height=18,
        )
        for column, text, width in (
            ("id", "ID", 70),
            ("account_name", "Аккаунт", 260),
            ("session_file", "Session file", 280),
            ("status", "Состояние", 130),
            ("updated_at", "Обновлён", 220),
        ):
            self.accounts_tree.heading(column, text=text)
            self.accounts_tree.column(column, width=width, anchor="w")
        self.accounts_tree.grid(row=0, column=0, sticky="nsew")

        accounts_scroll = ttk.Scrollbar(table_box, orient="vertical", command=self.accounts_tree.yview)
        accounts_scroll.grid(row=0, column=1, sticky="ns")
        self.accounts_tree.configure(yscrollcommand=accounts_scroll.set)

    @staticmethod
    def _make_session_filename(phone: str) -> str:
        digits = "".join(ch for ch in phone if ch.isdigit())
        token = digits[-10:] if digits else "account"
        suffix = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        return f"phone_{token}_{suffix}.session"

    @staticmethod
    def _proxy_status_text(settings: Settings) -> str:
        return f"Сеть: {settings.proxy_summary}."

    def request_account_code(self) -> None:
        phone = self.account_phone_var.get().strip()
        if not phone:
            raise_message("Введи номер телефона в международном формате, например +79990000000.")
            return

        try:
            settings = Settings.load()
        except Exception as exc:
            messagebox.showerror("Ошибка настроек", str(exc))
            return

        if self._account_login_backend is not None:
            self.worker.submit(self._account_login_backend.disconnect())

        session_file = self._make_session_filename(phone)
        session_path = (self.session_dir / session_file).resolve()
        self._account_login_backend = TelegramManagerBackend(
            settings=settings,
            db=self.db,
            logger=self.logger,
            session_path=session_path,
        )
        self._account_login_session_file = session_file
        self.account_auth_status_var.set(
            "Этап 1/3: подключение к Telegram и запрос кода. "
            f"{self._proxy_status_text(settings)}"
        )

        future = self.worker.submit(self._account_login_backend.request_code(phone))
        self._watch_account_future(
            future,
            self._on_account_code_requested,
            "Запрос кода (аккаунты)",
        )

    def complete_account_sign_in(self) -> None:
        if self._account_login_backend is None or not self._account_login_session_file:
            raise_message("Сначала нажми 'Запросить код' в блоке добавления по номеру.")
            return
        self.account_auth_status_var.set("Этап 2/3: проверка кода (и 2FA, если включен).")
        future = self.worker.submit(
            self._account_login_backend.sign_in(self.account_code_var.get(), self.account_password_var.get())
        )
        self._watch_account_future(
            future,
            self._on_account_sign_in_completed,
            "Добавление аккаунта (код + 2FA)",
        )

    def _on_account_code_requested(self, result: AuthResult) -> None:
        if result.status == "authorized":
            self.account_auth_status_var.set(f"Этап 3/3: вход завершён. {result.message}")
        else:
            self.account_auth_status_var.set(f"Этап 1/3 завершён. {result.message}")
        if result.status == "authorized":
            self._finalize_account_add(result)

    def _on_account_sign_in_completed(self, result: AuthResult) -> None:
        if result.status == "authorized":
            self.account_auth_status_var.set(f"Этап 3/3: вход завершён. {result.message}")
        else:
            self.account_auth_status_var.set(f"Этап 2/3: {result.message}")
        if result.status != "authorized":
            return
        self._finalize_account_add(result)

    def _finalize_account_add(self, result: AuthResult) -> None:
        session_file = self._account_login_session_file
        if not session_file:
            raise_message("Не найден session-файл для нового аккаунта.")
            return

        account_name = self.account_name_var.get().strip()
        if not account_name:
            account_name = result.display_name or result.username or session_file.removesuffix(".session")

        self.db.upsert_account(
            session_file=session_file,
            account_name=account_name,
            status="live",
        )
        self.session_manager.set_active_session(session_file)
        self.refresh_accounts()
        self.account_code_var.set("")
        self.account_password_var.set("")
        self.account_name_var.set("")
        if self._account_login_backend is not None:
            self.worker.submit(self._account_login_backend.disconnect())
        self._account_login_backend = None
        self._account_login_session_file = None
        self.account_auth_status_var.set(f"Аккаунт добавлен: {account_name}")
        messagebox.showinfo("Готово", f"Аккаунт добавлен: {account_name}\nSession: {session_file}")

    def _build_relay_tab(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        ttk.Label(frame, text="Рассылка из чата", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        self._add_selectable_note(
            frame,
            (
                "Создай run для пересылки/копирования сообщений из исходного чата в целевые чаты.\n"
                "Режим all_to_all: каждый Message ID отправляется в каждый Target chat.\n"
                "Message IDs можно вставлять числами или ссылками на сообщения."
            ),
            grid={"row": 1, "column": 0, "sticky": "ew", "pady": (8, 10)},
        )

        config_box = ttk.LabelFrame(frame, text="Параметры запуска", padding=10)
        config_box.grid(row=2, column=0, sticky="ew")
        for i in range(4):
            config_box.columnconfigure(i, weight=1 if i in (1, 3) else 0)

        ttk.Label(config_box, text="Source chat ID").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(config_box, textvariable=self.relay_source_chat_var, width=22).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Checkbutton(config_box, text="Dry run", variable=self.relay_dry_run_var).grid(
            row=0, column=2, sticky="w", padx=(12, 0), pady=4
        )

        ttk.Label(config_box, text="Message IDs / links").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(config_box, textvariable=self.relay_message_ids_var).grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)
        ttk.Label(config_box, text="Target chat IDs / links").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(config_box, textvariable=self.relay_target_ids_var).grid(row=2, column=1, columnspan=3, sticky="ew", pady=4)

        ttk.Label(config_box, text="Delay min/max (sec)").grid(row=3, column=0, sticky="w", pady=4)
        delay_row = ttk.Frame(config_box)
        delay_row.grid(row=3, column=1, sticky="w", pady=4)
        ttk.Entry(delay_row, textvariable=self.relay_delay_min_var, width=8).pack(side="left")
        ttk.Label(delay_row, text="/").pack(side="left", padx=4)
        ttk.Entry(delay_row, textvariable=self.relay_delay_max_var, width=8).pack(side="left")

        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, sticky="ew", pady=(12, 8))
        ttk.Button(
            actions,
            text="Старт рассылки",
            command=self._button_command("Старт рассылки", self.start_relay_run),
        ).pack(side="left")
        ttk.Label(actions, text="Run ID").pack(side="left", padx=(16, 6))
        ttk.Entry(actions, textvariable=self.relay_run_id_var, width=10).pack(side="left")
        ttk.Button(
            actions,
            text="Статус",
            command=self._button_command("Статус", self.relay_status),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            actions,
            text="Пауза",
            command=self._button_command("Пауза", self.relay_pause),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            actions,
            text="Возобновить",
            command=self._button_command("Возобновить", self.relay_resume),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            actions,
            text="Обновить список run",
            command=self._button_command("Обновить список run", self.refresh_relay_runs),
        ).pack(side="left", padx=(8, 0))

        ttk.Label(frame, textvariable=self.relay_status_var, foreground="#0b5ed7").grid(row=4, column=0, sticky="w")

        table = ttk.LabelFrame(frame, text="Последние relay run", padding=10)
        table.grid(row=5, column=0, sticky="nsew", pady=(10, 0))
        frame.rowconfigure(5, weight=1)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)

        self.relay_runs_tree = ttk.Treeview(
            table,
            columns=("id", "mode", "source_chat_id", "total", "status", "dry_run", "updated_at"),
            show="headings",
            height=14,
        )
        for column, text, width in (
            ("id", "run_id", 80),
            ("mode", "mode", 120),
            ("source_chat_id", "source_chat_id", 150),
            ("total", "total_tasks", 110),
            ("status", "status", 110),
            ("dry_run", "dry_run", 90),
            ("updated_at", "updated_at", 220),
        ):
            self.relay_runs_tree.heading(column, text=text)
            self.relay_runs_tree.column(column, width=width, anchor="w")
        self.relay_runs_tree.grid(row=0, column=0, sticky="nsew")
        self.relay_runs_tree.bind("<<TreeviewSelect>>", self._on_relay_run_select)

        scroll = ttk.Scrollbar(table, orient="vertical", command=self.relay_runs_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.relay_runs_tree.configure(yscrollcommand=scroll.set)


    @staticmethod
    def _summary_cell(parent: ttk.LabelFrame, title: str, value_var: tk.StringVar, column: int) -> None:
        cell = ttk.Frame(parent, padding=(6, 2))
        cell.grid(row=0, column=column, sticky="ew")
        ttk.Label(cell, text=title, foreground="#666666").pack(anchor="w")
        ttk.Label(cell, textvariable=value_var, font=("Segoe UI", 16, "bold")).pack(anchor="w")

    def add_account_via_browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Выбери telethon.session",
            filetypes=[
                ("Telethon session", "*.session"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        try:
            imported = self.session_manager.import_session(path)
            self.db.upsert_account(
                session_file=imported.destination.name,
                account_name=imported.destination.stem,
                status="live",
            )
            self.refresh_accounts()
        except AppError as exc:
            messagebox.showerror("Ошибка импорта", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive GUI branch
            messagebox.showerror("Ошибка", f"Не удалось добавить аккаунт: {exc}")
            return

        suffix = " (файл переименован)" if imported.renamed else ""
        messagebox.showinfo(
            "Готово",
            f"Аккаунт добавлен: {imported.destination.name}{suffix}",
        )

    def delete_selected_account(self) -> None:
        selected = self.accounts_tree.selection()
        if not selected:
            raise_message("Выбери аккаунт в таблице.")
            return

        values = self.accounts_tree.item(selected[0], "values")
        account_id = int(values[0])
        account_name = str(values[1] or "")
        session_file = str(values[2] or "")

        if not messagebox.askyesno(
            "Удалить аккаунт",
            f"Удалить аккаунт '{account_name or session_file}'?\n"
            "Запись получит статус 'удалён', а session-файл будет удалён с диска.",
        ):
            return

        self.db.update_account_status(account_id, "deleted")
        session_path = (self.session_dir / session_file).resolve()
        if session_file and session_path.exists() and session_path.is_file():
            try:
                session_path.unlink()
            except OSError as exc:
                messagebox.showwarning(
                    "Session не удалён",
                    f"Статус аккаунта обновлён, но session-файл не удалён:\n{exc}",
                )

        active_session = self.session_manager.get_active_session_path()
        if active_session is not None and active_session.name == session_file:
            self.session_manager.clear_active_session()
            self.backend = None
            self.settings = None
        self.refresh_accounts()

    def check_selected_account_session(self) -> None:
        selected = self.accounts_tree.selection()
        if not selected:
            raise_message("Выбери аккаунт в таблице.")
            return

        values = self.accounts_tree.item(selected[0], "values")
        account_id = int(values[0])
        account_name = str(values[1] or "")
        session_file = str(values[2] or "")
        session_path = (self.session_dir / session_file).resolve()

        if not session_file or not session_path.exists():
            self.db.update_account_status(account_id, "deleted")
            self.refresh_accounts()
            messagebox.showerror("Session не найдена", f"Файл сессии не найден:\n{session_path}")
            return

        self.session_manager.set_active_session(session_file)
        self.backend = None
        self.settings = None
        future = self.worker.submit(self._check_account_session(session_path))
        self._watch_account_future(
            future,
            lambda result: self._on_account_session_checked(account_id, account_name, session_file, result),
            "Проверка session",
        )

    async def _check_account_session(self, session_path: Path) -> AuthResult:
        settings = Settings.load()
        backend = TelegramManagerBackend(
            settings=settings,
            db=self.db,
            logger=self.logger,
            session_path=session_path,
        )
        try:
            return await backend.check_session()
        finally:
            await backend.disconnect()

    def _watch_account_future(self, future: Future[object], on_success, action_name: str) -> None:
        self.accounts_summary_var.set(f"{action_name}...")

        def _done_callback(done_future: Future[object]) -> None:
            self.root.after(0, self._handle_account_future_result, done_future, on_success, action_name)

        future.add_done_callback(_done_callback)

    def _handle_account_future_result(self, future: Future[object], on_success, action_name: str) -> None:
        try:
            result = future.result()
        except Exception as exc:
            self.logger.exception("GUI action failed: %s", action_name)
            self.account_auth_status_var.set(f"{action_name}: ошибка — {exc}")
            messagebox.showerror("Ошибка", str(exc))
            self.refresh_accounts()
            return
        on_success(result)

    def _on_account_session_checked(
        self,
        account_id: int,
        account_name: str,
        session_file: str,
        result: AuthResult,
    ) -> None:
        if result.status == "authorized":
            self.db.update_account_status(account_id, "live")
            self.refresh_accounts()
            user_display = result.display_name or result.username or "без имени"
            messagebox.showinfo(
                "Session валидна",
                f"Аккаунт: {account_name or session_file}\n"
                f"Пользователь: {user_display}\n"
                f"ID: {result.user_id}",
            )
            return

        self.db.update_account_status(account_id, "frozen")
        self.refresh_accounts()
        messagebox.showwarning(
            "Session требует вход",
            f"Аккаунт: {account_name or session_file}\n{result.message}",
        )
    def refresh_accounts(self) -> None:
        rows = [dict(row) for row in self.db.list_accounts(include_deleted=True)]
        for item in self.accounts_tree.get_children():
            self.accounts_tree.delete(item)

        counts = {"live": 0, "frozen": 0, "deleted": 0}
        status_labels = {"live": "живой", "frozen": "заморожен", "deleted": "удалён"}
        for row in rows:
            status = str(row.get("status") or "live")
            counts[status] = counts.get(status, 0) + 1
            self.accounts_tree.insert(
                "",
                "end",
                values=(
                    row.get("id"),
                    row.get("account_name"),
                    row.get("session_file"),
                    status_labels.get(status, status),
                    row.get("updated_at"),
                ),
            )

        total = len(rows)
        self.total_accounts_var.set(str(total))
        self.live_accounts_var.set(str(counts.get("live", 0)))
        self.frozen_accounts_var.set(str(counts.get("frozen", 0)))
        self.deleted_accounts_var.set(str(counts.get("deleted", 0)))

        self.accounts_summary_var.set(
            "В базе: "
            f"{total} аккаунтов — живые: {counts.get('live', 0)}, "
            f"замороженные: {counts.get('frozen', 0)}, удалённые: {counts.get('deleted', 0)}."
        )

    def start_relay_run(self) -> None:
        backend = self._require_backend()
        if backend is None:
            return
        source_chat_id = self._parse_chat_id(self.relay_source_chat_var.get(), "Source chat ID")
        if source_chat_id is None:
            return
        parsed = self._collect_relay_params()
        if parsed is None:
            return
        future = self.worker.submit(
            backend.start_relay_run(
                source_chat_id=source_chat_id,
                message_ids=parsed["message_ids"],
                target_chat_ids=parsed["target_chat_ids"],
                delay_min=parsed["delay_min"],
                delay_max=parsed["delay_max"],
                dry_run=self.relay_dry_run_var.get(),
            )
        )
        self._watch_main_future(future, self._on_relay_action_done, "Запуск relay рассылки")

    def relay_status(self) -> None:
        backend = self._require_backend()
        run_id = self._parse_single_int(self.relay_run_id_var.get(), "Run ID")
        if backend is None or run_id is None:
            return
        future = self.worker.submit(backend.relay_status(run_id=run_id))
        self._watch_main_future(future, self._on_relay_action_done, "Получение статуса relay")

    def relay_pause(self) -> None:
        backend = self._require_backend()
        run_id = self._parse_single_int(self.relay_run_id_var.get(), "Run ID")
        if backend is None or run_id is None:
            return
        future = self.worker.submit(backend.relay_pause(run_id=run_id))
        self._watch_main_future(future, self._on_relay_action_done, "Пауза relay")

    def relay_resume(self) -> None:
        backend = self._require_backend()
        run_id = self._parse_single_int(self.relay_run_id_var.get(), "Run ID")
        if backend is None or run_id is None:
            return
        future = self.worker.submit(backend.relay_resume(run_id=run_id))
        self._watch_main_future(future, self._on_relay_action_done, "Возобновление relay")

    def refresh_relay_runs(self) -> None:
        backend = self._require_backend()
        if backend is None:
            return
        future = self.worker.submit(backend.get_relay_runs(limit=200))
        self._watch_main_future(future, self._on_relay_runs_loaded, "Загрузка relay run")

    def _watch_main_future(self, future: Future[object], on_success, action_name: str) -> None:
        self.relay_status_var.set(f"{action_name}...")

        def _done_callback(done_future: Future[object]) -> None:
            self.root.after(0, self._handle_main_future_result, done_future, on_success, action_name)

        future.add_done_callback(_done_callback)

    def _handle_main_future_result(self, future: Future[object], on_success, action_name: str) -> None:
        try:
            result = future.result()
        except Exception as exc:
            self.logger.exception("GUI action failed: %s", action_name)
            self.relay_status_var.set(f"Ошибка: {exc}")
            messagebox.showerror("Ошибка", str(exc))
            return
        on_success(result)

    def _on_relay_action_done(self, summary: dict[str, object]) -> None:
        run_id = int(summary["id"])
        self.relay_run_id_var.set(str(run_id))
        self.relay_status_var.set(
            f"run_id={run_id} status={summary['status']} total={summary['total_tasks']} "
            f"sent={summary['sent_tasks']} failed={summary['failed_tasks']} skipped={summary['skipped_tasks']}"
        )
        self.refresh_relay_runs()

    def _on_relay_runs_loaded(self, rows: list[dict[str, object]]) -> None:
        for item in self.relay_runs_tree.get_children():
            self.relay_runs_tree.delete(item)
        for row in rows:
            self.relay_runs_tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row.get("id"),
                    row.get("mode"),
                    row.get("source_chat_id"),
                    row.get("total_tasks"),
                    row.get("status"),
                    "yes" if row.get("dry_run") else "no",
                    row.get("updated_at"),
                ),
            )
        self.relay_status_var.set(f"Загружено run: {len(rows)}")

    def _on_relay_run_select(self, _event: tk.Event) -> None:
        selected = self.relay_runs_tree.selection()
        if selected:
            self.relay_run_id_var.set(selected[0])

    def _collect_relay_params(self) -> dict[str, object] | None:
        delay_min = self._parse_single_int(self.relay_delay_min_var.get(), "Delay min")
        delay_max = self._parse_single_int(self.relay_delay_max_var.get(), "Delay max")
        if None in {delay_min, delay_max}:
            return None

        if delay_min <= 0 or delay_max <= 0 or delay_min > delay_max:
            raise_message("Delay min/max заданы некорректно.")
            return None
        message_ids = self._parse_message_ids(self.relay_message_ids_var.get(), "Message IDs")
        target_ids = self._parse_chat_id_list(self.relay_target_ids_var.get(), "Target chat IDs")
        if message_ids is None or target_ids is None:
            return None
        return {
            "delay_min": delay_min,
            "delay_max": delay_max,
            "message_ids": message_ids,
            "target_chat_ids": target_ids,
        }

    @staticmethod
    def _parse_single_int(raw: str, label: str) -> int | None:
        value = raw.strip()
        if not value:
            raise_message(f"{label}: обязательное поле.")
            return None
        try:
            return int(value)
        except ValueError:
            raise_message(f"{label} должен быть целым числом.")
            return None

    def _parse_message_ids(self, raw: str, label: str) -> list[int] | None:
        clean = [chunk for chunk in raw.replace(",", " ").split() if chunk]
        if not clean:
            return []
            
        parsed: list[int] = []
        for chunk in clean:
            message_id = self._extract_message_id(chunk)
            if message_id is None:
                raise_message(f"{label}: не удалось распознать ID из '{chunk}'.")
                return None
            parsed.append(message_id)
        return parsed

    def _parse_chat_id_list(self, raw: str, label: str) -> list[int] | None:
        clean = [chunk for chunk in raw.replace(",", " ").split() if chunk]
        if not clean:
            return []

        parsed: list[int] = []
        for chunk in clean:
            chat_id = self._parse_chat_id(chunk, label)
            if chat_id is None:
                return None
            parsed.append(chat_id)
        return parsed

    def _parse_chat_id(self, raw: str, label: str) -> int | None:
        value = raw.strip()
        if not value:
            raise_message(f"{label}: обязательное поле.")
            return None
        try:
                        return self._extract_chat_id(value)
        except ValueError as exc:
            raise_message(f"{label}: {exc}")
            return None

    @staticmethod
    def _extract_message_id(value: str) -> int | None:
        raw = value.strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
                        pass

        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"}:
            return None
        parts = [segment for segment in parsed.path.split("/") if segment]
        if not parts:
            return None
        candidate = parts[-1]
        if candidate.isdigit():
            return int(candidate)
        return None

    @staticmethod
    def _extract_chat_id(value: str) -> int:
        raw = value.strip()
        try:
            return int(raw)
        except ValueError:
            pass

        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("введи числовой chat id или ссылку вида https://t.me/c/<id>/<message_id>.")
        if parsed.netloc not in {"t.me", "telegram.me", "www.t.me"}:
            raise ValueError("поддерживаются только ссылки t.me.")
        parts = [segment for segment in parsed.path.split("/") if segment]
        if len(parts) >= 2 and parts[0] == "c" and parts[1].isdigit():
            return int(f"-100{parts[1]}")
        if re.fullmatch(r"-?\d+", parts[-1] if parts else ""):
            return int(parts[-1])
        raise ValueError("не удалось извлечь chat id из ссылки.")

    def _require_backend(self) -> TelegramManagerBackend | None:
        if self.backend is not None:
            return self.backend

        active_session = self.session_manager.get_active_session_path()
        if active_session is None:
            messagebox.showerror(
                "Сессия не выбрана",
                "Сначала импортируй .session файл во вкладке «Аккаунты».",
            )
            return None

        try:
            self.settings = Settings.load()
            self.backend = TelegramManagerBackend(
                settings=self.settings,
                db=self.db,
                logger=self.logger,
                session_path=active_session,
            )
        except Exception as exc:
            messagebox.showerror(
                "Настройка недоступна",
                f"Не удалось инициализировать Telegram backend.\n{exc}",
            )
            return None
        return self.backend

    def on_close(self) -> None:
        try:
            if self.backend is not None:
                self.worker.submit(self.backend.disconnect()).result(timeout=10)
        except Exception:
            self.logger.exception("Ошибка при отключении backend")
        finally:
            self.worker.stop()
            self.root.destroy()


    @staticmethod
    def _resolve_storage_path(env_name: str, default: str) -> Path:
        raw = os.getenv(env_name, default).strip() or default
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()


def configure_windows_dpi() -> None:
    if not hasattr(ctypes, "windll"):
        return

    user32 = ctypes.windll.user32
    shcore = getattr(ctypes.windll, "shcore", None)

    per_monitor_aware_v2 = ctypes.c_void_p(-4)

    try:
        user32.SetProcessDpiAwarenessContext(per_monitor_aware_v2)
        return
    except Exception:
        pass

    if shcore is None:
        return

    process_per_monitor_dpi_aware = 2
    try:
        shcore.SetProcessDpiAwareness(process_per_monitor_dpi_aware)
    except Exception:
        pass


def main() -> None:
    configure_windows_dpi()
    root = TkinterDnD.Tk()
    root.option_add("*Font", "{Segoe UI} 10")
    MainMenuWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
