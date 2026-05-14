#!/usr/bin/env python3
"""WhatsApp Web bulk chat exporter using Selenium."""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

BASE_DIR = Path(__file__).parent
PROFILES_DIR = BASE_DIR / "profiles"
EXPORTS_DIR = BASE_DIR / "exports"


def load_config():
    with open(BASE_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


def create_driver(profile_name, headless=False):
    profile_path = PROFILES_DIR / profile_name
    profile_path.mkdir(parents=True, exist_ok=True)

    options = Options()
    options.add_argument(f"--user-data-dir={profile_path}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    if headless:
        options.add_argument("--headless=new")

    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def wait_for_login(driver, timeout=120):
    """Wait until WhatsApp Web is fully loaded (QR scanned or session restored)."""
    print("Waiting for WhatsApp Web to load...")
    print("If this is the first time, scan the QR code with your phone.")
    print(f"You have {timeout} seconds...\n")
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '#pane-side'))
        )
        print("Logged in successfully!\n")
        time.sleep(3)
        return True
    except Exception:
        print("ERROR: Timed out waiting for login.")
        return False


def get_chat_list(driver):
    """Get all chat elements from the side panel."""
    pane = driver.find_element(By.CSS_SELECTOR, '#pane-side')

    chat_titles = set()
    last_count = 0
    stable_rounds = 0

    while stable_rounds < 3:
        chats = pane.find_elements(By.CSS_SELECTOR, '[role="listitem"]')
        for chat in chats:
            try:
                title_el = chat.find_element(By.CSS_SELECTOR, 'span[title]')
                title = title_el.get_attribute("title")
                if title:
                    chat_titles.add(title)
            except Exception:
                continue

        if len(chat_titles) == last_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            last_count = len(chat_titles)

        driver.execute_script(
            "arguments[0].scrollTop = arguments[0].scrollTop + arguments[0].offsetHeight;",
            pane
        )
        time.sleep(1.5)

    driver.execute_script("arguments[0].scrollTop = 0;", pane)
    time.sleep(1)

    return sorted(chat_titles)


def search_and_open_chat(driver, chat_name):
    """Use the search bar to find and open a specific chat."""
    try:
        search_box = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'div[contenteditable="true"][data-tab="3"]')
            )
        )
        search_box.click()
        time.sleep(0.5)

        search_box.clear()
        search_box.send_keys(chat_name)
        time.sleep(2)

        results = driver.find_elements(By.CSS_SELECTOR, '#pane-side [role="listitem"]')
        for result in results:
            try:
                title_el = result.find_element(By.CSS_SELECTOR, 'span[title]')
                if title_el.get_attribute("title") == chat_name:
                    result.click()
                    time.sleep(2)
                    search_box.send_keys(Keys.ESCAPE)
                    time.sleep(0.5)
                    return True
            except Exception:
                continue

        search_box.send_keys(Keys.ESCAPE)
        return False
    except Exception as e:
        print(f"  Could not open chat: {e}")
        return False


def scroll_chat_to_top(driver, max_attempts=50, pause=2.0):
    """Scroll up in the chat to load older messages."""
    print("  Loading message history...", end="", flush=True)
    attempts = 0
    last_height = None

    while attempts < max_attempts:
        try:
            msg_pane = driver.find_element(
                By.CSS_SELECTOR, 'div[role="application"]'
            )
            current_height = driver.execute_script(
                "return arguments[0].scrollHeight;", msg_pane
            )

            if current_height == last_height:
                break

            last_height = current_height
            driver.execute_script("arguments[0].scrollTop = 0;", msg_pane)
            time.sleep(pause)
            attempts += 1
            print(".", end="", flush=True)
        except Exception:
            break

    print(f" done ({attempts} scrolls)")


def extract_messages(driver):
    """Extract all visible messages from the current chat."""
    messages = []
    try:
        msg_elements = driver.find_elements(
            By.CSS_SELECTOR, 'div[role="row"]'
        )

        for msg_el in msg_elements:
            try:
                msg_data = extract_single_message(msg_el)
                if msg_data:
                    messages.append(msg_data)
            except Exception:
                continue
    except Exception as e:
        print(f"  Error extracting messages: {e}")

    return messages


def extract_single_message(msg_el):
    """Parse a single message row into structured data."""
    text = ""
    sender = ""
    timestamp = ""
    msg_type = "text"

    copyable = msg_el.find_elements(By.CSS_SELECTOR, 'span.copyable-text')
    if copyable:
        data_pre = copyable[0].get_attribute("data-pre-plain-text") or ""
        if data_pre:
            parts = data_pre.strip("[]").split("] ", 1)
            if len(parts) == 2:
                timestamp = parts[0].strip("[ ")
                sender = parts[1].strip(": ")

    selectable = msg_el.find_elements(
        By.CSS_SELECTOR, 'span.selectable-text'
    )
    if selectable:
        text = selectable[0].text
    else:
        img = msg_el.find_elements(By.CSS_SELECTOR, 'img[src*="blob"]')
        if img:
            msg_type = "image"
            text = "[Image]"
        video = msg_el.find_elements(By.CSS_SELECTOR, 'span[data-icon="video-pip"]')
        if video:
            msg_type = "video"
            text = "[Video]"
        doc = msg_el.find_elements(By.CSS_SELECTOR, 'span[data-icon="document"]')
        if doc:
            msg_type = "document"
            text = "[Document]"
        audio = msg_el.find_elements(By.CSS_SELECTOR, 'span[data-icon="audio-play"]')
        if audio:
            msg_type = "audio"
            text = "[Audio]"

    if not text and not timestamp:
        return None

    return {
        "timestamp": timestamp,
        "sender": sender,
        "text": text,
        "type": msg_type,
    }


def save_chat(chat_name, messages, export_dir, fmt="json"):
    """Save extracted messages to file."""
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in chat_name)
    safe_name = safe_name.strip()[:100]

    if fmt == "json":
        filepath = export_dir / f"{safe_name}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(
                {"chat_name": chat_name, "exported_at": datetime.now().isoformat(),
                 "message_count": len(messages), "messages": messages},
                f, ensure_ascii=False, indent=2,
            )
    elif fmt == "csv":
        filepath = export_dir / f"{safe_name}.csv"
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "sender", "text", "type"])
            writer.writeheader()
            writer.writerows(messages)
    else:
        filepath = export_dir / f"{safe_name}.txt"
        with open(filepath, "w", encoding="utf-8") as f:
            for msg in messages:
                line = f"[{msg['timestamp']}] {msg['sender']}: {msg['text']}"
                f.write(line + "\n")

    return filepath


def export_profile(profile_cfg, settings):
    """Run the full export for one WhatsApp profile."""
    name = profile_cfg["name"]
    label = profile_cfg.get("label", name)
    fmt = settings.get("export_format", "json")
    headless = settings.get("headless", False)
    max_scroll = settings.get("max_scroll_attempts", 50)
    msg_pause = settings.get("message_load_pause", 2.0)

    print(f"\n{'='*60}")
    print(f"EXPORTING: {label} ({name})")
    print(f"{'='*60}\n")

    export_dir = EXPORTS_DIR / name
    export_dir.mkdir(parents=True, exist_ok=True)

    driver = create_driver(name, headless=headless)

    try:
        driver.get("https://web.whatsapp.com")

        if not wait_for_login(driver):
            return

        print("Scanning chat list...")
        chat_names = get_chat_list(driver)
        print(f"Found {len(chat_names)} chats\n")

        summary = {"total": len(chat_names), "exported": 0, "failed": 0, "chats": []}

        for i, chat_name in enumerate(chat_names, 1):
            print(f"[{i}/{len(chat_names)}] {chat_name}")

            if not search_and_open_chat(driver, chat_name):
                print("  SKIPPED: could not open chat")
                summary["failed"] += 1
                continue

            scroll_chat_to_top(driver, max_attempts=max_scroll, pause=msg_pause)
            messages = extract_messages(driver)
            print(f"  Extracted {len(messages)} messages")

            if messages:
                filepath = save_chat(chat_name, messages, export_dir, fmt=fmt)
                print(f"  Saved to {filepath}")
                summary["exported"] += 1
                summary["chats"].append({
                    "name": chat_name, "messages": len(messages)
                })
            else:
                print("  SKIPPED: no messages found")
                summary["failed"] += 1

        summary_path = export_dir / "_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        print(f"\nDone! Exported {summary['exported']}/{summary['total']} chats")
        print(f"Failed: {summary['failed']}")
        print(f"Output: {export_dir}\n")

    finally:
        driver.quit()


def link_profile(profile_name):
    """Open WhatsApp Web for QR code scanning only."""
    print(f"Opening WhatsApp Web for profile: {profile_name}")
    print("Scan the QR code, then close the browser.\n")

    driver = create_driver(profile_name)
    driver.get("https://web.whatsapp.com")

    if wait_for_login(driver, timeout=300):
        print("Session saved! You can now run the export.")
    else:
        print("Login failed or timed out.")

    input("Press Enter to close the browser...")
    driver.quit()


def main():
    parser = argparse.ArgumentParser(description="WhatsApp Web Bulk Chat Exporter")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    link_parser = subparsers.add_parser("link", help="Link a phone by scanning QR code")
    link_parser.add_argument("profile", help="Profile name (e.g. phone01)")

    export_parser = subparsers.add_parser("export", help="Export chats from a linked profile")
    export_parser.add_argument("profile", nargs="?", help="Profile name (omit for all)")
    export_parser.add_argument("--format", choices=["json", "csv", "txt"], help="Export format")

    subparsers.add_parser("list", help="List all configured profiles")

    args = parser.parse_args()
    config = load_config()
    profiles = config["profiles"]
    settings = config["settings"]

    if args.command == "link":
        link_profile(args.profile)

    elif args.command == "export":
        if args.format:
            settings["export_format"] = args.format

        if args.profile:
            match = [p for p in profiles if p["name"] == args.profile]
            if not match:
                print(f"Profile '{args.profile}' not found in config.yaml")
                sys.exit(1)
            export_profile(match[0], settings)
        else:
            print(f"Exporting all {len(profiles)} profiles...\n")
            for profile in profiles:
                export_profile(profile, settings)

    elif args.command == "list":
        print(f"Configured profiles ({len(profiles)}):\n")
        for p in profiles:
            linked = (PROFILES_DIR / p["name"]).exists()
            status = "LINKED" if linked else "NOT LINKED"
            print(f"  {p['name']:10s}  {p['label']:25s}  [{status}]")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
