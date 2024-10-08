#! /usr/bin/env python
import os
import pathlib
import sqlite3
import tomllib
from collections import OrderedDict
from contextlib import contextmanager
from email.mime.text import MIMEText
from email.parser import HeaderParser, Parser
from email.policy import default as default_policy

import logzero
from dateutil.parser import parse as parse_date
from dateutil.tz import tzlocal
from imap_tools import A
from imap_tools.consts import MailMessageFlags
from logzero import logger
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.logging import TextualHandler
from textual.message import Message
from textual.widgets import (Button, Footer, Header, ListItem, ListView,
                             LoadingIndicator, Static)

from gmailtuilib.imap import (compress_uids, fetch_google_messages,
                              get_mailbox, is_starred, is_unread,
                              uid_seq_to_criteria)
from gmailtuilib.message import (CompositionScreen, MessageDismissResult,
                                 MessageItem, MessageScreen, InboxMessageScreen)
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
                                sql_insert_ml, sql_message_exists,
                                sql_update_message_unread)

handlers = logzero.logger.handlers[:]
for handler in handlers:
    logzero.logger.removeHandler(handler)
logzero.logger.addHandler(TextualHandler())


class Messages(ListView):
    BINDINGS = [
        ("a", "archive", "Archive message"),
        ("t", "trash", "Trash message"),
        ("u", "toggle_unread", "Toggle (un)read"),
    ]
    message_threads = OrderedDict()
    uids_in_view = set([])
    skip_refresh = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    class Mounted(Message):
        pass

    def on_mount(self):
        self.post_message(self.Mounted())

    def refresh_listview(self):
        """
        Refresh the list view to match the data.
        """
        skip_refresh = self.skip_refresh
        if skip_refresh:
            self.skip_refresh = False
            return
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
        if self.app.label == "INBOX":
            inbox = True
        else:
            inbox = False
        widget = MessageItem(
            gmessage_id,
            uid,
            date_str,
            sender,
            subject,
            starred=starred,
            unread=unread,
            inbox=inbox,
        )
        return widget

    def action_archive(self):
        """
        Archive a message.
        """
        index = self.index
        if index is None or index < 0:
            return
        li = self.children[index]
        mi = li.children[0]
        uid = mi.uid
        del self.message_threads[uid]
        logger.debug(f"Preparing to archive INBOX message with UID: {uid} ...")
        self.app.archive_message(uid)
        self.pop(index)
        index -= 1
        if index <= 0:
            index = 1
        self.index = index
        self.skip_refresh = True

    def action_trash(self):
        """
        Trash message.
        """
        index = self.index
        if index is None or index < 0:
            return
        li = self.children[index]
        mi = li.children[0]
        uid = mi.uid
        del self.message_threads[uid]
        self.app.trash_message(uid, self.app.label)
        self.remove_items([index])
        index -= 1
        if index <= 0:
            index = 1
        self.index = index
        self.skip_refresh = True

    def action_toggle_unread(self):
        index = self.index
        if index is None or index < 0:
            return
        li = self.children[index]
        mi = li.children[0]
        uid = mi.uid
        gmessage_id = mi.gmessage_id
        unread = mi.unread
        mi.unread = not unread
        self.app.mark_message_read_status(uid, self.app.label, read=unread)
        self.app.mark_cached_message_read_status(None, gmessage_id, read=unread)


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
        "inbox_msg_screen": InboxMessageScreen(),
        "composition_screen": CompositionScreen(),
        "search_screen": SearchScreen(),
        "search_results_screen": SearchResultsScreen(),
    }
    CSS_PATH = "gmail_app.tcss"
    BINDINGS = [
        Binding("ctrl+d", "toggle_dark", "Toggle dark mode", priority=True, show=True),
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
        yield Header(show_clock=True)
        yield MainPanel()
        yield Footer()

    def on_list_view_selected(self, event):
        list_item = event.item
        logger.debug(f"item: {list_item}")
        mi = list_item.children[0]
        uid = mi.uid
        gmessage_id = mi.gmessage_id
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
            screen = self.SCREENS["inbox_msg_screen"]
            logger.debug(f"Selected message subject: {msg['subject']}")
            screen.msg = msg
            # Mark remote message as read
            self.mark_message_read_status(uid, self.label, read=True)
            # Mark cached message as read
            self.mark_cached_message_read_status(cursor, gmessage_id, read=True)
            conn.commit()

        def handle_message_exit(result):
            if result is None:
                return
            if result == MessageDismissResult.EXIT:
                return
            if result == MessageDismissResult.ARCHIVE:
                logger.debug("Archiving message ...")
                self.archive_message(uid)
                return
            if result == MessageDismissResult.TRASH:
                logger.debug("Trashing message ...")
                self.trash_message(uid, self.label)
                return

        self.push_screen(screen, handle_message_exit)

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
                    uncached_message_uids = list(uncached_message_uids)
                    uncached_message_uids.sort()
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

    def mark_cached_message_read_status(self, cursor, gmessage_id, read=True):
        """
        Alter the cached read/unread status of a message.
        `cursor` may be None, in which case a new connection will be created.
        """
        with self.get_cursor_if_needed(cursor) as cursor:
            unread = int(not read)
            cursor.execute(sql_update_message_unread, [unread, gmessage_id])

    @contextmanager
    def get_cursor_if_needed(self, cursor=None):
        """
        Context manager.
        If passed in cursor is None, establish connection and yield a new cursor.
        Otherwise, use existing cursor.
        """
        if cursor is None:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA foreign_keys = ON;")
                cursor = conn.cursor()
                yield cursor
        else:
            yield cursor

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
            if update_only:
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
                    cursor, gmessage_id, gthread_id, glabels, msg, update_only=True
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

    @work(exclusive=True, group="toggle-message-seen", thread=True)
    def mark_message_read_status(self, uid, label, read=True):
        """
        Mark messages read/unread.
        """
        access_token = get_oauth2_access_token(self.config)
        with get_mailbox(self.config, access_token) as mailbox:
            mailbox.folder.set(label)
            uids = [str(uid)]
            flags = MailMessageFlags.SEEN
            value = read
            mailbox.flag(uids, flags, value)

    @work(exclusive=True, group="archive-message", thread=True)
    def archive_message(self, uid):
        """
        Archive a GMail Inbox message.
        """
        access_token = get_oauth2_access_token(self.config)
        with get_mailbox(self.config, access_token) as mailbox:
            mailbox.folder.set("INBOX")
            uids = [str(uid)]
            result = mailbox.delete(uids)
            logger.debug(f"Result of mailbox.delete([{uid}]): {result}")

    @work(exclusive=True, group="trash-message", thread=True)
    def trash_message(self, uid, label):
        """
        Move message to the trash.
        """
        access_token = get_oauth2_access_token(self.config)
        with get_mailbox(self.config, access_token) as mailbox:
            mailbox.folder.set(label)
            uids = [str(uid)]
            mailbox.move(uids, "[Gmail]/Trash")

    @work(exclusive=True, group="restore-message", thread=True)
    def restore_to_inbox(self, uid, from_curr_label=False):
        """
        Restore a message to the inbox.
        uid: UID of the message to restore.
        from_curr_label: If True, copy from the current label.
            Otherwise, copy from "[Gmail]/All Mail".
        """
        if from_curr_label:
            folder = self.label
        else:
            folder = "[Gmail]/All Mail"
        access_token = get_oauth2_access_token(self.config)
        with get_mailbox(self.config, access_token) as mailbox:
            mailbox.folder.set(folder)
            uids = [str(uid)]
            mailbox.copy(uids, "INBOX")

    @work(exclusive=False, group="smtp-send", thread=True)
    def send_smtp_message(self, access_token, message_string, recipients, user):
        with gmail_smtp(user, access_token) as smtp:
            smtp.sendmail(user, recipients, message_string)

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.dark = not self.dark

    def action_quit(self):
        self.sync_messages_flag = False
        self.workers.cancel_all()
        self.exit()
        logger.debug("Shutting down ...")

    def action_compose(self):
        screen = self.SCREENS["composition_screen"]
        screen.text = ""
        screen.subject = ""
        screen.recipients = ""
        logger.debug("Blanked composition.")

        def send_message(info):
            if info is None:
                return
            headers, text = info
            logger.debug(f"HEADERS: {headers}")
            logger.debug(f"TEXT: {text}")
            access_token = get_oauth2_access_token(self.config)
            user = self.config["oauth2"]["email"]
            message = MIMEText(text, policy=default_policy)
            message["From"] = user
            recipients = headers["To"]
            message["To"] = recipients
            message["Subject"] = headers["Subject"]
            self.send_smtp_message(access_token, message.as_string(), recipients, user)

        self.push_screen(screen, send_message)

    def action_search(self):
        screen = self.SCREENS["search_screen"]

        def process_search_form(search_fields):
            if search_fields is None:
                return
            logger.debug(f"SEARCH FIELDS: {search_fields}")
            screen = self.SCREENS["search_results_screen"]
            screen.search_fields = search_fields
            screen.search_completed = False
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
