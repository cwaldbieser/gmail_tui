#! /usr/bin/env python

import pathlib

import tomllib
from dateutil.parser import parse as parse_date
from textual import work
from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer
from textual.message import Message
from textual.widgets import Button, Footer, Header, Static, Label

from gmailtuilib.gmailapi import get_gmail_credentials, list_messages


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
    BINDINGS = [("d", "toggle_dark", "Toggle dark mode"),
                ("x", "test", "Debbugging")]

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        yield MainPanel()
        yield Footer()

    def on_messages_mounted(self, message):
        self.update_messages()

    async def on_mount(self):
        # self.update_messages()

        with open(pathlib.Path("~/.gmail_tui/conf.toml").expanduser(), "rb") as f:
            self.config = tomllib.load(f)

    @work(exclusive=True, thread=True)
    def update_messages(self):
        print("Updating messages ...")
        messages_widget = self.query_one("#messages")
        credentials = get_gmail_credentials(self.config)
        print("Got credentials.")
        message_threads = list_messages(credentials)
        print("Got messages.")
        messages_widget.remove_children()
        for n, (thread_id, threads) in enumerate(message_threads.items()):
            if n % 2 == 0:
                item_class = "item-even"
            else:
                item_class = "item-odd"
            item = threads[0]
            dt = parse_date(item["Date"])
            date_str = dt.isoformat()
            sender = item["From"]
            subject = item["Subject"]
            widget = MessageItem(thread_id, date_str, sender, subject, classes=item_class)
            self.message_thread_cache = message_threads
            self.call_from_thread(messages_widget.mount, widget)

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.dark = not self.dark

    def action_test(self):
        button = self.query_one("#btn-forwards")
        button.focus()

    def on_button_pressed(self, event: Button.Pressed):
        print(event)


if __name__ == "__main__":
    app = GMailApp()
    app.run()
