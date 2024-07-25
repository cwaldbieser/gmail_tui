#! /usr/bin/env python

import os
import pathlib
import sqlite3
import time
from base64 import urlsafe_b64decode
from collections import OrderedDict
from email.parser import BytesHeaderParser

import tomllib
from dateutil.parser import parse as parse_date
from dateutil.tz import tzlocal
from textual import work
from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer
from textual.message import Message
from textual.widgets import Button, Footer, Header, Label, Static

from gmailtuilib.gmailapi import (get_gmail_credentials, get_gmail_labels,
                                  get_gmail_message, list_gmail_messages)
from gmailtuilib.sqllib import (sql_ddl_labels, sql_ddl_labels_idx0,
                                sql_ddl_message_labels, sql_ddl_messages,
                                sql_ddl_messages_idx0,
                                sql_fetch_msgs_for_label, sql_find_ml,
                                sql_insert_ml)


class MessageItem(Static):
    def __init__(self, message_id, date_str, sender, subject, **kwds):
        self.message_id = message_id
        self.date_str = date_str
        self.sender = sender
        self.subject = subject
        super().__init__(**kwds)

    def compose(self):
        yield Label(f"ID:      {self.message_id}", classes="diagnostic")
        yield Label(f"Date:    {self.date_str}")
        yield Label(f"From:    {self.sender}")
        yield Label(f"Subject: {self.subject}")


class Messages(ScrollableContainer):
    message_threads = OrderedDict()

    class Mounted(Message):
        pass

    def on_mount(self):
        self.post_message(self.Mounted())

    def refresh_listview(self):
        self.remove_children()
        message_threads = self.message_threads
        for n, (thread_id, threads) in enumerate(message_threads.items()):
            item_classes = []
            if n % 2 == 0:
                item_classes.append("item-even")
            else:
                item_classes.append("item-odd")
            minfo = threads[0]
            message_id = minfo["message_id"]
            date_str = minfo["Date"]
            sender = minfo["From"]
            subject = minfo["Subject"]
            unread = minfo["unread"]
            if unread:
                item_classes.append("unread")
            item_class = " ".join(item_classes)
            widget = MessageItem(
                message_id, date_str, sender, subject, classes=item_class
            )
            self.mount(widget)


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

    page_size = 50
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
        self.set_interval(15, callback=self.refresh_listview, pause=False)

    @work(exclusive=True, group="refresh-listview", thread=True)
    def refresh_listview(self):
        """
        Refresh the UI listview.
        """
        print("Refreshing message list view ...")
        messages_widget = self.query_one("#messages")
        skip_rows = self.page * self.page_size
        message_threads = OrderedDict()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            cursor.execute(sql_fetch_msgs_for_label, [self.label, skip_rows])
            n = 0
            for message_id, thread_id, b64_message in fetchrows(
                cursor, cursor.arraysize
            ):
                b64_message, label_names = self.fetch_and_cache_message(
                    conn, message_id
                )
                if b64_message is None:
                    print(f"Could not get message {message_id}.")
                    continue
                if self.label not in label_names:
                    print(f"Message {message_id} no longer has label {self.label}.")
                    continue
                threads = message_threads.setdefault(thread_id, [])
                msg = decode_b64_message_headers(b64_message)
                date = msg.get("Date")
                dt = parse_date(date)
                dt = dt.astimezone(tzlocal())
                date_str = dt.isoformat()
                sender = msg.get("From")
                subject = msg.get("Subject")
                unread = "UNREAD" in label_names
                minfo = {
                    "message_id": message_id,
                    "Date": date_str,
                    "From": sender,
                    "Subject": subject,
                    "unread": unread,
                }
                threads.append(minfo)
                n += 1
                if n >= self.page_size:
                    break
        messages_widget.message_threads = message_threads
        self.call_from_thread(messages_widget.refresh_listview)

    def fetch_and_cache_message(self, conn, message_id):
        """
        Fetch and cache a GMail message details.
        Returns b64 message and set of label names.
        """
        gmail_msg = get_gmail_message(self.credentials, message_id)
        b64_message = gmail_msg["raw"]
        label_ids = gmail_msg["labelIds"]
        for attempt in range(7):
            try:
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
            except sqlite3.OperationalError:
                time.sleep(2)
                continue
            break
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
            for label_id, label_name, label_type in get_gmail_labels(self.credentials):
                is_system = int(label_type == "system")
                cursor.execute(
                    "SELECT id, name FROM labels WHERE label_id = ?", [label_id]
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        """\
                            INSERT INTO labels (label_id, name, is_system, synced)
                            VALUES (?, ?, ?, 1)
                        """,
                        [label_id, label_name, is_system],
                    )
                else:
                    cursor.execute(
                        """\
                            UPDATE labels SET name = ?, is_system = ?, synced = 1
                            WHERE label_id = ?
                        """,
                        [label_name, is_system, label_id],
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
        ddl_statements = [
            sql_ddl_messages,
            sql_ddl_messages_idx0,
            sql_ddl_labels,
            sql_ddl_labels_idx0,
            sql_ddl_message_labels,
        ]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            cursor = conn.cursor()
            for sql in ddl_statements:
                cursor.execute(sql)
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
