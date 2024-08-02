import contextlib
import datetime
import json
import pathlib
import sys
from collections import OrderedDict
from io import StringIO
from itertools import islice

import requests
from dateutil.parser import parse as parse_date
from dateutil.tz import tzlocal
from imap_tools import MailBox
from imap_tools.consts import MailMessageFlags

# The URL root for accessing Google Accounts.
GOOGLE_ACCOUNTS_BASE_URL = "https://accounts.google.com"
# Hardcoded redirect URI.
REDIRECT_URI = "https://oauth2.dance/"


def get_imap_access_token(config):
    """
    Get a valid OAuth2 access token to be used with IMAP.
    """
    imap_config = config.get("imap", {})
    expired = True
    client_id, client_secret = get_client_config(imap_config)
    token_path = pathlib.Path("~/.gmail_tui/access-tokens.json").expanduser()
    if token_path.exists():
        with open(token_path, "r") as f:
            tokens = json.load(f)
        expires_at = tokens["expires_at"]
        dt_expires = parse_date(expires_at)
        dt = datetime.datetime.today().replace(tzinfo=tzlocal())
        if dt < dt_expires:
            print("Access token is still valid.", file=sys.stderr)
            expired = False
        else:
            print("Refreshing tokens ...", file=sys.stderr)
            new_tokens = refresh_tokens(
                client_id, client_secret, tokens["refresh_token"]
            )
            tokens.update(new_tokens)
            print(tokens)
            print("Tokens refreshed.", file=sys.stderr)
            expired = False
    if expired:
        raise Exception("Could not obtain valid access token.")
    refresh_token = tokens["refresh_token"]
    access_token = tokens["access_token"]
    expires_in = tokens["expires_in"]
    issued_at = tokens["issued_at"]
    dt = parse_date(issued_at)
    expires_at = dt + datetime.timedelta(seconds=expires_in)
    print(f"Refresh Token: {refresh_token}")
    print(f"Access Token: {access_token}")
    print(f"Access Token issued at: {issued_at}")
    print(f"Access Token Expiration Seconds: {expires_in}")
    print(f"Access token expires at: {expires_at.isoformat()}")
    tokens["expires_at"] = expires_at.isoformat()
    with open(token_path, "w") as f:
        json.dump(tokens, f, indent=4)
    return access_token


def get_client_config(imap_config):
    """
    Get Oauth2 Client ID and Client Secret.
    """
    default_credentials_file = "~/.gmail_tui/gmail-imap-client-secret.json"
    credentials_file = imap_config.get("credentials_file", default_credentials_file)
    credentials_file = pathlib.Path(credentials_file).expanduser()
    with open(credentials_file) as f:
        o = json.load(f)
    web = o["web"]
    client_id = web["client_id"]
    client_secret = web["client_secret"]
    return client_id, client_secret


def refresh_tokens(client_id, client_secret, refresh_token):
    params = {}
    params["client_id"] = client_id
    params["client_secret"] = client_secret
    params["refresh_token"] = refresh_token
    params["grant_type"] = "refresh_token"
    request_url = accounts_url("o/oauth2/token")
    response = requests.post(request_url, data=params)
    tokens = response.json()
    issued_at = datetime.datetime.today().replace(tzinfo=tzlocal())
    tokens["issued_at"] = issued_at.isoformat()
    return tokens


@contextlib.contextmanager
def get_mailbox(config, access_token):
    """
    Returns an authenticated imap_tools.MailBox.
    """
    email = config["imap"]["email"]
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


def accounts_url(command):
    """
    Generate Google Accounts URL.
    """
    return f"{GOOGLE_ACCOUNTS_BASE_URL}/{command}"
