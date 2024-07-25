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
