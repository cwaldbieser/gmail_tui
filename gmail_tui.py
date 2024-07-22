#! /usr/bin/env python

from textual.app import App, ComposeResult
from textual.widgets import Button, Footer, Header, Static


class Messages(Static):
    pass


class ButtonBar(Static):
    def compose(self):
        yield Button("<", disabled=True, id="btn-backwards", classes="button")
        yield Button(">", disabled=True, id="btn-forwards", classes="button")


class MessageList(Static):
    def compose(self):
        yield Messages("List of messages")
        yield ButtonBar()


class MessagePane(Static):
    pass


class MainPanel(Static):
    def compose(self):
        yield MessageList()
        yield MessagePane("Message text is here.")


class GMailApp(App):
    """A Textual app to manage stopwatches."""

    CSS_PATH = "gmail_app.tcss"
    BINDINGS = [("d", "toggle_dark", "Toggle dark mode")]

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        yield MainPanel()
        yield Footer()

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.dark = not self.dark


if __name__ == "__main__":
    app = GMailApp()
    app.run()
