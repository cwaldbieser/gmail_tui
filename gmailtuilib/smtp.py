import base64
from contextlib import contextmanager
import smtplib


def generate_oauth2_string(user, access_token):
    auth_string = f"user={user}\1auth=Bearer {access_token}\1\1"
    return base64.b64encode(auth_string.encode("utf-8")).decode("ascii")


@contextmanager
def gmail_smtp(user, access_token):
    """
    Get an authenticated SMTP client for GMail.
    """
    xoauth_string = generate_oauth2_string(user, access_token)
    with smtplib.SMTP('smtp.gmail.com', 587) as conn:
        conn.starttls()
        conn.docmd('AUTH', 'XOAUTH2 ' + xoauth_string)
        yield conn
        conn.quit()
