sql_find_ml = """\
    SELECT message_id, label_id
    FROM message_labels
    WHERE message_id = (
        SELECT id
        FROM messages
        WHERE message_id = ?
    )
    AND label_id = (
        SELECT id
        FROM labels
        WHERE name = ?
    )
    """

sql_insert_ml = """\
    INSERT INTO message_labels (message_id, label_id)
    VALUES (
        (
        SELECT id
        FROM messages
        WHERE message_id = ?
        ),
        (
        SELECT id
        FROM labels
        WHERE name = ?
        )
    )
    """

sql_fetch_msgs_for_label = """\
    SELECT
        messages.message_id,
        messages.thread_id,
        messages.b64_message
    FROM messages
        INNER JOIN
        (
            SELECT
                thread_id,
                ROW_NUMBER()
                OVER
                (
                    ORDER BY thread_id DESC
                ) thread_num
            FROM (
                SELECT DISTINCT thread_id
                FROM messages
            ) threads
        ) threads2
            ON messages.thread_id = threads2.thread_id
        INNER JOIN message_labels
            ON messages.id = message_labels.message_id
        INNER JOIN labels
            ON message_labels.label_id = labels.id
    WHERE labels.name = ?
    AND threads2.thread_num > ?
    ORDER BY threads2.thread_num, messages.message_id DESC
    """

sql_ddl_messages = """\
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY,
        message_id TEXT,
        thread_id TEXT,
        b64_message TEXT
    )
    """
sql_ddl_messages_idx0 = """\
    create unique index if not exists idx0_messages
        on messages (message_id)
    """
sql_ddl_labels = """\
    CREATE TABLE IF NOT EXISTS labels (
        id INTEGER PRIMARY KEY,
        label_id TEXT,
        name TEXT,
        is_system INTEGER,
        synced INTEGER
    )
    """
sql_ddl_labels_idx0 = """\
    create unique index if not exists idx0_labels
        on labels (label_id)
    """
sql_ddl_message_labels = """\
    CREATE TABLE IF NOT EXISTS message_labels (
       message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
       label_id INTEGER REFERENCES labels(id) ON DELETE CASCADE,
       PRIMARY KEY (message_id, label_id)
    )
    """
