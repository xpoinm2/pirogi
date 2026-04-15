from __future__ import annotations

import ctypes
import traceback
from concurrent.futures import Future
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from app.db import Database
from app.gui.async_worker import AsyncWorker
from app.gui.backend import AuthResult, TelegramManagerBackend
from app.logging_setup import setup_logging
from app.models import DialogInfo, ImportMessageItem, ScheduleBatchResult, ScheduledMessageInfo
from app.settings import Settings
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

        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(root)
        notebook.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        main_menu_tab = ttk.Frame(notebook, padding=16)
        accounts_tab = ttk.Frame(notebook, padding=16)
        notebook.add(main_menu_tab, text="Главное меню")
        notebook.add(accounts_tab, text="Аккаунты")

        ttk.Label(main_menu_tab, text="Главное меню", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            main_menu_tab,
            text="Раздел готов к наполнению функциями.",
            foreground="#555555",
        ).pack(anchor="w", pady=(8, 0))

        ttk.Label(accounts_tab, text="Аккаунты", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            accounts_tab,
            text="Пока пусто — вкладка подготовлена для будущих функций.",
            foreground="#555555",
        ).pack(anchor="w", pady=(8, 0))


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
