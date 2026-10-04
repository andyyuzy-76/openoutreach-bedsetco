#Requires -Version 7.0
[CmdletBinding()]
param(
    [string]$OpenCliProfile = 'gtqfk5rw',
    [string[]]$RouteSessions = @('server-console', 'vnc', 'importyeti'),
    [int]$CommandTimeoutSeconds = 8,
    # Three named sessions are checked sequentially.  Each OpenCLI process
    # normally takes about three seconds to start, so ten seconds caused the
    # last session to be reported as a false timeout.
    [int]$TotalTimeoutSeconds = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-OpenCliWithTimeout {
    param(
        [Parameter(Mandatory)] [string[]]$Arguments,
        [int]$TimeoutSeconds = 8
    )

    $openCli = Get-Command opencli -CommandType ExternalScript,Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $openCli) {
        return [ordered]@{
            timed_out = $false
            exit_code = 127
            output = ''
            error = 'opencli executable was not found.'
        }
    }

    $commandPath = [string]$openCli.Source
    if ([string]::IsNullOrWhiteSpace($commandPath)) {
        $commandPath = [string]$openCli.Path
    }

    $process = $null
    $stdoutPath = [System.IO.Path]::GetTempFileName()
    $stderrPath = [System.IO.Path]::GetTempFileName()
    try {
        $processArguments = @('-NoProfile', '-NonInteractive', '-File', $commandPath) + $Arguments
        $process = Start-Process -FilePath (Join-Path $PSHOME 'pwsh.exe') -ArgumentList $processArguments -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru -WindowStyle Hidden

        $completed = $process.WaitForExit([Math]::Max(1000, $TimeoutSeconds * 1000))
        if (-not $completed) {
            try {
                $process.Kill($true)
            }
            catch {
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            }
            [void]$process.WaitForExit(1000)
            return [ordered]@{
                timed_out = $true
                exit_code = $null
                output = (Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue)
                error = "opencli timed out after $TimeoutSeconds seconds."
            }
        }

        return [ordered]@{
            timed_out = $false
            exit_code = $process.ExitCode
            output = (Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue)
            error = (Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue)
        }
    }
    finally {
        if ($process) {
            $process.Dispose()
        }
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

function ConvertFrom-TabListOutput {
    param([AllowNull()] [string]$Output)

    if ([string]::IsNullOrWhiteSpace($Output)) {
        return @()
    }

    $start = $Output.IndexOf('[')
    $end = $Output.LastIndexOf(']')
    if ($start -lt 0 -or $end -le $start) {
        return @()
    }

    try {
        $parsed = $Output.Substring($start, $end - $start + 1) | ConvertFrom-Json -Depth 20
        return @($parsed)
    }
    catch {
        return @()
    }
}

function Get-TabRouteClass {
    param([Parameter(Mandatory)] $Tab)

    $url = [string]$Tab.url
    $title = [string]$Tab.title
    if ([string]::IsNullOrWhiteSpace($url) -or $url -match '(?i)^about:blank$') {
        return 'blank'
    }

    if ($url -match '(?i)(/login|/signin|/sign-in)' -or $title -match '(?i)^(login|sign in|sign-in)') {
        return 'login_or_wrong_context'
    }

    if ($url -match '(?i)(cp\.green\.cloud|mail\.bedsetco\.com|vnc|guacamole)') {
        return 'route_present'
    }

    return 'nonblank_unknown'
}

function Get-RouteSessionState {
    param(
        [Parameter(Mandatory)] [string]$SessionName,
        [int]$TimeoutSeconds = 8
    )

    $call = Invoke-OpenCliWithTimeout -Arguments @(
        '--profile', $OpenCliProfile, 'browser', $SessionName, 'tab', 'list'
    ) -TimeoutSeconds $TimeoutSeconds
    $tabs = @(ConvertFrom-TabListOutput -Output ([string]$call.output))
    $tabStates = @()

    for ($index = 0; $index -lt $tabs.Count; $index++) {
        $tab = $tabs[$index]
        $tabStates += [ordered]@{
            index = $index
            active = [bool]$tab.active
            title = [string]$tab.title
            url = [string]$tab.url
            route_class = (Get-TabRouteClass -Tab $tab)
        }
    }

    $returnedEmptyTabArray = ([string]$call.output -match '(?s)\[\s*\]')
    if ($call.timed_out) {
        $status = 'command_timeout'
    }
    elseif ($tabs.Count -eq 0) {
        $status = if ($returnedEmptyTabArray) { 'empty_session' } else { 'missing_or_unreadable' }
    }
    elseif ($tabStates.route_class -contains 'route_present') {
        $status = 'route_present'
    }
    elseif ($tabStates.route_class -contains 'login_or_wrong_context') {
        $status = 'login_or_wrong_context'
    }
    elseif (@($tabStates.route_class | Where-Object { $_ -ne 'blank' }).Count -eq 0) {
        $status = 'blank'
    }
    else {
        $status = 'nonblank_unknown'
    }

    return [ordered]@{
        session = $SessionName
        status = $status
        tabs = $tabStates
        command_exit_code = $call.exit_code
        command_error = ([string]$call.error).Trim()
    }
}

$sessionStates = @()
$routeDeadline = (Get-Date).AddSeconds([Math]::Max(1, $TotalTimeoutSeconds))
foreach ($session in $RouteSessions) {
    $remainingSeconds = [int][Math]::Ceiling(($routeDeadline - (Get-Date)).TotalSeconds)
    if ($remainingSeconds -le 0) {
        $sessionStates += [ordered]@{
            session = $session
            status = 'command_timeout'
            tabs = @()
            command_exit_code = $null
            command_error = "Route preflight exceeded its $TotalTimeoutSeconds second total budget."
        }
        continue
    }

    $sessionStates += Get-RouteSessionState -SessionName $session `
        -TimeoutSeconds ([Math]::Min($CommandTimeoutSeconds, [Math]::Max(1, $remainingSeconds)))
}

$routePresent = [bool]($sessionStates.status -contains 'route_present')
if ($routePresent) {
    $routeStatus = 'route_present'
}
elseif ($sessionStates.status -contains 'command_timeout') {
    $routeStatus = 'command_timeout'
}
elseif ($sessionStates.status -contains 'login_or_wrong_context') {
    $routeStatus = 'login_or_wrong_context'
}
elseif ($sessionStates.status -contains 'nonblank_unknown') {
    $routeStatus = 'nonblank_unknown'
}
elseif ($sessionStates.status -contains 'blank') {
    $routeStatus = 'blank'
}
elseif ($sessionStates.status -contains 'empty_session') {
    $routeStatus = 'empty_session'
}
else {
    $routeStatus = 'missing_or_unreadable'
}

[ordered]@{
    checked_at = (Get-Date).ToString('o')
    profile = $OpenCliProfile
    route_status = $routeStatus
    route_present = $routePresent
    sessions = $sessionStates
    notes = @(
        'This is a read-only route check; it does not open pages, log in, read credentials, or send mail.',
        'Bridge daemon and extension health do not prove that the authenticated GreenCloud to bedsetco-mail to Browser VNC route is available.',
        'A blank tab, local Roundcube login page, or a new ordinary Chrome context is not an authenticated route.'
    )
} | ConvertTo-Json -Depth 20

# The result is diagnostic JSON. Callers must gate on route_status/route_present.
exit 0
