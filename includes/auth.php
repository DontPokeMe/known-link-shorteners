<?php
/**
 * Request authentication.
 * Stub: replace with real session/JWT validation when deploying.
 * Must return an array with at least 'id' and 'email' keys.
 */
function authenticate_request() {
    $auth_header = $_SERVER['HTTP_AUTHORIZATION'] ?? '';
    if (preg_match('/Bearer\s+(.+)/i', $auth_header, $m)) {
        $token = trim($m[1]);
        // TODO: validate JWT and return user
        return ['id' => 'anon', 'email' => 'anonymous@dontpoke.me'];
    }
    // Allow unauthenticated for development; in production return 401
    if (getenv('ALLOW_ANON_SUBMIT') === '1') {
        return ['id' => 'anon', 'email' => 'anonymous@dontpoke.me'];
    }
    http_response_code(401);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'Authentication required']);
    exit;
}
