#! /usr/bin/env python

import pathlib

import tomllib
from dateutil.parser import parse as parse_date
from textual import work
from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer
from textual.message import Message
from textual.widgets import Button, Footer, Header, Static

from gmailtuilib.gmailapi import get_gmail_credentials, list_messages


class MessageItem(Static):
    def __init__(self, date_str, sender, subject):
        self.date_str = date_str
        self.sender = sender
        self.subject = subject
        super().__init__()

    def compose(self):
        yield Static(self.date_str)
        yield Static(self.sender)
        yield Static(self.subject)


class Messages(ScrollableContainer):

    class Mounted(Message):
        pass

    def on_mount(self):
        self.post_message(self.Mounted())


class ButtonBar(Static):
    def compose(self):
        yield Button("<", disabled=True, id="btn-backwards", classes="button")
        yield Button(">", disabled=True, id="btn-forwards", classes="button")


class MessageList(Static):
    def compose(self):
        yield Messages(id="messages")
        yield ButtonBar()


class MessagePane(ScrollableContainer):
    pass


class MainPanel(Static):
    def compose(self):
        yield MessageList()
        yield MessagePane()


class GMailApp(App):
    """A Textual app to manage stopwatches."""

    CSS_PATH = "gmail_app.tcss"
    BINDINGS = [("d", "toggle_dark", "Toggle dark mode")]

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
        messages, next_page_token = list_messages(credentials)
        print("Got messages.")
        messages_widget.remove_children()
        for item in messages:
            print(item)
            dt = parse_date(item["Date"])
            date_str = dt.isoformat()
            sender = item["From"]
            subject = item["Subject"]
            widget = MessageItem(date_str, sender, subject)
            self.call_from_thread(messages_widget.mount, widget)

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.dark = not self.dark


if __name__ == "__main__":
    app = GMailApp()
    app.run()
