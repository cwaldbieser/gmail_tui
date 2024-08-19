import os
import pathlib
import subprocess
import tempfile
from email.mime.text import MIMEText
from email.policy import default as default_policy

import html2text
import logzero
from logzero import logger
from textual.containers import (Horizontal, HorizontalScroll,
                                ScrollableContainer)
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from gmailtuilib.oauth2 import get_oauth2_access_token
from gmailtuilib.smtp import gmail_smtp


class MessageItem(Static):
    starred = reactive(False)
    unread = reactive(False)

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
        super().__init__(**kwds)
        self.message_id = message_id
        self.uid = uid
        self.date_str = date_str
        self.sender = sender
        self.subject = " ".join(subject.split())
        self.starred = starred
        self.unread = unread

    def compose(self):
        status_line = self.compose_statusline()
        yield Label(status_line)
        yield Label(f"GMSGID:  {self.message_id}", classes="diagnostic")
        yield Label(f"UID:     {self.uid}", classes="diagnostic")
        yield Label(f"Date:    {self.date_str}")
        yield Label(f"From:    {self.sender}")
        yield Label(f"Subject: {self.subject}", classes="subject")

    def allow_focus(self):
        return True

    def watch_starred(self, value):
        self.update_statusline()

    def watch_unread(self, value):
        self.update_statusline()
        if self.parent is None:
            return
        if value:
            self.parent.add_class("unread")
        else:
            self.parent.remove_class("unread")

    def update_statusline(self):
        children = self.children
        if len(children) == 0:
            return
        statusline = self.compose_statusline()
        label = children[0]
        label.update(statusline)

    def compose_statusline(self):
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
        return status_line


class HeadersScreen(Screen):

    BINDINGS = [("escape", "app.pop_screen", "Pop screen")]

    def compose(self):
        with Horizontal(classes="headers-row"):
            yield Label("To:", classes="headers-label")
            yield Input(value="", id="headers-to")
        with Horizontal(classes="headers-row"):
            yield Label("Subject:", classes="headers-label")
            yield Input(value="", id="headers-subject")
        with Horizontal(id="headers-buttonbar"):
            yield Button("OK", id="headers-ok")
            yield Button("Cancel", id="headers-cancel")

    def set_fields(self, recipients="", subject=""):
        try:
            self.query_one("#headers-to").value = recipients
            self.query_one("#headers-subject").value = subject
        except Exception as ex:
            logger.exception(ex)

    def on_button_pressed(self, event):
        if event.button.id == "headers-ok":
            headers = {}
            to_input = self.query_one("#headers-to")
            recipients = [recipient.strip() for recipient in to_input.value.split(",")]
            headers["To"] = recipients
            subject = self.query_one("#headers-subject").value
            headers["Subject"] = subject
            self.dismiss(headers)
        else:
            self.dismiss(None)


class AttachmentButton(Button):
    binary_data = None
    fname = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fname = kwargs.get("label")

    def on_button_pressed(self):
        full_path = (
            pathlib.Path("~/Downloads")
            .expanduser()
            .joinpath(pathlib.Path(self.fname).name)
        )
        with open(full_path, "wb") as f:
            f.write(self.binary_data)
        logger.debug(f"Saved attachment to {full_path}.")


class MessageScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Pop screen"),
        ("r", "reply", "Reply to message."),
    ]

    msg = reactive(None, init=False, recompose=True)
    text = reactive("No text.")

    def compose(self):
        yield Header()
        yield ScrollableContainer(Static(self.text, id="msg-text"))
        attachments = get_attachments(self.msg)
        buttons = create_attachment_buttons(attachments)
        if len(buttons) > 0:
            yield HorizontalScroll(*buttons, id="attachments")
        yield Footer()

    def watch_msg(self, msg):
        logger.debug("Entered watch_msg().")
        if msg is None:
            logger.debug("msg is None.  Exiting function.")
            return
        text = get_text_from_message(msg, "text/plain")
        if text is None:
            logger.debug("No message text with content-type text/plain.")
            text = get_text_from_message(msg, "text/html")
            if text is None:
                logger.debug("No message text with content-type text/html.")
                text = "No text."
            else:
                logger.debug("Got HTML text.")
                text = html2text.html2text(text)
        text = text.lstrip()
        self.text = text

    def action_reply(self):
        screen = self.app.SCREENS["headers_screen"]
        orig_sender = self.msg["From"]
        orig_subject = self.msg["Subject"]
        if not orig_subject.startswith("Re:"):
            subject = f"Re: {orig_subject}"
        else:
            subject = orig_subject
        screen.set_fields(subject=subject, recipients=orig_sender)

        def compose_message(headers):
            if headers is None:
                return
            EDITOR = os.environ.get("EDITOR", "vim")
            logger.debug(f"EDITOR is: {EDITOR}")
            reply_text = "\n".join(f">{line}" for line in self.text.split("\n"))
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
                tfname = tf.name
                tf.write(reply_text)
            try:
                with self.app.suspend():
                    logzero.loglevel(logzero.CRITICAL)
                    subprocess.call([EDITOR, tfname])
                logzero.loglevel(logzero.DEBUG)
                with open(tfname, "r") as tf:
                    text = tf.read()
            finally:
                os.unlink(tfname)
            access_token = get_oauth2_access_token(self.app.config)
            user = self.app.config["oauth2"]["email"]
            message = MIMEText(text, policy=default_policy)
            message["From"] = user
            recipients = headers["To"]
            message["To"] = recipients
            message["Subject"] = headers["Subject"]
            with gmail_smtp(user, access_token) as smtp:
                smtp.sendmail(user, recipients, message.as_string())

        self.app.push_screen(screen, compose_message)


def get_text_from_message(msg, content_type="text/plain"):
    """
    Extract text from email message.
    """
    for part in msg.walk():
        part_content_type = part.get_content_type()
        logger.debug(f"Part content-type: {part_content_type}")
        if part_content_type == content_type:
            transfer_encoding = part.get("content-transfer-encoding")
            decode = transfer_encoding is not None
            payload = part.get_payload(decode=decode)
            if isinstance(payload, bytes):
                payload = payload.decode()
            text = payload
            return text
    return None


def get_attachments(msg):
    """
    Extract attachments from an email message.
    Return a list of (name, binary_data)
    """
    attachments = []
    if msg is None:
        return attachments
    for attachment in msg.iter_attachments():
        fname = attachment.get_filename()
        data = attachment.get_payload(decode=True)
        attachments.append((fname, data))
    return attachments


def create_attachment_buttons(attachments):
    """
    Returns a list of attachment buttons.
    """
    buttons = []
    for fname, data in attachments:
        button = AttachmentButton(label=fname)
        button.binary_data = data
        buttons.append(button)
    return buttons
