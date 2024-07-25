#! /usr/bin/env python

import os
import pathlib
import sqlite3
from base64 import urlsafe_b64decode
from email.parser import BytesHeaderParser

import tomllib
from dateutil.parser import parse as parse_date
from textual import work
from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer
from textual.message import Message
from textual.widgets import Button, Footer, Header, Label, Static

from gmailtuilib.gmailapi import (get_gmail_credentials, get_gmail_labels,
                                  get_gmail_message, list_gmail_messages)
from gmailtuilib.sqllib import (sql_fetch_msgs_for_label, sql_find_ml,
                                sql_insert_ml)


class MessageItem(Static):
    def __init__(self, thread_id, date_str, sender, subject, **kwds):
        self.thread_id = thread_id
        self.date_str = date_str
        self.sender = sender
        self.subject = subject
        super().__init__(**kwds)

    def compose(self):
        yield Label(f"Date:    {self.date_str}")
        yield Label(f"From:    {self.sender}")
        yield Label(f"Subject: {self.subject}")


class Messages(ScrollableContainer):
    class Mounted(Message):
        pass

    def on_mount(self):
        self.post_message(self.Mounted())


class ButtonBar(Static):
    def compose(self):
        yield Button("<", disabled=True, id="btn-backwards", classes="button")
        yield Button(">", disabled=False, id="btn-forwards", classes="button")


class MessageList(Static):
    def compose(self):
        yield Messages(id="messages")
        yield ButtonBar()


class MainPanel(Static):
    def compose(self):
        yield MessageList()


class GMailApp(App):
    """A Textual app to manage stopwatches."""

    CSS_PATH = "gmail_app.tcss"
    BINDINGS = [("d", "toggle_dark", "Toggle dark mode"), ("x", "test", "Debbugging")]

    page_size = 100
    page = 0
    label = "INBOX"

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        yield MainPanel()
        yield Footer()

    def on_messages_mounted(self, message):
        # self.update_messages()
        pass

    def on_mount(self):
        with open(pathlib.Path("~/.gmail_tui/conf.toml").expanduser(), "rb") as f:
            self.config = tomllib.load(f)

        self.db_path = pathlib.Path("~/.gmail_tui/mail.db").expanduser()
        if not os.path.exists(self.db_path):
            self.create_db()
        self.credentials = get_gmail_credentials(self.config)
        self.sync_labels()
        self.sync_messages(self.label)
        self.set_timer(5, callback=self.refresh_listview, pause=False)

    @work(exclusive=True, group="refresh-listview", thread=True)
    def refresh_listview(self):
        """
        Refresh the UI listview.
        """
        print("Refreshing message list view ...")
        messages_widget = self.query_one("#messages")
        messages_widget.remove_children()
        skip_rows = self.page * self.page_size
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            cursor.execute(sql_fetch_msgs_for_label, [self.label, skip_rows])
            last_thread_id = None
            n = 0
            for message_id, thread_id, b64_message in fetchrows(cursor, cursor.arraysize):
                if b64_message is None:
                    b64_message, label_names = self.fetch_and_cache_message(conn, message_id)
                    if b64_message is None:
                        print(f"Could not get message {message_id}.")
                        continue
                    if self.label not in label_names:
                        print(f"Message {message_id} no longer has label {self.label}.")
                        continue
                if last_thread_id == thread_id:
                    # Skip additional messages in the same thread.
                    continue
                last_thread_id = thread_id
                if n % 2 == 0:
                    item_class = "item-even"
                else:
                    item_class = "item-odd"
                msg = decode_b64_message_headers(b64_message)
                date = msg.get("Date")
                dt = parse_date(date)
                date_str = dt.isoformat()
                sender = msg.get("From")
                subject = msg.get("Subject")
                widget = MessageItem(
                    thread_id, date_str, sender, subject, classes=item_class
                )
                self.call_from_thread(messages_widget.mount, widget)
                n += 1

    def fetch_and_cache_message(self, conn, message_id):
        """
        Fetch and cache a GMail message details.
        Returns b64 message and set of label names.
        """
        gmail_msg = get_gmail_message(self.credentials, message_id)
        b64_message = gmail_msg["raw"]
        label_ids = gmail_msg["labelIds"]
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE messages SET b64_message = ? WHERE message_id = ?",
            [b64_message, message_id],
        )
        conn.commit()
        sql = """\
            DELETE FROM message_labels
            WHERE message_id = (
                SELECT id
                FROM messages
                WHERE message_id = ?
            )
            """
        cursor.execute(sql, [message_id])
        sql = """\
            INSERT INTO message_labels (message_id, label_id)
            VALUES (
                (
                    SELECT id
                    FROM messages
                    WHERE message_id = ?
                ),
                (
                    SELECT id
                    FROM labels
                    WHERE label_id = ?
                )
            )
            """
        for label_id in label_ids:
            cursor.execute(sql, [message_id, label_id])
        conn.commit()
        sql = """\
            SELECT name
            FROM labels
                INNER JOIN message_labels
                    ON labels.id = message_labels.label_id
                INNER JOIN messages
                    ON message_labels.message_id = messages.id
            WHERE messages.message_id = ?
            """
        cursor.execute(sql, [message_id])
        label_names = set([])
        for row in fetchrows(cursor, cursor.arraysize):
            label_name = row[0]
            label_names.add(label_name)
        return b64_message, label_names

    @work(exclusive=True, group="label-sync", thread=True)
    def sync_labels(self):
        """
        Sync the cloud labels to the local DB.
        """
        print("Syncing labels ...")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            cursor.execute("UPDATE labels SET synced = FALSE")
            for label_id, label_name in get_gmail_labels(self.credentials):
                cursor.execute(
                    "SELECT id, name FROM labels WHERE label_id = ?", [label_id]
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        "INSERT INTO labels (label_id, name, synced) VALUES (?, ?, 1)",
                        [label_id, label_name],
                    )
                else:
                    cursor.execute(
                        "UPDATE labels SET name = ?, synced = 1 WHERE label_id = ?",
                        [label_name, label_id],
                    )
            cursor.execute("DELETE FROM labels WHERE synced = FALSE")
            conn.commit()
            print("Label sync complete.")

    @work(exclusive=True, group="message-sync", thread=True)
    def sync_messages(self, label):
        print(f"Syncing messages for query: {label} ...")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            for message_id, thread_id in list_gmail_messages(
                self.credentials, f"label:{label}"
            ):
                cursor.execute(
                    "SELECT id FROM messages WHERE message_id = ?", [message_id]
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        "INSERT INTO messages (message_id, thread_id) VALUES (?, ?)",
                        [message_id, thread_id],
                    )
                cursor.execute(sql_find_ml, [message_id, label])
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(sql_insert_ml, [message_id, label])
            conn.commit()
        print(f"Message sync complete for query: {label}")

    def create_db(self):
        """
        Create local DB for storing mail.
        """
        sql_messages = """\
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                message_id TEXT,
                thread_id TEXT,
                b64_message TEXT
            )
            """
        sql_labels = """\
            CREATE TABLE labels (
                id INTEGER PRIMARY KEY,
                label_id TEXT,
                name TEXT,
                synced INTEGER
            )
            """
        sql_message_labels = """\
            CREATE TABLE message_labels (
               message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
               label_id INTEGER REFERENCES labels(id) ON DELETE CASCADE,
               PRIMARY KEY (message_id, label_id)
            )
            """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            cursor = conn.cursor()
            cursor.execute(sql_messages)
            cursor.execute(sql_labels)
            cursor.execute(sql_message_labels)
            conn.commit()

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.dark = not self.dark

    def action_test(self):
        button = self.query_one("#btn-forwards")
        button.focus()

    def on_button_pressed(self, event: Button.Pressed):
        button = event.button
        if button.id == "btn-forwards":
            pass


def fetchrows(cursor, num_rows=10, row_wrapper=None):
    """
    Fetch rows in batches of size `num_rows` and yield those.
    """
    columns = list(entry[0] for entry in cursor.description)
    while True:
        rows = cursor.fetchmany(num_rows)
        if not rows:
            break
        for row in rows:
            if row_wrapper is not None:
                row = row_wrapper(columns, row)
            yield row


def decode_b64_message_headers(b64_message):
    """
    Decode the message headers from a base64 encoded message string.
    """
    mbytes = urlsafe_b64decode(b64_message.encode())
    parser = BytesHeaderParser()
    msg = parser.parsebytes(mbytes)
    return msg


if __name__ == "__main__":
    app = GMailApp()
    app.run()
