sql_find_ml = """\
    SELECT message_id, label_id
    FROM message_labels
    WHERE message_id = (
        SELECT id
        FROM messages
        WHERE gmessage_id = ?
    )
    AND label_id = (
        SELECT id
        FROM labels
        WHERE label = ?
    )
    """

sql_insert_ml = """\
    INSERT INTO message_labels (message_id, label_id)
    VALUES (
        (
        SELECT id
        FROM messages
        WHERE gmessage_id = ?
        ),
        (
        SELECT id
        FROM labels
        WHERE label = ?
        )
    )
    """

sql_fetch_msgs_for_label = """\
    SELECT
        gmessage_id,
        gthread_id,
        message_string,
        unread,
        starred,
        uid
    FROM (
        SELECT
            id,
            gmessage_id,
            gthread_id,
            message_string,
            unread,
            starred,
            label,
            uid,
            max_mid,
            DENSE_RANK()
            OVER
            (
                ORDER BY max_mid DESC
            ) thread_number
        FROM (
            SELECT
                messages.id,
                messages.gmessage_id,
                messages.gthread_id,
                messages.message_string,
                messages.unread,
                messages.starred,
                labels.label,
                CASE
                    WHEN labels.label IS NOT NULL THEN message_labels.uid
                    ELSE NULL
                END uid,
                threads.max_mid
            FROM messages
                INNER JOIN (
                    SELECT
                        gthread_id, MAX(gmessage_id) max_mid
                    FROM messages
                    GROUP BY gthread_id
                ) threads
                    ON threads.gthread_id = messages.gthread_id
                LEFT OUTER JOIN message_labels
                    ON message_labels.message_id = messages.id
                LEFT OUTER JOIN labels
                    ON message_labels.label_id = labels.id
            WHERE (labels.label IS NULL OR labels.label = ?)
        ) mtl
    ) x
    WHERE thread_number > ?
    ORDER BY max_mid DESC, gmessage_id DESC
    """

sql_ddl_messages = """\
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY,
        gmessage_id TEXT,
        gthread_id TEXT,
        message_string TEXT,
        unread INT,
        starred INT
    )
    """
sql_ddl_messages_idx0 = """\
    create unique index if not exists idx0_messages
        on messages (gmessage_id)
    """
sql_ddl_labels = """\
    CREATE TABLE IF NOT EXISTS labels (
        id INTEGER PRIMARY KEY,
        label TEXT
    )
    """
sql_ddl_labels_idx0 = """\
    create unique index if not exists idx0_labels
        on labels (label)
    """
sql_ddl_message_labels = """\
    CREATE TABLE IF NOT EXISTS message_labels (
       message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
       label_id INTEGER REFERENCES labels(id) ON DELETE CASCADE,
       uid TEXT,
       PRIMARY KEY (message_id, label_id)
    )
    """
