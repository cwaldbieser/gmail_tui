from textual.screen import Screen
from textual.containers import Horizontal
from textual.widgets import Button, Input, Label, Switch


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
