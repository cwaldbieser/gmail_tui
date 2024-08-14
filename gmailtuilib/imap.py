import contextlib
from collections import OrderedDict
from itertools import islice

from imap_tools import MailBox
from imap_tools.consts import MailMessageFlags

from gmailtuilib.parsers import imap_gmail_uid_fetch_response_parser


@contextlib.contextmanager
def get_mailbox(config, access_token):
    """
    Returns an authenticated imap_tools.MailBox.
    """
    email = config["oauth2"]["email"]
    with MailBox("imap.gmail.com").xoauth2(email, access_token) as mailbox:
        yield mailbox


def batched(iterable, n):
    "Batch data into tuples of length n. The last batch may be shorter."
    # batched('ABCDEFG', 3) --> ABC DEF G
    if n < 1:
        raise ValueError("n must be at least one")
    it = iter(iterable)
    while batch := tuple(islice(it, n)):
        yield batch


def fetch_google_messages(
    mailbox, criteria="All", batch_size=100, headers_only=True, limit=None
):
    """
    Fetch messages in batches and decorate with Google IDs.
    Generator produces (gmessage_id, gthread_id, msg).
    """
    msg_generator = mailbox.fetch(
        criteria=criteria,
        reverse=True,
        headers_only=headers_only,
        mark_seen=False,
        bulk=batch_size,
        limit=limit,
    )
    for msg_batch in batched(msg_generator, batch_size):
        messages = OrderedDict()
        for msg in msg_batch:
            messages[msg.uid] = dict(msg=msg)
        uids = list(int(uid) for uid in messages.keys())
        max_uid = max(uids)
        min_uid = min(uids)
        client = mailbox.client
        response = client.uid(
            "fetch", f"{min_uid}:{max_uid}", "(X-GM-MSGID X-GM-THRID X-GM-LABELS)"
        )
        results = parse_fetch_google_ids_response(response)
        for fields in results:
            uid = fields["UID"]
            gmessage_id = fields["X-GM-MSGID"]
            gthread_id = fields["X-GM-THRID"]
            glabels = fields["X-GM-LABELS"]
            if glabels is None:
                glabels = []
            if gmessage_id is None or gthread_id is None:
                # Just skip a message if we can't get the Google IDs.
                continue
            msg_wrapper = messages.get(uid)
            if msg_wrapper:
                msg_wrapper["gmessage_id"] = gmessage_id
                msg_wrapper["gthread_id"] = gthread_id
                msg_wrapper["glabels"] = glabels
        for msg_wrapper in messages.values():
            msg = msg_wrapper["msg"]
            gmessage_id = msg_wrapper.get("gmessage_id")
            gthread_id = msg_wrapper.get("gthread_id")
            glabels = msg_wrapper.get("glabels", [])
            yield gmessage_id, gthread_id, glabels, msg


def parse_fetch_google_ids_response(response):
    """
    Parse fetch response for Google IDs.
    """
    status = response[0]
    if status != "OK":
        return []
    lines = response[1]
    for ascii_7bit_line in lines:
        line = ascii_7bit_line.decode()
        data = imap_gmail_uid_fetch_response_parser(line).line()
        msg_number, response_parts = data
        fields = {"MESSAGE_NUMBER": msg_number}
        names = ["X-GM-THRID", "X-GM-MSGID", "X-GM-LABELS", "UID"]
        for name in names:
            pos = response_parts.index(name)
            if pos == -1:
                value = None
            else:
                value = response_parts[pos]
            fields[name] = value
        yield fields


def is_unread(flags):
    return not (MailMessageFlags.SEEN in flags)


def is_starred(flags):
    return MailMessageFlags.FLAGGED in flags
