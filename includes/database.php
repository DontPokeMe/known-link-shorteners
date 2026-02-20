<?php
/**
 * Database connection for logging submissions.
 * Stub: replace with real PDO/MySQL when deploying.
 */
$db = null;

if (getenv('DB_DSN') || !empty($_ENV['DB_DSN'])) {
    $dsn = getenv('DB_DSN') ?: $_ENV['DB_DSN'];
    try {
        $db = new PDO($dsn, getenv('DB_USER') ?: $_ENV['DB_USER'] ?? '', getenv('DB_PASS') ?: $_ENV['DB_PASS'] ?? '');
        $db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    } catch (PDOException $e) {
        error_log('Database connection failed: ' . $e->getMessage());
    }
}
