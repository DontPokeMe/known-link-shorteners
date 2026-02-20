<?php
/**
 * Simple rate limiting (in-memory or Redis).
 * Returns ['allowed' => bool, 'reset_at' => ISO8601].
 * Stub: replace with Redis when deploying for multi-instance.
 */
function check_rate_limit($key, $max_requests, $window_seconds) {
    $reset_at = date('c', time() + $window_seconds);
    // In-memory stub: always allow 5 per hour for development
    $storage = sys_get_temp_dir() . '/rate_limit_' . md5($key);
    $now = time();
    $data = [];
    if (file_exists($storage)) {
        $data = json_decode(file_get_contents($storage), true) ?: [];
        $data = array_filter($data, function ($t) use ($now, $window_seconds) {
            return $t > $now - $window_seconds;
        });
    }
    $allowed = count($data) < $max_requests;
    if ($allowed) {
        $data[] = $now;
        file_put_contents($storage, json_encode(array_values($data)));
    }
    return [
        'allowed' => $allowed,
        'reset_at' => $reset_at,
    ];
}
