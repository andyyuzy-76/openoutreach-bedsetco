#Requires -Version 7.0

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$MessagePath,
    [Parameter(Mandatory)]
    [string]$ExpectedRecipient,
    [switch]$InternalSeed,
    [string]$MailHost = '173.249.205.109',
    [string]$MailUser = 'codex-mailer',
    [string]$KeyPath = (Join-Path $env:USERPROFILE '.ssh\bedsetco_mailer_ed25519'),
    [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = 'Stop'
$SenderAddress = 'sales@bedsetco.com'
$senderMigrationPending = Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\data\sender-migration-pending.json')
if ($senderMigrationPending -and -not $InternalSeed) {
    throw 'sales@bedsetco.com sender migration is pending server wrapper update; submission is disabled.'
}

function Get-SingleHeaderValue {
    param(
        [Parameter(Mandatory)] [hashtable]$Headers,
        [Parameter(Mandatory)] [string]$Name
    )

    $key = $Name.ToLowerInvariant()
    if (-not $Headers.ContainsKey($key) -or $Headers[$key].Count -ne 1) {
        throw "Message must contain exactly one $Name header."
    }
    [string]$Headers[$key][0]
}

if (-not (Test-Path -LiteralPath $MessagePath -PathType Leaf)) {
    throw "Message file was not found: $MessagePath"
}
if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
    throw "Restricted mailer key was not found: $KeyPath"
}

$resolvedMessagePath = (Resolve-Path -LiteralPath $MessagePath).Path
$messageBytes = [System.IO.File]::ReadAllBytes($resolvedMessagePath)
if ($messageBytes.Length -eq 0 -or $messageBytes.Length -gt 65536) {
    throw 'Message size must be between 1 byte and 64 KiB.'
}

$utf8 = [System.Text.UTF8Encoding]::new($false, $true)
try {
    $messageText = $utf8.GetString($messageBytes)
}
catch {
    throw 'Message must be valid UTF-8.'
}
if ($messageText.Contains([char]0)) {
    throw 'Message contains a NUL byte.'
}

$separator = [regex]::Match($messageText, "\r?\n\r?\n")
if (-not $separator.Success) {
    throw 'Message must contain a blank line between headers and body.'
}
$headerText = $messageText.Substring(0, $separator.Index)
$bodyText = $messageText.Substring($separator.Index + $separator.Length)
if ([string]::IsNullOrWhiteSpace($bodyText)) {
    throw 'Message body is empty.'
}

$headers = @{}
$currentName = $null
foreach ($line in ($headerText -split "\r?\n")) {
    if ($line -match '^[ \t]') {
        if (-not $currentName) {
            throw 'Malformed folded header.'
        }
        $lastIndex = $headers[$currentName].Count - 1
        $headers[$currentName][$lastIndex] = "$($headers[$currentName][$lastIndex]) $($line.Trim())"
        continue
    }
    if ($line -notmatch '^([^:]+):(.*)$') {
        throw "Malformed header line: $line"
    }
    $currentName = $Matches[1].Trim().ToLowerInvariant()
    if (-not $headers.ContainsKey($currentName)) {
        $headers[$currentName] = [System.Collections.Generic.List[string]]::new()
    }
    $headers[$currentName].Add($Matches[2].Trim())
}

foreach ($forbidden in @('cc', 'bcc')) {
    if ($headers.ContainsKey($forbidden)) {
        throw "The $forbidden header is not permitted."
    }
}
foreach ($name in $headers.Keys) {
    if ($name.StartsWith('resent-', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Resent-* headers are not permitted.'
    }
}

$fromValue = Get-SingleHeaderValue -Headers $headers -Name 'From'
$toValue = Get-SingleHeaderValue -Headers $headers -Name 'To'
[void](Get-SingleHeaderValue -Headers $headers -Name 'Subject')
[void](Get-SingleHeaderValue -Headers $headers -Name 'Date')
$messageId = Get-SingleHeaderValue -Headers $headers -Name 'Message-ID'

try {
    $fromAddress = ([System.Net.Mail.MailAddress]::new($fromValue)).Address.ToLowerInvariant()
    $toAddress = ([System.Net.Mail.MailAddress]::new($toValue)).Address.ToLowerInvariant()
    $expectedAddress = ([System.Net.Mail.MailAddress]::new($ExpectedRecipient)).Address.ToLowerInvariant()
}
catch {
    throw 'From, To, or expected recipient is not a valid single email address.'
}

if ($fromAddress -ne $SenderAddress) {
    throw "From must be exactly $SenderAddress."
}
if ($toAddress -ne $expectedAddress) {
    throw "To does not match the expected recipient: $expectedAddress"
}
if ($InternalSeed) {
    if ($toAddress -ne 'postmaster@bedsetco.com') {
        throw 'Internal seed recipient must be exactly postmaster@bedsetco.com.'
    }
    if ($messageId -notmatch '^<codex-selftest-[^<>\s]+@bedsetco\.com>$') {
        throw 'Internal seed Message-ID must use the codex-selftest prefix.'
    }
}
elseif ($toAddress.EndsWith('@bedsetco.com', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Customer-send helper cannot be used for an internal recipient.'
}
if ($headers.ContainsKey('reply-to')) {
    $replyTo = ([System.Net.Mail.MailAddress]::new((Get-SingleHeaderValue -Headers $headers -Name 'Reply-To'))).Address.ToLowerInvariant()
    if ($replyTo -ne $SenderAddress) {
        throw "Reply-To must be exactly $SenderAddress when present."
    }
}
if ($messageId -notmatch '^<[^<>\s]+@bedsetco\.com>$') {
    throw 'Message-ID must be a single bedsetco.com identifier enclosed in angle brackets.'
}

# During migration, only the existing internal seed route may be used, and
# only after the read-only audit confirms the exact restored sales wrapper.
if ($senderMigrationPending) {
    $auditScript = Join-Path $PSScriptRoot 'importyeti-mail-audit.ps1'
    $seedHealth = (& $auditScript -Operation health -AuditHost $MailHost) | ConvertFrom-Json -Depth 30
    if (-not $seedHealth.services_healthy -or -not $seedHealth.queue.empty -or
        $seedHealth.mailer_wrapper.sha256 -ne 'c9c3d03ab575f21e06ad79e284206aa3a251dcb54b245f17f303c7df99600fe3') {
        throw 'Internal migration seed is disabled until the exact sales wrapper, healthy services, and empty queue are verified.'
    }
}

$sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $sshPath
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardInput = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
foreach ($argument in @(
    '-T',
    '-o', 'BatchMode=yes',
    '-o', 'IdentitiesOnly=yes',
    '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ConnectTimeout=10',
    '-o', 'ConnectionAttempts=1',
    '-i', $KeyPath,
    "$MailUser@$MailHost"
)) {
    [void]$startInfo.ArgumentList.Add($argument)
}

$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $startInfo
try {
    if (-not $process.Start()) {
        throw 'Unable to start the restricted mail submission process.'
    }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.StandardInput.BaseStream.Write($messageBytes, 0, $messageBytes.Length)
    $process.StandardInput.BaseStream.Flush()
    $process.StandardInput.Close()

    if (-not $process.WaitForExit([Math]::Max(1, $TimeoutSeconds) * 1000)) {
        try { $process.Kill($true) } catch { }
        throw 'Submission timed out with an ambiguous result; do not retry this recipient in the same run.'
    }
    $stdout = $stdoutTask.GetAwaiter().GetResult().Trim()
    $stderr = $stderrTask.GetAwaiter().GetResult().Trim()
    if ($process.ExitCode -ne 0) {
        throw "Restricted wrapper rejected the message (exit $($process.ExitCode)): $stderr $stdout"
    }
    if ($stdout -ne 'accepted') {
        throw "Restricted wrapper returned an unexpected acknowledgement: $stdout"
    }

    [pscustomobject]@{
        wrapper_accepted = $true
        recipient = $expectedAddress
        message_id = $messageId
        bytes = $messageBytes.Length
        acknowledgement = $stdout
    } | ConvertTo-Json -Compress
}
finally {
    $process.Dispose()
}
