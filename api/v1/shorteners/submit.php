<?php
require_once __DIR__ . '/../../../includes/config.php';
require_once __DIR__ . '/../../../includes/database.php';
require_once __DIR__ . '/../../../includes/auth.php';
require_once __DIR__ . '/../../../includes/rate-limit.php';

header('Content-Type: application/json');

// Authenticate user
$user = authenticate_request();

// Rate limit: 5 submissions per hour per user
$rate_key = "shortener_submit:{$user['id']}";
$rate_check = check_rate_limit($rate_key, 5, 3600);

if (!$rate_check['allowed']) {
    http_response_code(429);
    echo json_encode([
        'error' => 'Rate limit exceeded',
        'reset_at' => $rate_check['reset_at']
    ]);
    exit;
}

// Get input
$input = json_decode(file_get_contents('php://input'), true);
if (!is_array($input)) {
    http_response_code(400);
    echo json_encode(['error' => 'Invalid JSON body']);
    exit;
}

// Validate required fields
$required = ['domain', 'type', 'evidence'];
foreach ($required as $field) {
    if (empty($input[$field])) {
        http_response_code(400);
        echo json_encode(['error' => "Missing required field: {$field}"]);
        exit;
    }
}

// Validate domain format
$domain = strtolower(trim($input['domain']));
$domain = idn_to_ascii($domain, IDNA_DEFAULT, INTL_IDNA_VARIANT_UTS46);

if (!preg_match('/^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/', $domain)) {
    http_response_code(400);
    echo json_encode(['error' => 'Invalid domain format']);
    exit;
}

// Check for IP addresses
if (filter_var($domain, FILTER_VALIDATE_IP)) {
    http_response_code(400);
    echo json_encode(['error' => 'IP addresses not allowed']);
    exit;
}

// Validate type
$type = $input['type'];
if (!in_array($type, ['shortener', 'redirector', 'tracking'])) {
    http_response_code(400);
    echo json_encode(['error' => 'Invalid type. Must be: shortener, redirector, or tracking']);
    exit;
}

// Validate evidence (single URL from API)
$evidence = is_array($input['evidence']) ? $input['evidence'][0] : $input['evidence'];
if (!filter_var($evidence, FILTER_VALIDATE_URL)) {
    http_response_code(400);
    echo json_encode(['error' => 'Invalid evidence URL']);
    exit;
}

// Optional notes
$notes = $input['notes'] ?? '';
if (strlen($notes) > 500) {
    http_response_code(400);
    echo json_encode(['error' => 'Notes too long (max 500 characters)']);
    exit;
}

// Verify Cloudflare Turnstile
$turnstile_token = $input['cf_turnstile_token'] ?? '';
if (!verify_turnstile($turnstile_token)) {
    http_response_code(400);
    echo json_encode(['error' => 'CAPTCHA verification failed']);
    exit;
}

// Check if domain already exists (fetch latest from GitHub)
$existing = check_domain_exists($domain);
if ($existing) {
    http_response_code(409);
    echo json_encode([
        'error' => 'Domain already exists',
        'existing_type' => $existing['type'],
        'existing_status' => $existing['status']
    ]);
    exit;
}

// Create GitHub Issue
try {
    $issue_url = create_github_issue($domain, $type, $evidence, $notes, $user);

    if ($db) {
        log_submission($user['id'], $domain, $type, $issue_url);
    }

    http_response_code(201);
    echo json_encode([
        'success' => true,
        'message' => 'Submission received and under review',
        'issue_url' => $issue_url,
        'review_time' => 'Usually reviewed within 48 hours'
    ]);
} catch (Exception $e) {
    error_log("GitHub issue creation failed: " . $e->getMessage());
    http_response_code(500);
    echo json_encode(['error' => 'Failed to create submission. Please try again.']);
}

function check_domain_exists($domain)
{
    $files = ['shorteners.json', 'redirectors.json', 'tracking.json'];

    foreach ($files as $file) {
        $url = "https://raw.githubusercontent.com/" . GITHUB_REPO . "/main/data/{$file}";
        $data = @file_get_contents($url);

        if ($data) {
            $entries = json_decode($data, true);
            if (is_array($entries)) {
                foreach ($entries as $entry) {
                    if (isset($entry['domain']) && $entry['domain'] === $domain) {
                        return $entry;
                    }
                }
            }
        }
    }

    return false;
}

function create_github_issue($domain, $type, $evidence, $notes, $user)
{
    $github_token = GITHUB_PAT;
    $repo = GITHUB_REPO;

    $title = "[Submission] {$domain}";
    $date = date('Y-m-d H:i:s');
    $body = "## Submission Details\n\n**Domain**: `{$domain}`\n**Type**: {$type}\n**Evidence**: {$evidence}\n**Notes**: " . ($notes ?: '(none)') . "\n\n---\n\n**Submitted by**: {$user['email']}\n**Date**: {$date}\n\n---\n\n*This issue was automatically created via dontpoke.me submission form.*";

    $data = [
        'title' => $title,
        'body' => $body,
        'labels' => ['shortener-submission', $type]
    ];

    $ch = curl_init("https://api.github.com/repos/{$repo}/issues");
    curl_setopt_array($ch, [
        CURLOPT_HTTPHEADER => [
            'Authorization: Bearer ' . $github_token,
            'User-Agent: dontpoke-me',
            'Accept: application/vnd.github+json',
            'Content-Type: application/json'
        ],
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => json_encode($data),
        CURLOPT_RETURNTRANSFER => true,
    ]);

    $response = curl_exec($ch);
    $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if ($http_code !== 201) {
        $msg = json_decode($response, true)['message'] ?? $response;
        throw new Exception("GitHub API returned {$http_code}: {$msg}");
    }

    $issue = json_decode($response, true);
    return $issue['html_url'];
}

function verify_turnstile($token)
{
    if (!TURNSTILE_SECRET_KEY) {
        return true; // Skip in development when key not set
    }

    $ch = curl_init('https://challenges.cloudflare.com/turnstile/v0/siteverify');
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => http_build_query([
            'secret' => TURNSTILE_SECRET_KEY,
            'response' => $token
        ]),
        CURLOPT_RETURNTRANSFER => true,
    ]);

    $response = curl_exec($ch);
    curl_close($ch);

    $result = json_decode($response, true);
    return $result['success'] ?? false;
}

function generate_uuid()
{
    $data = random_bytes(16);
    $data[6] = chr(ord($data[6]) & 0x0f | 0x40);
    $data[8] = chr(ord($data[8]) & 0x3f | 0x80);
    return vsprintf('%s%s-%s-%s-%s-%s%s%s', str_split(bin2hex($data), 4));
}

function log_submission($user_id, $domain, $type, $issue_url)
{
    global $db;
    if (!$db) return;

    $stmt = $db->prepare("
        INSERT INTO shortener_submissions (id, user_id, domain, type, issue_url, created_at)
        VALUES (?, ?, ?, ?, ?, NOW())
    ");
    $stmt->execute([generate_uuid(), $user_id, $domain, $type, $issue_url]);
}
