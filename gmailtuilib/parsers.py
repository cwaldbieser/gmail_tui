#! /usr/bin/env python
import csv

import parsley

imap_gmail_uid_fetch_response_grammar = """\
line = msg_number:m ws plist:x end -> (m, x)
msg_number = digit+:dl -> int("".join(dl))
plist = '(' items:x ')' -> x
items = item_space*:x -> x
item_space = item:x ws -> x
item = string_item | plist
string_item = atom | qstring
atom = (letterOrDigit | punctuation)+:c -> ''.join(c)
punctuation = anything:c ?(c in '!#$%&*+,-./:;<=>?@[]^_`{|}~') -> c
qstring = '"' qstring_contents:a '"' -> a
qstring_contents = qstring_chars+:c -> ''.join(c)
qstring_chars = anything:c ?(c not in '"') -> c
"""
imap_gmail_uid_fetch_response_parser = parsley.makeGrammar(
    imap_gmail_uid_fetch_response_grammar, {}
)


def parse_maybe_quoted_csv(s):
    """
    Parses a string that represents comma-delimited items.
    The items may or may not be quoted.
    """
    r = csv.reader(iter([s]))
    items = list(r)[0]
    return items
