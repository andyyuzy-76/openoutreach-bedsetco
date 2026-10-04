#Requires -Version 7.0
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$dataDirectory = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\data'))
$pendingPath = Join-Path $dataDirectory 'sender-migration-pending.json'
$reportPath = Join-Path $dataDirectory 'sales-sender-verification.json'
$auditScript = Join-Path $PSScriptRoot 'importyeti-mail-audit.ps1'
$sendScript = Join-Path $PSScriptRoot 'importyeti-mail-send.ps1'
$expectedHash = 'c9c3d03ab575f21e06ad79e284206aa3a251dcb54b245f17f303c7df99600fe3'

function Get-MailHealth {
    $health = (& $auditScript -Operation health) | ConvertFrom-Json -Depth 30
    if (-not $health.services_healthy -or -not $health.queue.empty -or
        $health.mailer_wrapper.sha256 -ne $expectedHash) {
        throw 'Server has not been restored to the verified sales wrapper with healthy services and an empty queue.'
    }
    $health
}

function Save-VerificationReport {
    param([Parameter(Mandatory)]$Report)
    $temporary = $reportPath + '.tmp'
    [System.IO.File]::WriteAllText($temporary, ($Report | ConvertTo-Json -Depth 30), [System.Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $reportPath -Force
}

$migrationMutex = [System.Threading.Mutex]::new($false, 'Local\BedSetCoSalesSenderMigration')
$mutexAcquired = $false
try {
    $mutexAcquired = $migrationMutex.WaitOne(0)
    if (-not $mutexAcquired) { throw 'Sender migration verification is already running.' }
    if (-not (Test-Path -LiteralPath $pendingPath -PathType Leaf)) {
        throw 'No pending sender migration. No test message was submitted.'
    }
    $pending = Get-Content -LiteralPath $pendingPath -Raw | ConvertFrom-Json
    if ($pending.requested_sender -ne 'sales@bedsetco.com') { throw 'Unexpected pending sender.' }
    $health = Get-MailHealth
    if (Test-Path -LiteralPath $reportPath -PathType Leaf) {
        # Resume read-only verification of the original Message-ID; never retry a submission.
        $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json -AsHashtable
        if ($report.sender -ne 'sales@bedsetco.com' -or $report.recipient -ne 'postmaster@bedsetco.com' -or
            $report.message_id -notmatch '^<codex-selftest-sales-migration-[^<>\s]+@bedsetco\.com>$') {
            throw 'Existing verification report needs manual review. No message was submitted.'
        }
    }
    else {
        $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ')
        $messageId = '<codex-selftest-sales-migration-' + $stamp + '@bedsetco.com>'
        $messagePath = Join-Path $dataDirectory ('sales-migration-' + $stamp + '.eml')
        $dateHeader = [DateTime]::UtcNow.ToString('ddd, dd MMM yyyy HH:mm:ss +0000', [Globalization.CultureInfo]::InvariantCulture)
        $message = @(
            'From: Andy Yu <sales@bedsetco.com>',
            'To: postmaster@bedsetco.com',
            'Reply-To: sales@bedsetco.com',
            'Subject: BedSetCo sales sender deployment verification',
            ('Date: ' + $dateHeader),
            ('Message-ID: ' + $messageId),
            'MIME-Version: 1.0',
            'Content-Type: text/plain; charset=utf-8',
            'Content-Transfer-Encoding: 8bit',
            '',
            'Internal deployment test: verify sales@bedsetco.com as the actual sender.',
            'This message is for the BedSetCo administrator mailbox.',
            '',
            'Andy Yu',
            'BedSetCo',
            'sales@bedsetco.com',
            ''
        ) -join "`r`n"
        [System.IO.File]::WriteAllText($messagePath, $message, [System.Text.UTF8Encoding]::new($false))
        $report = [ordered]@{
            started_at = (Get-Date).ToString('o')
            sender = 'sales@bedsetco.com'
            recipient = 'postmaster@bedsetco.com'
            message_id = $messageId
            message_path = $messagePath
            state = 'submission_started'
            customer_messages = 0
            wrapper_sha256 = $expectedHash
        }
        Save-VerificationReport $report
        try {
            $submission = (& $sendScript -MessagePath $messagePath -ExpectedRecipient 'postmaster@bedsetco.com' -InternalSeed) | ConvertFrom-Json
            if (-not $submission.wrapper_accepted -or $submission.message_id -ne $messageId) {
                throw 'Unexpected seed acknowledgement; retain the Message-ID for audit without resending.'
            }
            $report.submission = $submission
            $report.state = 'wrapper_accepted'
            Save-VerificationReport $report
        }
        catch {
            $report.state = 'submission_unresolved'
            $report.error = $_.Exception.Message
            Save-VerificationReport $report
            throw
        }
    }

    $received = $null
    for ($attempt = 0; $attempt -lt 4; $attempt++) {
        $received = (& $auditScript -Operation mailbox_lookup -Mailbox INBOX -Header 'Message-ID' -Value $report.message_id) | ConvertFrom-Json -Depth 30
        if ($received.found) { break }
        if ($attempt -lt 3) { Start-Sleep -Seconds 2 }
    }
    if (-not $received.found -or $received.command_exit_code -ne 0) {
        throw 'Seed not confirmed in the administrator INBOX; migration stays paused. Do not resend.'
    }
    $receivedFields = @{}
    foreach ($field in @('from', 'to', 'message-id')) {
        $values = @([regex]::Matches($received.output, ('(?im)^hdr\.' + $field + ':\s*([^\r\n]+)')) | ForEach-Object { $_.Groups[1].Value.Trim() })
        if ($values.Count -ne 1) { throw "Expected one received $field header; migration stays paused." }
        $receivedFields[$field] = $values[0]
    }
    if (([System.Net.Mail.MailAddress]::new($receivedFields.from)).Address -ne 'sales@bedsetco.com' -or
        ([System.Net.Mail.MailAddress]::new($receivedFields.to)).Address -ne 'postmaster@bedsetco.com' -or
        $receivedFields['message-id'] -ne $report.message_id) {
        throw 'Received message headers do not match the sales seed; migration stays paused.'
    }
    $delivery = (& $auditScript -Operation lookup -Needle $report.message_id) | ConvertFrom-Json -Depth 30
    if ($delivery.failure_seen -or $delivery.matched_line_count -lt 1) {
        throw 'Delivery audit is missing or contains a failure; migration stays paused.'
    }
    $report.health = Get-MailHealth
    $report.received = $received
    $report.delivery = $delivery
    $report.state = 'verified'
    $report.verified_at = (Get-Date).ToString('o')
    Save-VerificationReport $report
    $completedPath = Join-Path $dataDirectory ('sender-migration-completed-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ') + '.json')
    Move-Item -LiteralPath $pendingPath -Destination $completedPath
    $report | ConvertTo-Json -Depth 30
}
finally {
    if ($mutexAcquired) { $migrationMutex.ReleaseMutex() }
    $migrationMutex.Dispose()
}
