<?php
/**
 * Application configuration.
 * Load from environment (e.g. getenv('GITHUB_PAT')) or .env file.
 */
if (file_exists(__DIR__ . '/../.env')) {
    $lines = file(__DIR__ . '/../.env', FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    foreach ($lines as $line) {
        if (strpos(trim($line), '#') === 0) continue;
        if (strpos($line, '=') !== false) {
            list($key, $val) = explode('=', $line, 2);
            $_ENV[trim($key)] = trim($val, " \t\"'");
        }
    }
}

define('GITHUB_PAT', getenv('GITHUB_PAT') ?: ($_ENV['GITHUB_PAT'] ?? ''));
define('TURNSTILE_SECRET_KEY', getenv('TURNSTILE_SECRET_KEY') ?: ($_ENV['TURNSTILE_SECRET_KEY'] ?? ''));
define('GITHUB_REPO', getenv('GITHUB_REPO') ?: 'DontPokeMe/known-link-shorteners');
