# Telegram Manager Desktop (Telethon, personal account)

Desktop application and CLI project for Telegram management through a **personal account** with **Telethon**, **SQLite**, **dotenv**, drag-and-drop import and Windows-friendly startup scripts.

## Features

- Login through `api_id`, `api_hash` and a Telethon session
- Desktop GUI on `tkinter`
- Browse button for CSV / JSON import
- Drag-and-drop for CSV / JSON import files
- List dialogs and choose a target chat in a table
- Import CSV / JSON
- Validate rows before scheduling
- Mass schedule up to 100 messages per chat
- Show already scheduled messages
- Cancel one or several scheduled messages
- Local SQLite audit trail
- File + console logs
- `FloodWait` retry handling
- Dry-run mode
- CLI remains available for automation / fallback

## Project structure

```text
telegram_manager_telethon/
├── app/
│   ├── cli.py
│   ├── db.py
│   ├── exceptions.py
│   ├── logging_setup.py
│   ├── main.py
│   ├── models.py
│   ├── settings.py
│   ├── utils.py
│   ├── gui/
│   │   ├── app.py
│   │   ├── async_worker.py
│   │   └── backend.py
│   ├── importers/
│   │   ├── csv_importer.py
│   │   ├── json_importer.py
│   │   └── schemas.py
│   ├── services/
│   │   └── scheduler_service.py
│   └── telegram/
│       ├── auth.py
│       ├── chats.py
│       ├── client.py
│       ├── retry.py
│       └── scheduled.py
├── config/
│   └── README.md
├── data/
│   └── sessions/
├── logs/
├── .env.example
├── requirements.txt
├── run.py
├── run_gui.bat
├── run_menu.bat
├── sample_messages.csv
├── sample_messages.json
└── setup_windows.bat
```

## GUI workflow

1. Start the app with `run_gui.bat`
2. On the **Авторизация** tab:
   - enter `Phone`
   - click **Запросить код**
   - enter `Code`
   - if needed, enter `2FA password`
   - click **Войти**
3. On **Импорт и постановка**:
   - click **Загрузить диалоги**
   - select a chat in the table
   - choose your file via **Browse...** or drag-and-drop it into the drop zone
   - click **Предпросмотр**
   - optionally enable **Dry run**
   - click **Поставить в scheduled queue**
4. On **Scheduled messages**:
   - click **Обновить**
   - select rows and click **Отменить выбранные**
   - or paste message IDs and click **Отменить IDs**
5. On **Локальная SQLite**:
   - review the local audit trail

## Configuration

1. Copy `.env.example` to `config/.env`
2. Fill:
   - `API_ID`
   - `API_HASH`
   - optionally `DEFAULT_PHONE`
   - optionally `STRING_SESSION` if you already have one
3. If `STRING_SESSION` is empty, a file session will be used.

## CSV format

Required columns:

- `text`
- `send_at`
- `attachment_path`
- `disable_preview`

Example:

```csv
text,send_at,attachment_path,disable_preview
"Hello","2026-05-01 10:00:00","",true
"File message","2026-05-01 11:00:00","data/attachments/file.pdf",false
```

## JSON format

```json
[
  {
    "text": "Hello",
    "send_at": "2026-05-01 10:00:00",
    "attachment_path": "",
    "disable_preview": true
  }
]
```

## Commands

### Desktop GUI

```bash
python run.py gui
```

### Interactive CLI menu

```bash
python run.py menu
```

### Login

```bash
python run.py login
```

### List dialogs

```bash
python run.py dialogs
```

### Validate import file only

```bash
python run.py preview-import --file sample_messages.csv
```

### Dry-run scheduling

```bash
python run.py schedule --file sample_messages.csv --dry-run
```

### Schedule to a specific dialog id

```bash
python run.py schedule --file sample_messages.csv --chat-id 123456789
```

### Show scheduled messages

```bash
python run.py list-scheduled --chat-id 123456789
```

### Cancel scheduled messages

```bash
python run.py cancel --chat-id 123456789 --message-ids 101 102 103
```

## Windows quick start

```bat
copy .env.example config\.env
setup_windows.bat
run_gui.bat
```

## Notes

- `disable_preview` is applied to **text-only** messages sent through `send_message`.
- When a row has `attachment_path`, the project uses `send_file` with `caption=text`.
- Scheduling is done sequentially on purpose, to keep behavior predictable and logs clean.
- The project stores an audit trail locally in SQLite even when remote Telegram state later changes.
- The GUI and CLI use the same Telethon session and the same SQLite database.
