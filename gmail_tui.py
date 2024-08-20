#! /usr/bin/env python
import os
import pathlib
import sqlite3
import subprocess
import tempfile
import tomllib
from collections import OrderedDict
from email.mime.text import MIMEText
# from email.parser import BytesHeaderParser
from email.parser import HeaderParser, Parser
from email.policy import default as default_policy

import logzero
from dateutil.parser import parse as parse_date
from dateutil.tz import tzlocal
from imap_tools import A
from logzero import logger
from textual import work
from textual.app import App, ComposeResult
from textual.logging import TextualHandler
from textual.message import Message
from textual.widgets import (Button, Footer, Header, ListItem, ListView,
                             LoadingIndicator, Static)

from gmailtuilib.imap import (compress_uids, fetch_google_messages,
                              get_mailbox, is_starred, is_unread,
                              uid_seq_to_criteria)
from gmailtuilib.message import HeadersScreen, MessageItem, MessageScreen
from gmailtuilib.oauth2 import get_oauth2_access_token
from gmailtuilib.search import SearchResultsScreen, SearchScreen
from gmailtuilib.smtp import gmail_smtp
from gmailtuilib.sqllib import (sql_all_uids_for_label, sql_ddl_labels,
                                sql_ddl_labels_idx0, sql_ddl_message_labels,
                                sql_ddl_messages, sql_ddl_messages_idx0,
                                sql_delete_message_label,
                                sql_fetch_msgs_for_label, sql_find_ml,
                                sql_get_message_labels_in_uid_range,
                                sql_get_message_string_by_uid_and_label,
                                sql_insert_ml, sql_message_exists)

handlers = logzero.logger.handlers[:]
for handler in handlers:
    logzero.logger.removeHandler(handler)
logzero.logger.addHandler(TextualHandler())


class Messages(ListView):
    message_threads = OrderedDict()
    uids_in_view = set([])

    class Mounted(Message):
        pass

    def on_mount(self):
        self.post_message(self.Mounted())

    def refresh_listview(self):
        """
        Refresh the list view to match the data.
        """
        message_threads = self.message_threads
        try:
            loader = self.parent.query_one("#loading")
            if len(message_threads) == 0:
                loader.remove_class("invisible")
            else:
                loader.add_class("invisible")
        except Exception as ex:
            logger.debug(f"Could not get loader: {ex}")
        uids_should_be_in_view = set(message_threads.keys())
        uids_in_view = self.uids_in_view
        uids_to_be_removed_from_view = uids_in_view - uids_should_be_in_view
        uids_to_be_added_to_view = uids_should_be_in_view - uids_in_view
        messages_need_to_be_added = len(uids_to_be_added_to_view) > 0
        messages_need_to_be_deleted = len(uids_to_be_removed_from_view) > 0
        if not (messages_need_to_be_added or messages_need_to_be_deleted):
            for minfo, list_item in zip(message_threads.values(), self.children):
                unread = minfo["unread"]
                starred = minfo["starred"]
                message_item = list_item.children[0]
                message_item.unread = unread
                message_item.starred = starred
            return
        # Just clear out the view and rebuild it.
        curr_index = self.index
        new_index = None
        if curr_index is None:
            curr_uid = None
        else:
            curr_uid = self.children[curr_index].children[0].uid
        self.clear()
        for n, (uid, minfo) in enumerate(message_threads.items()):
            widget = self.create_message_item(uid, minfo)
            list_item = ListItem(widget)
            if uid == curr_uid:
                list_item.highlighted = True
                new_index = n
            if n % 2 == 0:
                widget.add_class("item-even")
            else:
                widget.add_class("item-odd")
            unread = minfo["unread"]
            if unread:
                list_item.add_class("unread")
            self.append(list_item)
        if new_index is None and len(message_threads) > 0:
            new_index = 1
        self.index = new_index
        uids_in_view.clear()
        uids_in_view.update(set(message_threads.keys()))

    def create_message_item(self, uid, minfo):
        gmessage_id = minfo["gmessage_id"]
        date_str = minfo["Date"]
        sender = minfo["From"]
        subject = minfo["Subject"]
        unread = minfo["unread"]
        starred = minfo["starred"]
        widget = MessageItem(
            gmessage_id,
            uid,
            date_str,
            sender,
            subject,
            starred=starred,
            unread=unread,
        )
        return widget


class ButtonBar(Static):
    def compose(self):
        yield Button("<", disabled=True, id="btn-backwards", classes="button")
        yield Button(">", disabled=False, id="btn-forwards", classes="button")


class MessageList(Static):
    def compose(self):
        yield Messages(id="messages")
        yield LoadingIndicator(id="loading")
        yield ButtonBar()


class MainPanel(Static):
    def compose(self):
        yield MessageList()


class GMailApp(App):
    """A Textual app to manage stopwatches."""

    SCREENS = {
        "msg_screen": MessageScreen(),
        "headers_screen": HeadersScreen(),
        "search_screen": SearchScreen(),
        "search_results_screen": SearchResultsScreen(),
    }
    CSS_PATH = "gmail_app.tcss"
    BINDINGS = [
        ("d", "toggle_dark", "Toggle dark mode"),
        ("q", "quit", "Quit"),
        ("c", "compose", "Compose message"),
        ("s", "search", "Search for messages"),
    ]

    page_size = 50
    page = 0
    label = "INBOX"
    sync_messages_flag = True
    min_uid = None
    max_uid = None

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        yield MainPanel()
        yield Footer()

    def on_list_view_selected(self, event):
        list_item = event.item
        logger.debug(f"item: {list_item}")
        uid = list_item.children[0].uid
        logger.debug(f"Selected message with UID {uid}.")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            cursor.execute(sql_get_message_string_by_uid_and_label, [self.label, uid])
            row = cursor.fetchone()
            if row is None:
                return
            message_string = row[0]
            parser = Parser(policy=default_policy)
            msg = parser.parsestr(message_string)
            # Get plain text from message
            screen = self.SCREENS["msg_screen"]
            logger.debug(f"Selected message subject: {msg['subject']}")
            screen.msg = msg
        # Stop workers
        for worker in self.workers:
            if worker.group == "refresh-listview":
                worker.cancel()
        self.push_screen(screen)

    def on_mount(self):
        with open(pathlib.Path("~/.gmail_tui/conf.toml").expanduser(), "rb") as f:
            self.config = tomllib.load(f)

        self.db_path = pathlib.Path("~/.gmail_tui/mail.db").expanduser()
        if not os.path.exists(self.db_path):
            self.create_db()
        self.sync_messages_flag = True
        self.sync_messages()
        self.set_interval(10, callback=self.refresh_listview, pause=False)

    @work(exclusive=True, group="refresh-listview", thread=True)
    def refresh_listview(self):
        """
        Refresh the UI listview.
        """
        try:
            messages_widget = self.query_one("#messages")
        except Exception:
            return
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
                gmessage_id,
                gthread_id,
                message_string,
                unread,
                starred,
                uid,
            ) in fetchrows(cursor, cursor.arraysize):
                uids.append(int(uid))
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
                    "gmessage_id": gmessage_id,
                    "Date": date_str,
                    "From": sender,
                    "Subject": subject,
                    "unread": unread,
                    "starred": starred,
                }
                message_threads[uid] = minfo
                n += 1
                if n >= self.page_size:
                    break
        logger.debug(f"Retrieved {n} rows for list view.")
        if len(uids) == 0:
            return
        self.min_uid = min(uids)
        self.max_uid = max(uids)
        messages_widget.message_threads = message_threads
        self.call_from_thread(messages_widget.refresh_listview)

    @work(exclusive=True, group="message-sync", thread=True)
    def sync_messages(self):
        logger.debug(f"Starting message sync for label {self.label} ...")
        while self.sync_messages_flag:
            try:
                access_token = get_oauth2_access_token(self.config)
                with get_mailbox(self.config, access_token) as mailbox, sqlite3.connect(
                    self.db_path
                ) as conn:
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute("PRAGMA foreign_keys = ON;")
                    cursor = conn.cursor()
                    self.insert_current_label(cursor)
                    conn.commit()
                    uid_set = set([])
                    uncached_message_uids = set([])
                    mailbox.folder.set(self.label)
                    # Get the set of messages that are in the mailbox.
                    for gmessage_id, gthread_id, glabels, msg in fetch_google_messages(
                        mailbox, headers_only=True, limit=500
                    ):
                        # Record message UID
                        uid_set.add(int(msg.uid))
                        # Update any cached messages
                        # Record any uncached messages that should be cached.
                        if self.get_cached_message(cursor, gmessage_id):
                            self.insert_or_update_message(
                                cursor,
                                gmessage_id,
                                gthread_id,
                                glabels,
                                msg,
                                update_only=True,
                            )
                        else:
                            uncached_message_uids.add(int(msg.uid))
                    # Remove any cached labels that are no longer applied.
                    self.remove_cached_labels(cursor, uid_set)
                    # Download and cache any uncached messages.
                    all_uids = list(uid_set)
                    all_uids.sort()
                    logger.debug(f"ALL UIDS: {all_uids}")
                    uncached_message_uids = list(uncached_message_uids)
                    uncached_message_uids.sort()
                    logger.debug(f"UNCACHED UIDS: {uncached_message_uids}")
                    uid_seq = compress_uids(all_uids, uncached_message_uids)
                    if len(uid_seq) > 0:
                        uid_criteria = uid_seq_to_criteria(uid_seq)
                        for (
                            gmessage_id,
                            gthread_id,
                            glabels,
                            msg,
                        ) in fetch_google_messages(
                            mailbox,
                            criteria=A(uid=uid_criteria),
                            headers_only=False,
                            limit=500,
                        ):
                            self.insert_or_update_message(
                                cursor, gmessage_id, gthread_id, glabels, msg
                            )
                    conn.commit()
                    logger.debug(f"Message sync complete for query: {self.label}")
                    self.accept_imap_updates(mailbox, conn)
            except Exception as ex:
                logger.debug(f"[DEGUB] exception closed imap mailbox: {type(ex)}, {ex}")

    def remove_cached_labels(self, cursor, uid_set):
        """
        Remove cached labels for UIDs no longer in the mailbox.
        """
        cursor.execute(sql_all_uids_for_label, [self.label])
        message_labels_to_delete = []
        for row in fetchrows(cursor, num_rows=cursor.arraysize):
            row_id, uid = row
            if uid not in uid_set:
                logger.debug(f"UID {uid} to be deleted from label {self.label} ...")
                message_labels_to_delete.append(row_id)
        logger.debug(f"Row IDs of message labels to delete: {message_labels_to_delete}")
        for row_id in message_labels_to_delete:
            cursor.execute(sql_delete_message_label, [row_id])

    def get_cached_message(self, cursor, gmessage_id):
        """
        Return cached row or None.
        """
        cursor.execute(sql_message_exists, [gmessage_id])
        row = cursor.fetchone()
        return row

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
        logger.debug("Checking for deleted messages ...")
        min_uid = self.min_uid
        max_uid = self.max_uid
        if min_uid is None or max_uid is None:
            return
        logger.debug(f"min UID: {min_uid}, max UID: {max_uid}")
        cursor.execute(sql_get_message_labels_in_uid_range, [min_uid, max_uid])
        rows_to_delete = []
        for row in fetchrows(cursor, cursor.arraysize):
            row_id, uid = row
            if uid not in found_uids:
                rows_to_delete.append(row_id)
        for row_id in rows_to_delete:
            cursor.execute(sql_delete_message_label, [row_id])

    def insert_or_update_message(
        self, cursor, gmessage_id, gthread_id, glabels, msg, update_only=False
    ):
        """
        `msg` must be an imap_tools.message.Message.
        """
        flags = msg.flags
        unread = is_unread(flags)
        starred = is_starred(flags)
        cursor.execute("SELECT id FROM messages WHERE gmessage_id = ?", [gmessage_id])
        row = cursor.fetchone()
        if row is None:
            if not update_only:
                return
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
            logger.debug(
                "INSERTing message label for "
                f"gmessage_id {gmessage_id}, uid: {msg.uid}, label: {self.label}"
            )
            cursor.execute(sql_insert_ml, [gmessage_id, self.label, msg.uid])

    def accept_imap_updates(self, mailbox, conn):
        logger.debug("Accepting IMAP IDLE updates ...")
        while self.sync_messages_flag:
            with mailbox.idle as idle:
                responses = idle.poll(timeout=30)
            logger.debug(f"IDLE responses: {responses}")
            cursor = conn.cursor()
            # Check for changes to currently viewed UIDs
            found_uids = set([])
            for gmessage_id, gthread_id, glabels, msg in fetch_google_messages(
                mailbox,
                headers_only=True,
                limit=500,
            ):
                self.insert_or_update_message(
                    cursor, gmessage_id, gthread_id, glabels, msg
                )
                found_uids.add(int(msg.uid))
            # Check for deleted messages.
            self.check_for_deleted_messages(cursor, found_uids)
            # Check for new (unseen) messages.
            for gmessage_id, gthread_id, glabels, msg in fetch_google_messages(
                mailbox,
                criteria=A(seen=False),
                headers_only=False,
            ):
                self.insert_or_update_message(
                    cursor, gmessage_id, gthread_id, glabels, msg
                )
            cursor.close()
            conn.commit()
        logger.debug("No longer accepting IMAP IDLE updates.")

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
                logger.debug(f"Executing DDL: {sql}")
                cursor.execute(sql)
            conn.commit()

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.dark = not self.dark

    def action_quit(self):
        self.sync_messages_flag = False
        self.workers.cancel_all()
        self.exit()
        logger.debug("Shutting down ...")

    def action_compose(self):
        screen = self.SCREENS["headers_screen"]
        screen.set_fields()
        logger.debug("Blanked headers.")

        def compose_message(headers):
            if headers is None:
                return
            EDITOR = os.environ.get("EDITOR", "vim")
            logger.debug(f"EDITOR is: {EDITOR}")
            with tempfile.NamedTemporaryFile("r+", suffix=".txt", delete=False) as tf:
                tfname = tf.name
            try:
                with self.suspend():
                    logzero.loglevel(logzero.CRITICAL)
                    subprocess.call([EDITOR, tfname])
                logzero.loglevel(logzero.DEBUG)
                with open(tfname, "r") as tf:
                    text = tf.read()
            finally:
                os.unlink(tfname)
            access_token = get_oauth2_access_token(self.config)
            user = self.config["oauth2"]["email"]
            message = MIMEText(text, policy=default_policy)
            message["From"] = user
            recipients = headers["To"]
            message["To"] = recipients
            message["Subject"] = headers["Subject"]
            with gmail_smtp(user, access_token) as smtp:
                smtp.sendmail(user, recipients, message.as_string())

        self.push_screen(screen, compose_message)

    def action_search(self):
        screen = self.SCREENS["search_screen"]

        def process_search_form(search_fields):
            if search_fields is None:
                return
            logger.debug(f"SEARCH FIELDS: {search_fields}")
            screen = self.SCREENS["search_results_screen"]
            screen.search_fields = search_fields
            self.push_screen(screen)

        self.push_screen(screen, process_search_form)

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
