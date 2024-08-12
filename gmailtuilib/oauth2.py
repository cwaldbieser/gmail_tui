import json
import pathlib
import sys
from dateutil.parser import parse as parse_date
import datetime
from dateutil.tz import tzlocal
import requests

# The URL root for accessing Google Accounts.
GOOGLE_ACCOUNTS_BASE_URL = "https://accounts.google.com"


def accounts_url(command):
    """
    Generate Google Accounts URL.
    """
    return f"{GOOGLE_ACCOUNTS_BASE_URL}/{command}"


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


def get_oauth2_access_token(config):
    """
    Get a valid OAuth2 access token to be used with IMAP.
    """
    oauth2_config = config.get("oauth2", {})
    expired = True
    client_id, client_secret = get_client_config(oauth2_config)
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
