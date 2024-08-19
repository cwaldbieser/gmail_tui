sql_get_message_string_by_uid_and_label = """\
    SELECT
        message_string
    FROM messages
        INNER JOIN message_labels
            ON messages.id = message_labels.message_id
        INNER JOIN labels
            ON message_labels.label_id = labels.id
    WHERE labels.label = ?
    AND message_labels.uid = ?
    """

sql_all_uids_for_label = """\
    SELECT
        message_labels.rowid,
        message_labels.uid
    FROM message_labels
        INNER JOIN labels
            ON message_labels.label_id = labels.id
    WHERE labels.label = ?
    """

sql_delete_message_label = """\
    DELETE FROM message_labels
    WHERE rowid = ?
    """

sql_get_message_labels_in_uid_range = """\
    SELECT
        rowid,
        uid
    FROM message_labels
    WHERE CAST(uid AS INTEGER) >= ?
    AND CAST(uid AS INTEGER) <= ?
    """

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
    INSERT INTO message_labels (message_id, label_id, uid)
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
        ),
        ?
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
            gmessage_id,
            gthread_id,
            message_string,
            unread,
            starred,
            uid,
            thread_number,
            thread_rank,
            ROW_NUMBER()
            OVER
            (
                ORDER BY uid DESC
            ) row_num
        FROM (
            SELECT
                *,
                ROW_NUMBER()
                OVER
                (
                    PARTITION BY thread_number
                    ORDER BY gmessage_id DESC
                ) thread_rank
            FROM (
                SELECT
                    gmessage_id,
                    gthread_id,
                    message_string,
                    unread,
                    starred,
                    uid,
                    DENSE_RANK()
                    OVER (
                        ORDER BY gthread_id DESC
                    ) thread_number
                FROM messages
                    INNER JOIN message_labels
                        ON message_labels.message_id = messages.id
                    INNER JOIN labels
                        ON message_labels.label_id = labels.id
                WHERE labels.label = ?
            ) in1_table
        ) outer_table
        WHERE thread_rank = 1
    ) final
    WHERE row_num > ?
    AND row_num < 500
    ORDER BY uid DESC
    """

sql_message_exists = """\
    SELECT
        gmessage_id,
        gthread_id,
        message_string,
        unread,
        starred
    FROM messages
    WHERE gmessage_id = ?
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
       uid INTEGER,
       PRIMARY KEY (message_id, label_id)
    )
    """
