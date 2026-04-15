from __future__ import annotations

import ctypes
import os
import traceback
from concurrent.futures import Future
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

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
        ttk.Button(frame, text="Запросить код", command=self.request_code).grid(row=1, column=2, sticky="w", padx=(8, 0))

        ttk.Label(frame, text="Code").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.code_var, width=24).grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(frame, text="2FA password").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.password_var, width=24, show="*").grid(row=3, column=1, sticky="w", pady=4)

        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Button(actions, text="Войти", command=self.sign_in).pack(side="left")
        ttk.Button(actions, text="Проверить session", command=self.check_session).pack(side="left", padx=(8, 0))

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

        ttk.Button(dialogs_box, text="Загрузить диалоги", command=self.load_dialogs).grid(row=0, column=0, sticky="w")
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
        ttk.Button(file_row, text="Browse...", command=self.browse_import_file).grid(row=0, column=1, padx=(8, 0))

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
        ttk.Button(options_row, text="Предпросмотр", command=self.preview_import_file).pack(side="left", padx=(10, 0))
        ttk.Button(options_row, text="Поставить в scheduled queue", command=self.schedule_file).pack(side="left", padx=(10, 0))

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
        ttk.Button(top, text="Обновить", command=self.refresh_scheduled).pack(side="left", padx=(12, 0))
        ttk.Button(top, text="Отменить выбранные", command=self.cancel_selected_scheduled).pack(side="left", padx=(8, 0))
        ttk.Entry(top, textvariable=self.cancel_ids_var, width=30).pack(side="left", padx=(16, 0))
        ttk.Button(top, text="Отменить IDs", command=self.cancel_manual_ids).pack(side="left", padx=(8, 0))

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
        ttk.Button(top, text="Показать записи SQLite", command=self.refresh_local_records).pack(side="left")

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
        self.accounts_summary_var = tk.StringVar(value="Аккаунты ещё не добавлены.")
        self.total_accounts_var = tk.StringVar(value="0")
        self.live_accounts_var = tk.StringVar(value="0")
        self.frozen_accounts_var = tk.StringVar(value="0")
        self.deleted_accounts_var = tk.StringVar(value="0")

        self.session_dir = self._resolve_storage_path("SESSION_DIR", "data/sessions")
        self.database_path = self._resolve_storage_path("DATABASE_PATH", "data/telegram_manager.db")
        self.db = Database(self.database_path)
        self.db.init()
        self.session_manager = SessionManager(session_dir=self.session_dir)
        self.worker = AsyncWorker()
        self.settings: Settings | None = None
        self.backend: TelegramManagerBackend | None = None
        self.logger = setup_logging(self._resolve_storage_path("LOG_DIR", "logs"), "INFO")
        self.relay_mode_var = tk.StringVar(value="one_to_one")
        self.relay_source_chat_var = tk.StringVar()
        self.relay_plan_var = tk.StringVar()
        self.relay_message_ids_var = tk.StringVar()
        self.relay_target_ids_var = tk.StringVar()
        self.relay_delay_min_var = tk.StringVar(value="180")
        self.relay_delay_max_var = tk.StringVar(value="360")
        self.relay_long_every_var = tk.StringVar(value="20")
        self.relay_long_min_var = tk.StringVar(value="300")
        self.relay_long_max_var = tk.StringVar(value="600")
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
        ttk.Label(
            main_menu_tab,
            text="Раздел готов к наполнению функциями.",
            foreground="#555555",
        ).pack(anchor="w", pady=(8, 0))

        self._build_relay_tab(relay_tab)
        self._build_accounts_tab(accounts_tab)
        self.refresh_accounts()

    def _build_accounts_tab(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)

        ttk.Label(frame, text="Аккаунты", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            frame,
            text=(
                "Импортируй Telethon-сессии (.session) через Browse, чтобы добавить аккаунт в программу.\n"
                "После добавления аккаунты доступны для дальнейших операций."
            ),
            foreground="#555555",
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        controls = ttk.Frame(frame)
        controls.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(controls, text="Добавить аккаунт (Browse...)", command=self.add_account_via_browse).pack(side="left")
        ttk.Button(controls, text="Обновить список", command=self.refresh_accounts).pack(side="left", padx=(8, 0))
        ttk.Label(
            controls,
            text=f"Session storage: {self.session_dir}",
            foreground="#666666",
        ).pack(side="left", padx=(16, 0))

        summary = ttk.LabelFrame(frame, text="Состояние аккаунтов", padding=10)
        summary.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        for index in range(4):
            summary.columnconfigure(index, weight=1)

        self._summary_cell(summary, "Всего", self.total_accounts_var, 0)
        self._summary_cell(summary, "Живые", self.live_accounts_var, 1)
        self._summary_cell(summary, "Замороженные", self.frozen_accounts_var, 2)
        self._summary_cell(summary, "Удалённые", self.deleted_accounts_var, 3)

        ttk.Label(summary, textvariable=self.accounts_summary_var, foreground="#444444").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(10, 0)
        )

        table_box = ttk.LabelFrame(frame, text="Список аккаунтов", padding=10)
        table_box.grid(row=4, column=0, sticky="nsew", pady=(12, 0))
        frame.rowconfigure(4, weight=1)
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

    def _build_relay_tab(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        ttk.Label(frame, text="Рассылка из чата", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            frame,
            text=(
                "Создай run для пересылки/копирования сообщений из исходного чата в целевые чаты.\n"
                "Поддерживаются one_to_one (план-файл) и all_to_all (списки ID)."
            ),
            foreground="#555555",
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(8, 10))

        config_box = ttk.LabelFrame(frame, text="Параметры запуска", padding=10)
        config_box.grid(row=2, column=0, sticky="ew")
        for i in range(6):
            config_box.columnconfigure(i, weight=1 if i in (1, 3, 5) else 0)

        ttk.Label(config_box, text="Source chat ID").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(config_box, textvariable=self.relay_source_chat_var, width=22).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(config_box, text="Режим").grid(row=0, column=2, sticky="w", padx=(12, 0), pady=4)
        ttk.Combobox(
            config_box,
            textvariable=self.relay_mode_var,
            values=("one_to_one", "all_to_all"),
            state="readonly",
            width=16,
        ).grid(row=0, column=3, sticky="w", pady=4)
        ttk.Checkbutton(config_box, text="Dry run", variable=self.relay_dry_run_var).grid(
            row=0, column=4, sticky="w", padx=(12, 0), pady=4
        )

        ttk.Label(config_box, text="Plan file (CSV/JSON)").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(config_box, textvariable=self.relay_plan_var).grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)
        ttk.Button(config_box, text="Browse...", command=self.browse_relay_plan).grid(row=1, column=4, sticky="w", padx=(8, 0))
        ttk.Button(config_box, text="Проверить план", command=self.preview_relay_plan).grid(
            row=1, column=5, sticky="w", padx=(8, 0)
        )

        ttk.Label(config_box, text="Message IDs").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(config_box, textvariable=self.relay_message_ids_var).grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(config_box, text="Target chat IDs").grid(row=2, column=3, sticky="w", pady=4)
        ttk.Entry(config_box, textvariable=self.relay_target_ids_var).grid(row=2, column=4, columnspan=2, sticky="ew", pady=4)

        ttk.Label(config_box, text="Delay min/max (sec)").grid(row=3, column=0, sticky="w", pady=4)
        delay_row = ttk.Frame(config_box)
        delay_row.grid(row=3, column=1, sticky="w", pady=4)
        ttk.Entry(delay_row, textvariable=self.relay_delay_min_var, width=8).pack(side="left")
        ttk.Label(delay_row, text="/").pack(side="left", padx=4)
        ttk.Entry(delay_row, textvariable=self.relay_delay_max_var, width=8).pack(side="left")

        ttk.Label(config_box, text="Long pause every N").grid(row=3, column=2, sticky="w", padx=(12, 0), pady=4)
        ttk.Entry(config_box, textvariable=self.relay_long_every_var, width=8).grid(row=3, column=3, sticky="w", pady=4)

        ttk.Label(config_box, text="Long pause min/max").grid(row=3, column=4, sticky="w", pady=4)
        long_row = ttk.Frame(config_box)
        long_row.grid(row=3, column=5, sticky="w", pady=4)
        ttk.Entry(long_row, textvariable=self.relay_long_min_var, width=8).pack(side="left")
        ttk.Label(long_row, text="/").pack(side="left", padx=4)
        ttk.Entry(long_row, textvariable=self.relay_long_max_var, width=8).pack(side="left")

        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, sticky="ew", pady=(12, 8))
        ttk.Button(actions, text="Старт рассылки", command=self.start_relay_run).pack(side="left")
        ttk.Label(actions, text="Run ID").pack(side="left", padx=(16, 6))
        ttk.Entry(actions, textvariable=self.relay_run_id_var, width=10).pack(side="left")
        ttk.Button(actions, text="Статус", command=self.relay_status).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Пауза", command=self.relay_pause).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Возобновить", command=self.relay_resume).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Обновить список run", command=self.refresh_relay_runs).pack(side="left", padx=(8, 0))

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

    def browse_relay_plan(self) -> None:
        path = filedialog.askopenfilename(
            title="Выбери CSV/JSON план рассылки",
            filetypes=[("CSV / JSON", "*.csv *.json"), ("All files", "*.*")],
        )
        if path:
            self.relay_plan_var.set(path)

    def preview_relay_plan(self) -> None:
        backend = self._require_backend()
        if backend is None:
            return
        path = self.relay_plan_var.get().strip()
        if not path:
            raise_message("Сначала выбери plan file.")
            return
        future = self.worker.submit(backend.preview_relay_plan(file_path=path))
        self._watch_main_future(future, self._on_relay_plan_preview, "Проверка relay плана")

    def start_relay_run(self) -> None:
        backend = self._require_backend()
        if backend is None:
            return
        source_chat_id = self._parse_single_int(self.relay_source_chat_var.get(), "Source chat ID")
        if source_chat_id is None:
            return
        parsed = self._collect_relay_params()
        if parsed is None:
            return
        future = self.worker.submit(
            backend.start_relay_run(
                source_chat_id=source_chat_id,
                mode=self.relay_mode_var.get().strip() or "one_to_one",
                file_path=self.relay_plan_var.get().strip() or None,
                message_ids=parsed["message_ids"],
                target_chat_ids=parsed["target_chat_ids"],
                delay_min=parsed["delay_min"],
                delay_max=parsed["delay_max"],
                long_pause_every=parsed["long_pause_every"],
                long_pause_min=parsed["long_pause_min"],
                long_pause_max=parsed["long_pause_max"],
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

    def _on_relay_plan_preview(self, pairs: list[tuple[int, int]]) -> None:
        self.relay_status_var.set(f"План валиден. Пар: {len(pairs)}")

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
        long_every = self._parse_single_int(self.relay_long_every_var.get(), "Long pause every")
        long_min = self._parse_single_int(self.relay_long_min_var.get(), "Long pause min")
        long_max = self._parse_single_int(self.relay_long_max_var.get(), "Long pause max")
        if None in {delay_min, delay_max, long_every, long_min, long_max}:
            return None

        if delay_min <= 0 or delay_max <= 0 or delay_min > delay_max:
            raise_message("Delay min/max заданы некорректно.")
            return None
        if long_every < 0:
            raise_message("Long pause every не может быть отрицательным.")
            return None
        if long_every > 0 and (long_min <= 0 or long_max <= 0 or long_min > long_max):
            raise_message("Long pause min/max заданы некорректно.")
            return None

        message_ids = self._parse_id_list(self.relay_message_ids_var.get(), "Message IDs")
        target_ids = self._parse_id_list(self.relay_target_ids_var.get(), "Target chat IDs")
        if message_ids is None or target_ids is None:
            return None
        return {
            "delay_min": delay_min,
            "delay_max": delay_max,
            "long_pause_every": long_every,
            "long_pause_min": long_min,
            "long_pause_max": long_max,
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

    @staticmethod
    def _parse_id_list(raw: str, label: str) -> list[int] | None:
        clean = [chunk for chunk in raw.replace(",", " ").split() if chunk]
        if not clean:
            return []
        try:
            return [int(chunk) for chunk in clean]
        except ValueError:
            raise_message(f"{label} должны содержать только целые числа.")
            return None

    def _require_backend(self) -> TelegramManagerBackend | None:
        if self.backend is not None:
            return self.backend
        try:
            self.settings = Settings.load()
            self.backend = TelegramManagerBackend(settings=self.settings, db=self.db, logger=self.logger)
        except Exception as exc:
            messagebox.showerror(
                "Настройка недоступна",
                f"Не удалось инициализировать Telegram backend.\nПроверь config/.env (API_ID/API_HASH).\n{exc}",
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
