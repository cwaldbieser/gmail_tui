import contextlib
from collections import OrderedDict
from io import StringIO
from itertools import islice

from imap_tools import MailBox
from imap_tools.consts import MailMessageFlags


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
            "fetch", f"{min_uid}:{max_uid}", "(X-GM-MSGID X-GM-THRID)"
        )
        results = parse_fetch_google_ids_response(response)
        for uid, (gmessage_id, gthread_id) in results.items():
            if gmessage_id is None or gthread_id is None:
                # Just skip a message if we can't get the Google IDs.
                continue
            msg_wrapper = messages.get(uid)
            if msg_wrapper:
                msg_wrapper["gmessage_id"] = gmessage_id
                msg_wrapper["gthread_id"] = gthread_id
        for msg_wrapper in messages.values():
            msg = msg_wrapper["msg"]
            gmessage_id = msg_wrapper.get("gmessage_id")
            gthread_id = msg_wrapper.get("gthread_id")
            yield gmessage_id, gthread_id, msg


def parse_fetch_google_ids_response(response):
    """
    Parse fetch response for Google IDs.
    """
    status = response[0]
    if status != "OK":
        return []
    pieces = response[1]
    buffer = StringIO()
    results = {}
    for piece in pieces:
        buffer.write(piece.decode())
        value = buffer.getvalue()
        if value.endswith(")"):
            uid, gmessage_id, gthread_id = parse_google_ids_item(value)
            results[uid] = (gmessage_id, gthread_id)
            buffer.seek(0)
            buffer.truncate()
    return results


def parse_google_ids_item(value):
    """
    Parse an individual fetch result for Google message and thread IDs.
    """
    parts = value.split(" ", 1)
    components = parts[1][1:-1].split()
    part_map = {}
    for n in range(1, len(components), 2):
        part_map[components[n - 1]] = components[n]
    return part_map["UID"], part_map.get("X-GM-MSGID"), part_map.get("X-GM-THRID")


def is_unread(flags):
    return not (MailMessageFlags.SEEN in flags)


def is_starred(flags):
    return MailMessageFlags.FLAGGED in flags
