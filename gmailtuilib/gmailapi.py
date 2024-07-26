import pathlib

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


def get_gmail_credentials(config):
    """
    Load GMail API credentials.
    """
    api_config = config.get("api", {})
    cred_file = pathlib.Path(
        api_config.get("credentials_file", "~/.gmail_tui/credentials.json")
    ).expanduser()

    subject = api_config["subject"]
    email = api_config["email"]
    creds = service_account.Credentials.from_service_account_file(
        cred_file,
        scopes=SCOPES,
        subject=subject,
    )
    creds_delegated = creds.with_subject(email)
    return creds_delegated


def get_gmail_labels(credentials):
    """
    Generator produces (label ID, label name, label type).
    """
    service = build("gmail", "v1", credentials=credentials)
    results = page_results(
        service.users().labels().list,
        items_key="labels",
        userId="me",
    )
    for item in results:
        label_id = item["id"]
        label_name = item["name"]
        label_type = item["type"]
        yield label_id, label_name, label_type


def list_gmail_messages(config, credentials, query):
    """
    Generator yields message_id, thread_id tuples for messages that match
    `query`.
    """
    api_config = config.get("api", {})
    max_messages_per_query = api_config.get("max_messages_per_query", 500)
    service = build("gmail", "v1", credentials=credentials)
    results = page_results(
        service.users().messages().list, items_key="messages", userId="me", q=query
    )
    for n, message in enumerate(results):
        message_id = message["id"]
        thread_id = message["threadId"]
        yield message_id, thread_id
        if n + 1 >= max_messages_per_query:
            break


def get_gmail_message(credentials, message_id):
    """
    Get a GMail message.
    """
    service = build("gmail", "v1", credentials=credentials)
    result = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="raw")
        .execute()
    )
    return result


def page_results(func, items_key=None, **kwargs):
    """
    Generator pages Google API results.
    """
    page_token = None
    while True:
        results = func(**kwargs).execute()
        if items_key is None:
            kind = results["kind"]
            items_key = kind.split("#")[-1]
        try:
            items = results[items_key]
        except KeyError:
            items = []
        for item in items:
            yield item
        page_token = results.get("nextPageToken")
        if not page_token:
            break
        kwargs["pageToken"] = page_token
