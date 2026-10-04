#Requires -Version 7.0

[CmdletBinding()]
param(
    [string]$MailHost = 'mail.bedsetco.com',
    [int[]]$Ports = @(25, 465, 587, 993, 995),
    [int]$TimeoutMilliseconds = 3000,
    [int]$OpenCliTimeoutSeconds = 8,
    [string]$OpenCliProfile = 'gtqfk5rw',
    [string]$SessionPreflightPath = (Join-Path $PSScriptRoot 'importyeti-mail-session-preflight.ps1'),
    [string]$AuditPreflightPath = (Join-Path $PSScriptRoot 'importyeti-mail-audit.ps1'),
    [int]$AuditTimeoutSeconds = 15,
    [switch]$IncludeLegacyBrowserDiagnostics,
    [string]$ExpectedMailerSha256 = 'c9c3d03ab575f21e06ad79e284206aa3a251dcb54b245f17f303c7df99600fe3'
)

function Invoke-PwshFileWithTimeout {
    param(
        [Parameter(Mandatory)] [string]$ScriptPath,
        [string[]]$ArgumentList = @(),
        [int]$TimeoutSeconds = 8
    )

    $process = $null
    $stdoutPath = [System.IO.Path]::GetTempFileName()
    $stderrPath = [System.IO.Path]::GetTempFileName()
    try {
        if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
            throw "PowerShell script was not found: $ScriptPath"
        }

        $pwshPath = Join-Path $PSHOME 'pwsh.exe'
        $pwshArguments = @('-NoLogo', '-NoProfile', '-NonInteractive', '-File', $ScriptPath) + $ArgumentList
        $process = Start-Process -FilePath $pwshPath -ArgumentList $pwshArguments `
            -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath `
            -PassThru -WindowStyle Hidden

        $timedOut = -not $process.WaitForExit([Math]::Max(1, $TimeoutSeconds) * 1000)
        if ($timedOut) {
            try {
                $process.Kill($true)
            }
            catch {
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            }
            [void]$process.WaitForExit(1000)
        }

        $stdout = [string](Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue)
        $stderr = [string](Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue)
        $outputParts = @($stdout, $stderr) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        if ($timedOut) {
            $outputParts += "Command timed out after $TimeoutSeconds seconds."
        }

        [pscustomobject]@{
            output = (($outputParts -join [Environment]::NewLine).Trim())
            timed_out = $timedOut
            exit_code = if ($timedOut) { $null } else { $process.ExitCode }
        }
    }
    catch {
        [pscustomobject]@{
            output = $_.Exception.Message
            timed_out = $false
            exit_code = -1
        }
    }
    finally {
        if ($process) {
            $process.Dispose()
        }
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-OpenCliDoctorWithTimeout {
    param([int]$TimeoutSeconds = 8)

    try {
        $openCliScript = (Get-Command opencli -CommandType ExternalScript -ErrorAction Stop).Source
        Invoke-PwshFileWithTimeout -ScriptPath $openCliScript -ArgumentList @('doctor') -TimeoutSeconds $TimeoutSeconds
    }
    catch {
        [pscustomobject]@{
            output = $_.Exception.Message
            timed_out = $false
            exit_code = -1
        }
    }
}

function Invoke-SessionPreflightWithTimeout {
    param(
        [Parameter(Mandatory)] [string]$ScriptPath,
        [Parameter(Mandatory)] [string]$Profile,
        [int]$TimeoutSeconds = 45
    )

    Invoke-PwshFileWithTimeout -ScriptPath $ScriptPath -ArgumentList @(
        '-OpenCliProfile', $Profile,
        '-CommandTimeoutSeconds', [string]$OpenCliTimeoutSeconds,
        # The session probe performs three sequential OpenCLI calls.  Keep a
        # bounded but sufficient budget so the third call is not misclassified
        # as a missing route.
        '-TotalTimeoutSeconds', [string]([Math]::Max(30, ($OpenCliTimeoutSeconds * 3) + 6))
    ) -TimeoutSeconds $TimeoutSeconds
}

$startedAt = Get-Date
$result = [ordered]@{
    checked_at = (Get-Date).ToString('o')
    completed = $false
    duration_ms = $null
    mail_host = $MailHost
    dns_addresses = @()
    tcp = [ordered]@{}
    https = [ordered]@{
        reachable = $false
        certificate_valid = $null
        status_code = $null
        probe = $null
        error = $null
    }
    mail_audit = [ordered]@{
        reachable = $false
        timed_out = $false
        exit_code = $null
        services = $null
        services_healthy = $false
        wrapper_exists = $false
        wrapper_sha256 = $null
        wrapper_matches_expected = $false
        queue_empty = $false
        log_files = @()
        error = $null
    }
    mail_control_ready = $false
    customer_send_gate_ready = $false
    browser_bridge = [ordered]@{
        daemon = 'unknown'
        extension = 'unknown'
        connectivity = 'unknown'
        timed_out = $false
        exit_code = $null
        doctor_output = $null
    }
    browser_session = [ordered]@{
        profile = $OpenCliProfile
        route_status = 'unknown'
        route_present = $false
        timed_out = $false
        exit_code = $null
        sessions = @()
        error = $null
    }
    notes = @(
        'Network, durable mail audit, and optional legacy browser diagnostics are reported separately. Browser checks are performed through the current Codex browser tools by default.',
        'By explicit user authorization on 2026-08-16, the durable forced-command mail audit replaces the expired authenticated Browser VNC page as the customer-send gate.',
        'Browser Bridge and browser-session fields remain diagnostic only and do not grant or block customer sending.',
        'A ready preflight still does not replace recipient deduplication, historical delivery review, actual From verification, or post-send external MX and bounce acceptance.'
    )
}

try {
    $result.dns_addresses = @(
        Resolve-DnsName -Name $MailHost -Type A -ErrorAction Stop |
            Where-Object { $_.IPAddress } |
            Select-Object -ExpandProperty IPAddress -Unique
    )
}
catch {
    $result.dns_error = $_.Exception.Message
}

$tcpChecks = @()
foreach ($port in $Ports) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $tcpChecks += [pscustomobject]@{
            port = $port
            client = $client
            task = $client.ConnectAsync($MailHost, $port)
            start_error = $null
        }
    }
    catch {
        $tcpChecks += [pscustomobject]@{
            port = $port
            client = $client
            task = $null
            start_error = $_.Exception.GetBaseException().Message
        }
    }
}

$tcpDeadline = (Get-Date).AddMilliseconds($TimeoutMilliseconds)
foreach ($check in $tcpChecks) {
    try {
        if ($check.start_error) {
            throw $check.start_error
        }
        $remainingMilliseconds = [Math]::Max(0, [int](($tcpDeadline - (Get-Date)).TotalMilliseconds))
        $connectedInTime = $check.task.IsCompleted -or $check.task.Wait($remainingMilliseconds)
        $result.tcp[[string]$check.port] = [ordered]@{
            reachable = [bool]($connectedInTime -and $check.client.Connected)
            error = $null
        }
    }
    catch {
        $result.tcp[[string]$check.port] = [ordered]@{
            reachable = $false
            error = $_.Exception.GetBaseException().Message
        }
    }
    finally {
        $check.client.Dispose()
    }
}

try {
    $auditResult = Invoke-PwshFileWithTimeout -ScriptPath $AuditPreflightPath -ArgumentList @(
        '-Operation', 'health'
    ) -TimeoutSeconds $AuditTimeoutSeconds
    $result.mail_audit.timed_out = $auditResult.timed_out
    $result.mail_audit.exit_code = $auditResult.exit_code

    if ($auditResult.timed_out) {
        throw $auditResult.output
    }
    if ($auditResult.exit_code -ne 0) {
        throw "Mail audit exited with code $($auditResult.exit_code). $($auditResult.output)"
    }

    $jsonStart = $auditResult.output.IndexOf('{')
    $jsonEnd = $auditResult.output.LastIndexOf('}')
    if ($jsonStart -lt 0 -or $jsonEnd -le $jsonStart) {
        throw 'Mail audit returned no JSON.'
    }
    $auditJson = $auditResult.output.Substring($jsonStart, $jsonEnd - $jsonStart + 1) | ConvertFrom-Json -Depth 30
    if (-not $auditJson.ok) {
        throw "Mail audit rejected the request: $($auditJson.error)"
    }

    $result.mail_audit.reachable = $true
    $result.mail_audit.services = $auditJson.services
    $result.mail_audit.services_healthy = [bool]$auditJson.services_healthy
    $result.mail_audit.wrapper_exists = [bool]$auditJson.mailer_wrapper.exists
    $result.mail_audit.wrapper_sha256 = [string]$auditJson.mailer_wrapper.sha256
    $result.mail_audit.wrapper_matches_expected = (
        $result.mail_audit.wrapper_exists -and
        $result.mail_audit.wrapper_sha256 -eq $ExpectedMailerSha256
    )
    $result.mail_audit.queue_empty = [bool]$auditJson.queue.empty
    $result.mail_audit.log_files = @($auditJson.log_files)
    $result.mail_control_ready = (
        $result.mail_audit.reachable -and
        $result.mail_audit.services_healthy -and
        $result.mail_audit.wrapper_matches_expected
    )
    # The customer gate is finalized after HTTPS below. Browser transport and
    # page-session state are diagnostic only; the user authorized the durable
    # forced-command audit to replace the expired VNC page requirement.
}
catch {
    $result.mail_audit.error = $_.Exception.Message
}

try {
    $requestParameters = @{
        Uri = "https://$MailHost/"
        Method = 'Head'
        MaximumRedirection = 0
        TimeoutSec = [Math]::Max(3, [Math]::Ceiling($TimeoutMilliseconds / 1000))
        SkipHttpErrorCheck = $true
        NoProxy = $true
        ErrorAction = 'Stop'
    }
    $response = Invoke-WebRequest @requestParameters
    $result.https.reachable = $true
    $result.https.certificate_valid = $true
    $result.https.status_code = [int]$response.StatusCode
    $result.https.probe = 'powershell'
}
catch {
    $powerShellHttpsError = $_.Exception.Message

    try {
        # Windows Schannel can fail locally with SEC_E_NO_CREDENTIALS even when
        # the server certificate is valid.  Python uses its bundled OpenSSL and
        # still performs full hostname and certificate-chain verification.
        $pythonCommand = @(Get-Command python -CommandType Application -ErrorAction Stop)[0]
        $pythonTimeoutSeconds = [Math]::Max(3, [Math]::Ceiling($TimeoutMilliseconds / 1000))
        $pythonCode = "import http.client,json,ssl,sys; h=sys.argv[1]; t=float(sys.argv[2]); c=http.client.HTTPSConnection(h,443,timeout=t,context=ssl.create_default_context()); c.request('HEAD','/'); r=c.getresponse(); print(json.dumps({'status_code':r.status})); c.close()"
        $pythonOutput = & $pythonCommand.Source -c $pythonCode $MailHost ([string]$pythonTimeoutSeconds) 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw (($pythonOutput | ForEach-Object { [string]$_ }) -join [Environment]::NewLine)
        }
        $pythonProbe = (($pythonOutput | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop
        $result.https.reachable = $true
        $result.https.certificate_valid = $true
        $result.https.status_code = [int]$pythonProbe.status_code
        $result.https.probe = 'python-openssl-fallback'
        $result.https.error = $null
    }
    catch {
        $result.https.certificate_valid = $false
        $result.https.probe = 'powershell-and-python-failed'
        $result.https.error = "$powerShellHttpsError | Verified TLS fallback: $($_.Exception.Message)"
    }
}

if ($IncludeLegacyBrowserDiagnostics) {
try {
    $doctorResult = Invoke-OpenCliDoctorWithTimeout -TimeoutSeconds $OpenCliTimeoutSeconds
    $doctorOutput = $doctorResult.output
    $result.browser_bridge.doctor_output = $doctorOutput
    $result.browser_bridge.timed_out = $doctorResult.timed_out
    $result.browser_bridge.exit_code = $doctorResult.exit_code

    if ($doctorOutput -match '\[OK\]\s+Daemon') {
        $result.browser_bridge.daemon = 'running'
    }
    elseif ($doctorOutput -match 'Daemon') {
        $result.browser_bridge.daemon = 'problem'
    }

    if ($doctorOutput -match '\[OK\]\s+Extension') {
        $result.browser_bridge.extension = 'connected'
    }
    elseif ($doctorOutput -match '(Extension:\s+not connected|extension is not connected)') {
        $result.browser_bridge.extension = 'disconnected'
    }

    if ($doctorOutput -match '\[OK\]\s+Connectivity') {
        $result.browser_bridge.connectivity = 'ok'
    }
    elseif ($doctorOutput -match '\[FAIL\]\s+Connectivity') {
        $result.browser_bridge.connectivity = 'failed'
    }

    if ($doctorResult.timed_out) {
        $result.browser_bridge.connectivity = 'doctor_timed_out'
    }
}
catch {
    $result.browser_bridge.doctor_output = $_.Exception.Message
    $result.browser_bridge.connectivity = 'doctor_failed'
}

try {
    $sessionResult = Invoke-SessionPreflightWithTimeout -ScriptPath $SessionPreflightPath -Profile $OpenCliProfile -TimeoutSeconds ([Math]::Max(45, ($OpenCliTimeoutSeconds * 3) + 12))
    $result.browser_session.timed_out = $sessionResult.timed_out
    $result.browser_session.exit_code = $sessionResult.exit_code
    if ($sessionResult.timed_out) {
        $result.browser_session.route_status = 'command_timeout'
        $result.browser_session.error = $sessionResult.output
    }
    else {
        $jsonStart = $sessionResult.output.IndexOf('{')
        $jsonEnd = $sessionResult.output.LastIndexOf('}')
        if ($jsonStart -lt 0 -or $jsonEnd -le $jsonStart) {
            throw 'Session preflight returned no JSON.'
        }
        $sessionJson = $sessionResult.output.Substring($jsonStart, $jsonEnd - $jsonStart + 1) | ConvertFrom-Json -Depth 20
        $result.browser_session.route_status = [string]$sessionJson.route_status
        $result.browser_session.route_present = [bool]$sessionJson.route_present
        $result.browser_session.sessions = @($sessionJson.sessions)
    }
}
catch {
    $result.browser_session.route_status = 'missing_or_unreadable'
    $result.browser_session.error = $_.Exception.Message
}
}
else {
    $result.browser_bridge.daemon = 'not_probed'
    $result.browser_bridge.extension = 'not_probed'
    $result.browser_bridge.connectivity = 'use_current_codex_browser_tools'
    $result.browser_session.route_status = 'use_current_codex_browser_tools'
}

$result.customer_send_gate_ready = (
    -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\openoutreach-bedsetco\data\sender-migration-pending.json')) -and
    $result.mail_control_ready -and
    $result.mail_audit.queue_empty -and
    $result.https.reachable -and
    $result.https.certificate_valid
)

$result.completed = $true
$result.duration_ms = [int]((Get-Date) - $startedAt).TotalMilliseconds
[pscustomobject]$result | ConvertTo-Json -Depth 8
