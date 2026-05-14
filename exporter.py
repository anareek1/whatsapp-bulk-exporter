#!/usr/bin/env python3
"""WhatsApp Web bulk chat exporter using Selenium."""

import argparse
import csv
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
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

    lock_file = profile_path / "SingletonLock"
    if lock_file.exists():
        lock_file.unlink()

    options = Options()
    options.add_argument(f"--user-data-dir={profile_path}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--remote-debugging-port=0")
    if headless:
        options.add_argument("--headless=new")

    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def wait_for_login(driver, timeout=120):
    print("Waiting for WhatsApp Web to load...")
    print("If this is the first time, scan the QR code with your phone.")
    print(f"You have {timeout} seconds...\n")
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '#pane-side'))
        )
        print("Logged in successfully!\n")
        time.sleep(5)
        return True
    except Exception:
        print("ERROR: Timed out waiting for login.")
        return False


def find_and_click_chat(driver, chat_name):
    """Find a chat by name in the sidebar and click it using JS."""
    clicked = driver.execute_script("""
        const target = arguments[0];
        const pane = document.querySelector('#pane-side');
        if (!pane) return false;
        const spans = pane.querySelectorAll('span[title]');
        for (const span of spans) {
            if (span.getAttribute('title') === target) {
                span.closest('[role="row"]').click();
                return true;
            }
        }
        return false;
    """, chat_name)

    if clicked:
        time.sleep(2)
        return bool(driver.find_elements(By.CSS_SELECTOR, '#main'))
    return False


def scroll_to_chat(driver, chat_name):
    """Scroll the chat list to find a specific chat, then click it."""
    pane = driver.find_element(By.CSS_SELECTOR, '#pane-side')
    chat_list = pane.find_elements(By.CSS_SELECTOR, '[data-testid="chat-list"]')
    scroll_target = chat_list[0] if chat_list else pane

    driver.execute_script("arguments[0].scrollTop = 0;", scroll_target)
    time.sleep(0.5)

    for _ in range(100):
        if find_and_click_chat(driver, chat_name):
            return True
        driver.execute_script(
            "arguments[0].scrollTop = arguments[0].scrollTop + arguments[0].offsetHeight;",
            scroll_target
        )
        time.sleep(0.8)

    return False


def get_all_chats_by_scrolling(driver):
    """Scroll through the entire chat list and collect all chat names with their order."""
    pane = driver.find_element(By.CSS_SELECTOR, '#pane-side')
    chat_list = pane.find_elements(By.CSS_SELECTOR, '[data-testid="chat-list"]')
    scroll_target = chat_list[0] if chat_list else pane

    driver.execute_script("arguments[0].scrollTop = 0;", scroll_target)
    time.sleep(1)

    chat_titles = []
    seen = set()
    last_count = 0
    stable_rounds = 0

    while stable_rounds < 3:
        rows = pane.find_elements(By.CSS_SELECTOR, '[role="row"]')
        for row in rows:
            try:
                container = row.find_elements(
                    By.CSS_SELECTOR, '[data-testid="cell-frame-container"]'
                )
                if not container:
                    continue
                title_els = container[0].find_elements(By.CSS_SELECTOR, 'span[title]')
                if title_els:
                    title = title_els[0].get_attribute("title")
                    if title and title not in seen:
                        seen.add(title)
                        chat_titles.append(title)
            except Exception:
                continue

        if len(chat_titles) == last_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            last_count = len(chat_titles)

        driver.execute_script(
            "arguments[0].scrollTop = arguments[0].scrollTop + arguments[0].offsetHeight;",
            scroll_target
        )
        time.sleep(1.5)

    driver.execute_script("arguments[0].scrollTop = 0;", scroll_target)
    time.sleep(1)

    return chat_titles


def iterate_and_export_chats(driver, export_dir, fmt, max_scroll, msg_pause):
    """Scroll through chat list, clicking each chat row directly to export."""
    pane = driver.find_element(By.CSS_SELECTOR, '#pane-side')
    chat_list = pane.find_elements(By.CSS_SELECTOR, '[data-testid="chat-list"]')
    scroll_target = chat_list[0] if chat_list else pane

    driver.execute_script("arguments[0].scrollTop = 0;", scroll_target)
    time.sleep(1)

    exported = set()
    summary = {"total": 0, "exported": 0, "failed": 0, "chats": []}
    stable_rounds = 0
    last_exported_count = 0

    while stable_rounds < 3:
        rows = pane.find_elements(By.CSS_SELECTOR, '[role="row"]')
        for row in rows:
            try:
                container = row.find_elements(
                    By.CSS_SELECTOR, '[data-testid="cell-frame-container"]'
                )
                if not container:
                    continue
                title_els = container[0].find_elements(By.CSS_SELECTOR, 'span[title]')
                if not title_els:
                    continue
                chat_name = title_els[0].get_attribute("title")
                if not chat_name or chat_name in exported:
                    continue

                exported.add(chat_name)
                summary["total"] += 1
                idx = summary["total"]
                print(f"[{idx}] {chat_name}")

                try:
                    ActionChains(driver).move_to_element(title_els[0]).click().perform()
                except Exception:
                    row.click()
                time.sleep(2)

                if not driver.find_elements(By.CSS_SELECTOR, '#main'):
                    print("  SKIPPED: chat did not open")
                    summary["failed"] += 1
                    continue

                scroll_chat_to_top(driver, max_attempts=max_scroll, pause=msg_pause)
                messages = extract_messages(driver)
                print(f"  Extracted {len(messages)} messages")

                if messages:
                    filepath = save_chat(chat_name, messages, export_dir, fmt=fmt)
                    print(f"  Saved to {filepath.name}")
                    summary["exported"] += 1
                    summary["chats"].append({
                        "name": chat_name, "messages": len(messages)
                    })
                else:
                    print("  SKIPPED: no messages")
                    summary["failed"] += 1

            except Exception as e:
                print(f"  ERROR: {e}")
                summary["failed"] += 1

        if len(exported) == last_exported_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            last_exported_count = len(exported)

        driver.execute_script(
            "arguments[0].scrollTop = arguments[0].scrollTop + arguments[0].offsetHeight;",
            scroll_target
        )
        time.sleep(1.5)

    return summary


def scroll_chat_to_top(driver, max_attempts=50, pause=2.0):
    print("  Loading history...", end="", flush=True)
    attempts = 0
    last_height = None

    while attempts < max_attempts:
        try:
            msg_pane = driver.find_element(By.CSS_SELECTOR, '#main [role="application"]')
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
    messages = []
    try:
        main = driver.find_element(By.CSS_SELECTOR, '#main')
        msg_rows = main.find_elements(By.CSS_SELECTOR, '[role="row"]')

        for row in msg_rows:
            try:
                raw_text = row.text.strip()
                if not raw_text:
                    continue
                msg_data = parse_message_text(raw_text)
                if msg_data:
                    messages.append(msg_data)
            except Exception:
                continue
    except Exception as e:
        print(f"  Error extracting messages: {e}")

    return messages


def parse_message_text(raw_text):
    lines = raw_text.split("\n")
    if not lines:
        return None

    timestamp = ""
    msg_type = "text"

    last_line = lines[-1].strip()
    time_match = re.search(r'(\d{1,2}:\d{2})', last_line)
    if time_match:
        timestamp = time_match.group(1)

    text_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r'^\d{1,2}:\d{2}$', stripped):
            continue
        if stripped in ("Изменено", "Edited"):
            continue
        text_lines.append(stripped)

    text = "\n".join(text_lines)
    if not text:
        return None

    return {
        "timestamp": timestamp,
        "text": text,
        "type": msg_type,
    }


def save_chat(chat_name, messages, export_dir, fmt="json"):
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
            writer = csv.DictWriter(f, fieldnames=["timestamp", "text", "type"])
            writer.writeheader()
            writer.writerows(messages)
    else:
        filepath = export_dir / f"{safe_name}.txt"
        with open(filepath, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(f"[{msg['timestamp']}] {msg['text']}\n")

    return filepath


def export_profile(profile_cfg, settings):
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

        print("Exporting chats...\n")
        summary = iterate_and_export_chats(driver, export_dir, fmt, max_scroll, msg_pause)

        summary_path = export_dir / "_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        print(f"\nDone! Exported {summary['exported']}/{summary['total']} chats")
        print(f"Failed: {summary['failed']}")
        print(f"Output: {export_dir}\n")

    finally:
        driver.quit()
        cleanup_profile_cache(name)


def cleanup_profile_cache(profile_name):
    """Delete Chrome cache dirs to free disk space. Keeps the session alive."""
    profile_path = PROFILES_DIR / profile_name
    cache_dirs = [
        "Default/Cache",
        "Default/Code Cache",
        "Default/Service Worker/CacheStorage",
        "Default/GPUCache",
        "GrShaderCache",
        "GraphiteDawnCache",
        "BrowserMetrics",
        "BrowserMetrics-spare.pma",
        "CrashpadMetrics-active.pma",
    ]
    freed = 0
    for d in cache_dirs:
        target = profile_path / d
        if target.is_dir():
            size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
            shutil.rmtree(target, ignore_errors=True)
            freed += size
        elif target.is_file():
            freed += target.stat().st_size
            target.unlink(missing_ok=True)

    if freed:
        print(f"  Cleaned {freed / 1024 / 1024:.0f} MB of cache from {profile_name}")


def link_profile(profile_name):
    print(f"Opening WhatsApp Web for profile: {profile_name}")
    print("Scan the QR code, then close the browser.\n")

    driver = create_driver(profile_name)
    driver.get("https://web.whatsapp.com")

    if wait_for_login(driver, timeout=300):
        print("Session saved! You can now run the export.")
    else:
        print("Login failed or timed out.")

    try:
        input("Press Enter to close the browser...")
    except EOFError:
        pass
    driver.quit()
    cleanup_profile_cache(profile_name)


def main():
    parser = argparse.ArgumentParser(description="WhatsApp Web Bulk Chat Exporter")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    link_parser = subparsers.add_parser("link", help="Link a phone by scanning QR code")
    link_parser.add_argument("profile", help="Profile name (e.g. andrea)")

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
