import datetime
import sqlite3

from dateutil.parser import parse as parse_date
from dateutil.tz import tzlocal
from imap_tools import A
from logzero import logger
from textual import work
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import (Button, Footer, Input, Label, ListItem, ListView,
                             LoadingIndicator, Switch)

from gmailtuilib.imap import (fetch_google_messages, get_mailbox, is_starred,
                              is_unread, quote_imap_string)
from gmailtuilib.message import MessageItem, msg_to_email_msg, str_to_email_msg
from gmailtuilib.oauth2 import get_oauth2_access_token


class SearchScreen(ModalScreen):

    BINDINGS = [("escape", "app.pop_screen", "Cancel Search")]

    def compose(self):
        with Horizontal(classes="search-row"):
            yield Label("Search All Mailboxes:", classes="search-label")
            yield Switch(value=True, id="search-all-mbox")
        with Horizontal(classes="search-row"):
            yield Label("Search:", classes="search-label")
            yield Input(value="", id="search-criteria")
        with Horizontal(id="search-buttonbar"):
            yield Button("OK", id="search-ok")
            yield Button("Cancel", id="search-cancel")

    def on_button_pressed(self, event):
        if event.button.id == "search-ok":
            fields = {}
            all_mbox_switch = self.query_one("#search-all-mbox")
            criteria = self.query_one("#search-criteria").value
            fields["all_mbox"] = all_mbox_switch.value
            fields["criteria"] = criteria
            self.dismiss(fields)
        else:
            self.dismiss(None)


class SearchResultsScreen(ModalScreen):

    BINDINGS = [("escape", "back", "Back")]
    search_fields = None
    search_completed = False

    def compose(self):
        yield ListView(id="search-results")
        yield LoadingIndicator(id="search-loading")
        yield Footer()

    def on_screen_resume(self):
        if not self.search_completed:
            self.init_search()

    def init_search(self):
        if self.search_fields is None:
            logger.debug("No search criteria-- terminating search.")
            return
        try:
            lv = self.query_one("#search-results")
        except Exception:
            logger.debug("Could not get search results list view.  Terminating search.")
            return
        if lv is None:
            return
        lv.clear()
        lv.add_class("invisible")
        loading = self.query_one("#search-loading")
        loading.remove_class("invisible")
        self.fetch_search_results()

    def action_back(self):
        app = self.app
        wm = app.workers
        wm.cancel_node(self)
        app.pop_screen()

    @work(exclusive=True, group="fetch-search-results", thread=True)
    def fetch_search_results(self):
        """
        Fetch search results from IMAP server.
        """
        search_fields = self.search_fields
        results = []
        criteria = f'X-GM-RAW {quote_imap_string(search_fields["criteria"])}'
        config = self.app.config
        access_token = get_oauth2_access_token(config)
        with get_mailbox(config, access_token) as mailbox:
            if search_fields["all_mbox"]:
                mailbox.folder.set("[Gmail]/All Mail")
            else:
                mailbox.folder.set(self.app.label)
            start = datetime.datetime.now()
            for gmessage_id, gthread_id, glabels, msg in fetch_google_messages(
                mailbox, criteria=criteria, headers_only=False, batch_size=50, limit=50
            ):
                results.append((gmessage_id, glabels, msg))
            stop = datetime.datetime.now()
            td = stop - start
            logger.debug(f"Total seconds for IMAP query: {td.total_seconds()}")
        self.app.call_from_thread(self.display_search_results, results)

    def display_search_results(self, search_results):
        """
        Display search results.
        """
        lv = self.query_one("#search-results")
        for n, (gmessage_id, glabels, msg) in enumerate(search_results):
            date = msg.obj.get("Date")
            dt = parse_date(date)
            dt = dt.astimezone(tzlocal())
            date_str = dt.isoformat()
            sender = msg.from_
            subject = msg.subject
            unread = is_unread(msg.flags)
            starred = is_starred(msg.flags)
            message_item = MessageItem(
                gmessage_id,
                msg.uid,
                date_str,
                sender,
                subject,
                unread=unread,
                starred=starred,
                glabels=glabels,
            )
            if n % 2 == 0:
                message_item.add_class("item-even")
            else:
                message_item.add_class("item-odd")
            li = ListItem(message_item)
            lv.append(li)
        loading = self.query_one("#search-loading")
        loading.add_class("invisible")
        lv.remove_class("invisible")
        self.search_completed = True

    def on_list_view_selected(self, event):
        event.stop()
        lv = self.query_one("#search-results")
        item = event.item
        msgitem = item.children[0]
        uid = msgitem.uid
        gmessage_id = msgitem.gmessage_id
        glabels = msgitem.glabels
        loading = self.query_one("#search-loading")
        loading.remove_class("invisible")
        lv.add_class("invisible")
        self.fetch_message(gmessage_id, uid, glabels)

    @work(exclusive=True, group="fetch-message", thread=True)
    def fetch_message(self, gmessage_id, uid, glabels):
        """
        Fetch a specific message by UID.
        """
        search_fields = self.search_fields
        db_path = self.app.db_path
        logger.debug(f"DB path: {db_path}")
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            result = self.app.get_cached_message(cursor, gmessage_id)
            if result is None:
                logger.debug(f"Fetching message with ID {gmessage_id}.")
                config = self.app.config
                access_token = get_oauth2_access_token(config)
                with get_mailbox(config, access_token) as mailbox:
                    if search_fields["all_mbox"]:
                        mailbox.folder.set("[Gmail]/All Mail")
                    else:
                        mailbox.folder.set(self.app.label)
                    criteria = A(uid=[uid])
                    for gmessage_id, gthread_id, glabels, msg in fetch_google_messages(
                        mailbox, criteria=criteria, headers_only=False, limit=1
                    ):
                        result = (
                            gmessage_id,
                            gthread_id,
                            glabels,
                            msg_to_email_msg(msg.obj),
                        )
                        break
                logger.debug(f"Preparing to cache {gmessage_id}")
                self.cache_message(cursor, gmessage_id, gthread_id, glabels, msg)
                conn.commit()
            else:
                logger.debug(f"Using cached message: {gmessage_id}.")
                _, gthread_id, message_string, unread, starred = result
                msg = str_to_email_msg(message_string)
                result = (gmessage_id, gthread_id, glabels, msg)
        self.app.call_from_thread(self.display_message, *result)

    def cache_message(self, cursor, gmessage_id, gthread_id, glabels, msg):
        flags = msg.flags
        unread = is_unread(flags)
        starred = is_starred(flags)
        sql = """\
            INSERT INTO messages
                (gmessage_id, gthread_id, message_string, unread, starred)
                VALUES (?, ?, ?, ?, ?)
            """
        cursor.execute(
            sql,
            [gmessage_id, gthread_id, msg.obj.as_string(), unread, starred],
        )

    def display_message(self, gmessage_id, gthread_id, glabels, msg):
        loading = self.query_one("#search-loading")
        loading.add_class("invisible")
        lv = self.query_one("#search-results")
        lv.remove_class("invisible")
        screen = self.app.SCREENS["msg_screen"]
        screen.msg = msg
        self.app.push_screen(screen)
