-- Optional: table for logging submissions (used by api/v1/shorteners/submit.php when DB_DSN is set)
-- Run this on your database if you enable submission logging.

CREATE TABLE IF NOT EXISTS shortener_submissions (
    id          VARCHAR(36)  PRIMARY KEY,
    user_id     VARCHAR(255) NOT NULL,
    domain      VARCHAR(253) NOT NULL,
    type        VARCHAR(20)  NOT NULL,
    issue_url   VARCHAR(500) NOT NULL,
    created_at  DATETIME     NOT NULL,
    INDEX (user_id),
    INDEX (domain),
    INDEX (created_at)
);
