# WhatsApp Bulk Exporter

Bulk export all chats from multiple WhatsApp Business numbers via WhatsApp Web using Selenium.

## How it works

1. You link each phone number by scanning a QR code (once per number)
2. The scraper opens WhatsApp Web, iterates through every chat, and exports all messages
3. Output is saved as JSON, CSV, or TXT — one file per chat

## Setup

```bash
# Install Python dependencies
pip install -r requirements.txt

# Chrome must be installed on your machine
```

## Usage

### Step 1: Link a phone number

```bash
python exporter.py link phone01
```

This opens WhatsApp Web. Ask the phone owner to scan the QR code (via video call or in person). The session is saved — you only need to do this once per number.

### Step 2: Export chats

```bash
# Export one profile
python exporter.py export phone01

# Export all 15 profiles
python exporter.py export

# Export as CSV
python exporter.py export phone01 --format csv
```

### Step 3: Check profiles

```bash
python exporter.py list
```

## Output

Exports are saved to `exports/<profile_name>/`:

```
exports/
  phone01/
    John Doe.json
    Marketing Group.json
    _summary.json
  phone02/
    ...
```

Each JSON file contains:

```json
{
  "chat_name": "John Doe",
  "exported_at": "2026-05-14T12:00:00",
  "message_count": 342,
  "messages": [
    {
      "timestamp": "12:30, 5/14/2026",
      "sender": "John Doe",
      "text": "Hello!",
      "type": "text"
    }
  ]
}
```

## Configuration

Edit `config.yaml` to customize profiles and settings:

- `profiles` — list of phone numbers to export (name + label)
- `settings.scroll_pause` — seconds between scrolls when loading chat list
- `settings.message_load_pause` — seconds between scrolls when loading message history
- `settings.max_scroll_attempts` — how far back to load messages per chat
- `settings.export_format` — default output format (json/csv/txt)

## Notes

- WhatsApp Web sessions last ~14 days before requiring a re-scan
- Each profile uses a separate Chrome user data directory
- WhatsApp Web DOM changes frequently — if selectors break, check the Issues tab
- Media files (images, videos) are not downloaded — they are marked as `[Image]`, `[Video]`, etc.
