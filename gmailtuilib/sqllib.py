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
        message_id,
        thread_id,
        b64_message
    FROM (
        SELECT
            id,
            message_id,
            thread_id,
            b64_message,
            name,
            max_mid,
            DENSE_RANK()
            OVER
            (
                ORDER BY max_mid DESC
            ) thread_number
        FROM (
            SELECT
                messages.id,
                messages.message_id,
                messages.thread_id,
                messages.b64_message,
                labels.name,
                threads.max_mid
            FROM messages
                INNER JOIN (
                    SELECT
                        thread_id, MAX(message_id) max_mid
                    FROM messages
                    GROUP BY thread_id
                ) threads
                    ON threads.thread_id = messages.thread_id
                LEFT OUTER JOIN message_labels
                    ON message_labels.message_id = messages.id
                LEFT OUTER JOIN labels
                    ON message_labels.label_id = labels.id
            WHERE (labels.name IS NULL OR labels.name = ?)
        ) mtl
    ) x
    WHERE thread_number > ?
    ORDER BY max_mid DESC, message_id DESC
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
