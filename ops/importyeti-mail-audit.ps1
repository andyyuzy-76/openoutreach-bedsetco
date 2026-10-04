#Requires -Version 7.0

[CmdletBinding()]
param(
    [ValidateSet('health', 'lookup', 'recent_outbound', 'mailbox_lookup')]
    [string]$Operation = 'health',
    [string]$Needle,
    [ValidateSet('INBOX', 'Sent', 'Junk', 'Trash')]
    [string]$Mailbox = 'INBOX',
    [ValidateSet('Message-ID', 'Subject', 'From', 'To')]
    [string]$Header = 'Message-ID',
    [string]$Value,
    [string]$AuditHost = '173.249.205.109',
    [string]$AuditUser = 'root',
    [string]$KeyPath = (Join-Path $env:USERPROFILE '.ssh\bedsetco_mail_audit_ed25519'),
    [int]$ConnectTimeoutSeconds = 10
)

$ErrorActionPreference = 'Stop'
$request = [ordered]@{ operation = $Operation }

switch ($Operation) {
    'lookup' {
        if ([string]::IsNullOrWhiteSpace($Needle)) {
            throw '-Needle is required for lookup.'
        }
        $request.needle = $Needle
    }
    'mailbox_lookup' {
        if ([string]::IsNullOrWhiteSpace($Value)) {
            throw '-Value is required for mailbox_lookup.'
        }
        $request.mailbox = $Mailbox
        $request.header = $Header
        $request.value = $Value
    }
}

if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
    throw "Audit key was not found: $KeyPath"
}

$sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
$sshArguments = @(
    '-T',
    '-o', 'BatchMode=yes',
    '-o', 'IdentitiesOnly=yes',
    '-o', 'StrictHostKeyChecking=yes',
    '-o', "ConnectTimeout=$ConnectTimeoutSeconds",
    '-o', 'ConnectionAttempts=1',
    '-i', $KeyPath,
    "$AuditUser@$AuditHost"
)

$requestJson = $request | ConvertTo-Json -Compress -Depth 5
$previousOutputEncoding = $OutputEncoding
try {
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $responseText = ($requestJson | & $sshPath @sshArguments 2>&1 | Out-String).Trim()
    $sshExitCode = $LASTEXITCODE
}
finally {
    $OutputEncoding = $previousOutputEncoding
}

if ($sshExitCode -ne 0) {
    throw "Mail audit SSH request failed with exit code $sshExitCode. $responseText"
}

try {
    $response = $responseText | ConvertFrom-Json -Depth 30
}
catch {
    throw "Mail audit returned invalid JSON. $responseText"
}

if (-not $response.ok) {
    throw "Mail audit rejected the request: $($response.error)"
}

$response | ConvertTo-Json -Compress -Depth 30
