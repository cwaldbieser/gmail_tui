from dateutil.parser import parse as parse_date
from dateutil.tz import tzlocal
from logzero import logger
from textual import work
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import (Button, Input, Label, ListItem, ListView,
                             LoadingIndicator, Switch)

from gmailtuilib.imap import (fetch_google_messages, get_mailbox, is_starred,
                              is_unread)
from gmailtuilib.message import MessageItem
from gmailtuilib.oauth2 import get_oauth2_access_token


class SearchScreen(Screen):

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


class SearchResultsScreen(Screen):

    BINDINGS = [("escape", "back", "Back")]
    search_fields = None

    def compose(self):
        yield ListView(id="search-results")
        yield LoadingIndicator(id="search-loading")

    def init_search(self, search_fields):
        self.search_fields = search_fields

    def on_mount(self):
        if self.search_fields is None:
            return
        logger.debug("Initializing search results ...")
        lv = self.query_one("#search-results")
        if lv is None:
            return
        lv.clear()
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
        criteria = f'X-GM-RAW "{search_fields["criteria"]}"'
        config = self.app.config
        access_token = get_oauth2_access_token(config)
        with get_mailbox(config, access_token) as mailbox:
            if search_fields["all_mbox"]:
                mailbox.folder.set("[Gmail]/All Mail")
            for gmessage_id, gthread_id, glabels, msg in fetch_google_messages(
                mailbox, criteria=criteria, headers_only=False, limit=50
            ):
                results.append((gmessage_id, glabels, msg))
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
            )
            if n % 2 == 0:
                message_item.add_class("item-even")
            else:
                message_item.add_class("item-odd")
            li = ListItem(message_item)
            lv.append(li)
        loading = self.query_one("#search-loading")
        loading.add_class("invisible")
