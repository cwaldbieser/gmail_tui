#! /usr/bin/env python

import os
import pathlib
import sqlite3
from collections import OrderedDict
# from email.parser import BytesHeaderParser
from email.parser import HeaderParser
from email.policy import default as default_policy

import tomllib
from dateutil.parser import parse as parse_date
from dateutil.tz import tzlocal
from imap_tools import A, UidRange
from textual import work
from textual.app import App, ComposeResult
# from textual.containers import ScrollableContainer
from textual.message import Message
# from textual.reactive import reactive
from textual.widgets import (Button, Footer, Header, Label, ListItem, ListView,
                             Static)

from gmailtuilib.imap import (fetch_google_messages, get_imap_access_token,
                              get_mailbox, is_starred, is_unread)
from gmailtuilib.sqllib import (sql_all_uids_for_label, sql_ddl_labels,
                                sql_ddl_labels_idx0, sql_ddl_message_labels,
                                sql_ddl_messages, sql_ddl_messages_idx0,
                                sql_delete_message_label,
                                sql_fetch_msgs_for_label, sql_find_ml,
                                sql_get_message_labels_in_uid_range,
                                sql_insert_ml)


class MessageItem(Static):
    def __init__(
        self,
        message_id,
        uid,
        date_str,
        sender,
        subject,
        starred=False,
        unread=False,
        **kwds,
    ):
        self.message_id = message_id
        if uid is not None:
            self.uid = uid
        else:
            self.uid = ""
        self.date_str = date_str
        self.sender = sender
        self.subject = " ".join(subject.split())
        self.starred = starred
        self.unread = unread
        super().__init__(**kwds)

    def compose(self):
        starred = self.starred
        unread = self.unread
        icons = []
        if starred:
            icons.append("⭐")
        if unread:
            icons.append("")
        else:
            icons.append("")
        status_line = " ".join(icons)
        yield Label(status_line)
        yield Label(f"GMSGID:  {self.message_id}", classes="diagnostic")
        yield Label(f"UID:     {self.uid}", classes="diagnostic")
        yield Label(f"Date:    {self.date_str}")
        yield Label(f"From:    {self.sender}")
        yield Label(f"Subject: {self.subject}", classes="subject")

    def allow_focus(self):
        return True


class Messages(ListView):
    message_threads = OrderedDict()
    thread_map = {}

    class Mounted(Message):
        pass

    def on_mount(self):
        self.post_message(self.Mounted())

    def refresh_listview(self):
        curr_index = self.index
        self.clear()
        message_threads = self.message_threads
        for n, (thread_id, threads) in enumerate(message_threads.items()):
            minfo = threads[0]
            message_id = minfo["message_id"]
            uid = minfo["uid"]
            date_str = minfo["Date"]
            sender = minfo["From"]
            subject = minfo["Subject"]
            unread = minfo["unread"]
            starred = minfo["starred"]
            widget = MessageItem(
                message_id,
                uid,
                date_str,
                sender,
                subject,
                starred=starred,
                unread=unread,
            )
            list_item = ListItem(widget)
            if n % 2 == 0:
                widget.add_class("item-even")
            else:
                widget.add_class("item-odd")
            if unread:
                list_item.add_class("unread")
            if n == curr_index:
                list_item.highlighted = True
            self.append(list_item)

        thread_count = len(self.message_threads)
        if curr_index is not None and thread_count > curr_index:
            self.index = curr_index
        elif thread_count > 0:
            self.index = 0


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
        self.sync_messages()
        self.set_interval(10, callback=self.refresh_listview, pause=False)

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
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            cursor.execute(sql_fetch_msgs_for_label, [self.label, skip_rows])
            n = 0
            uids = []
            for (
                message_id,
                thread_id,
                message_string,
                unread,
                starred,
                uid,
            ) in fetchrows(cursor, cursor.arraysize):
                if uid is not None:
                    uids.append(int(uid))
                threads = message_threads.setdefault(thread_id, [])
                msg = parse_string_message_headers(message_string)
                date = msg.get("Date")
                dt = parse_date(date)
                dt = dt.astimezone(tzlocal())
                date_str = dt.isoformat()
                sender = msg.get("From")
                subject = msg.get("Subject")
                unread = bool(unread)
                starred = bool(starred)
                minfo = {
                    "message_id": message_id,
                    "uid": uid,
                    "Date": date_str,
                    "From": sender,
                    "Subject": subject,
                    "unread": unread,
                    "starred": starred,
                }
                threads.append(minfo)
                n += 1
                if n >= self.page_size:
                    break
        self.min_uid = min(uids)
        self.max_uid = max(uids)
        messages_widget.message_threads = message_threads
        self.call_from_thread(messages_widget.refresh_listview)

    @work(exclusive=True, group="message-sync", thread=True)
    def sync_messages(self):
        print(f"Starting message sync for label {self.label} ...")
        access_token = get_imap_access_token(self.config)
        with get_mailbox(self.config, access_token) as mailbox, sqlite3.connect(
            self.db_path
        ) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            self.insert_current_label(cursor)
            conn.commit()
            uid_set = set([])
            for gmessage_id, gthread_id, msg in fetch_google_messages(
                mailbox, headers_only=False, limit=500
            ):
                uid_set.add(msg.uid)
                self.insert_or_update_message(cursor, gmessage_id, gthread_id, msg)
            cursor.execute(sql_all_uids_for_label, [self.label])
            message_labels_to_delete = []
            print(f"Fetching all uids for label {self.label} ...")
            print(f"Found uid set includes: {sorted(list(uid_set))}")
            for row in fetchrows(cursor, num_rows=cursor.arraysize):
                row_id, uid = row
                print(f"Found row with uid: {uid}")
                if uid not in uid_set:
                    print(f"UID {uid} to be deleted from label {self.label} ...")
                    message_labels_to_delete.append(row_id)
            print(f"Row IDs of message labels to delete: {message_labels_to_delete}")
            for row_id in message_labels_to_delete:
                cursor.execute(sql_delete_message_label, [row_id])
            conn.commit()
            print(f"Message sync complete for query: {self.label}")
            self.accept_imap_updates(mailbox, conn)

    def insert_current_label(self, cursor):
        sql = """\
            SELECT id
            FROM labels
            WHERE label = ?
            """
        cursor.execute(sql, [self.label])
        row = cursor.fetchone()
        if row is None:
            sql = """\
                INSERT INTO labels (label) VALUES (?)
                """
            cursor.execute(sql, [self.label])

    def check_for_deleted_messages(self, cursor, found_uids):
        """
        Check for messages that have been removed from the current label with UID
        between self.min_uid and self.max_uid.
        """
        min_uid = self.min_uid
        max_uid = self.max_uid
        if min_uid is None or max_uid is None:
            return
        min_uid = min_uid
        max_uid = max_uid
        cursor.execute(sql_get_message_labels_in_uid_range, [min_uid, max_uid])
        rows_to_delete = []
        for row in fetchrows(cursor, cursor.arraysize):
            row_id, uid = row
            if uid not in found_uids:
                rows_to_delete.append(row_id)
        for row_id in rows_to_delete:
            cursor.execute(sql_delete_message_label, [row_id])

    def insert_or_update_message(self, cursor, gmessage_id, gthread_id, msg):
        """
        `msg` must be an imap_tools.message.Message.
        """
        flags = msg.flags
        unread = is_unread(flags)
        starred = is_starred(flags)
        cursor.execute("SELECT id FROM messages WHERE gmessage_id = ?", [gmessage_id])
        row = cursor.fetchone()
        if row is None:
            sql = """\
                INSERT INTO messages
                    (gmessage_id, gthread_id, message_string, unread, starred)
                    VALUES (?, ?, ?, ?, ?)
                """
            cursor.execute(
                sql,
                [gmessage_id, gthread_id, msg.obj.as_string(), unread, starred],
            )
        else:
            db_id = row[0]
            sql = "UPDATE messages SET unread = ?, starred = ? WHERE id = ?"
            cursor.execute(sql, [unread, starred, db_id])
        cursor.execute(sql_find_ml, [gmessage_id, self.label])
        row = cursor.fetchone()
        if row is None:
            cursor.execute(sql_insert_ml, [gmessage_id, self.label, msg.uid])

    def accept_imap_updates(self, mailbox, conn):
        print("Accepting IMAP IDLE updates ...")
        self.imap_idle = True
        while self.imap_idle:
            with mailbox.idle as idle:
                responses = idle.poll(timeout=60)
            if responses:
                print(f"Detected IMAP changes: {responses}")
                cursor = conn.cursor()
                # Check for changes to currently viewed UIDs
                found_uids = set([])
                for gmessage_id, gthread_id, msg in fetch_google_messages(
                    mailbox,
                    criteria=UidRange(str(self.min_uid), str(self.max_uid)),
                    headers_only=False,
                ):
                    self.insert_or_update_message(cursor, gmessage_id, gthread_id, msg)
                    found_uids.add(msg.uid)
                # Check for deleted messages.
                self.check_for_deleted_messages(cursor, found_uids)
                # Check for new (unseen) messages.
                for gmessage_id, gthread_id, msg in fetch_google_messages(
                    mailbox,
                    criteria=A(seen=False),
                    headers_only=False,
                ):
                    self.insert_or_update_message(cursor, gmessage_id, gthread_id, msg)
                cursor.close()
                conn.commit()
            else:
                print("No IMAP changes detected.")
        print("No longer accepting IMAP IDLE updates.")

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
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys = ON")
            cursor = conn.cursor()
            for sql in ddl_statements:
                print(f"Executing DDL: {sql}")
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


def parse_string_message_headers(message_string):
    """
    Parse a string into structured message headers.
    """
    parser = HeaderParser(policy=default_policy)
    msg = parser.parsestr(message_string)
    return msg


if __name__ == "__main__":
    app = GMailApp()
    app.run()
