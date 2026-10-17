#Requires -Version 7.2
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $IsWindows -or $env:GITHUB_ACTIONS -ne 'true' -or $env:CI -ne 'true' -or
    $env:RUNNER_OS -ne 'Windows' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'WINDOWS_INSTALLER_SMOKE_REQUIRES_HOSTED_WINDOWS_CI'
}
if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP) -or
    -not [System.IO.Path]::IsPathFullyQualified($env:RUNNER_TEMP)) {
    throw 'WINDOWS_INSTALLER_SMOKE_REQUIRES_RUNNER_TEMP'
}

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runnerTemporary = Get-Item -LiteralPath $env:RUNNER_TEMP
if (-not $runnerTemporary.PSIsContainer -or
    ($runnerTemporary.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
    throw 'WINDOWS_INSTALLER_SMOKE_UNSAFE_RUNNER_TEMP'
}
$temporaryRoot = Join-Path $runnerTemporary.FullName ('moyu-installer-smoke-' + [guid]::NewGuid().ToString('N'))
$installDirectory = Join-Path $temporaryRoot '中文 安装目录'
$installedExecutable = Join-Path $installDirectory '墨屿.exe'
$installedBackend = Join-Path $installDirectory 'resources\backend\moyu-service.exe'
$uninstaller = Join-Path $installDirectory 'Uninstall 墨屿.exe'
$nodeExecutable = ''
$temporaryCreated = $false
$stage = 'PREPARE'
$failureCode = $null
$failureDiagnostics = $null
$cleanupFailed = $false
$installationVerified = $false
$installedDesktopCases = 0
$uninstallationVerified = $false
$defaultUninstallationAttempted = $false
$cleanupUninstallationAttempted = $false
$cleanupUninstallationVerified = $false
$appDataFixture = $null
$appDataFixtureCreated = $false
$appDataPreservationVerified = $false
$appDataCleanupVerified = $false
$appDataExpectedHashes = @{}
$appDataExpectedLengths = @{}
$appDataInitialHash = $null
$appDataPreservedHash = $null

function Get-SystemAppDataDirectory {
    $systemDirectory = [Environment]::GetFolderPath([Environment+SpecialFolder]::ApplicationData)
    if ([string]::IsNullOrWhiteSpace($systemDirectory) -or
        -not [System.IO.Path]::IsPathFullyQualified($systemDirectory) -or
        [string]::IsNullOrWhiteSpace($env:APPDATA) -or
        -not [System.IO.Path]::IsPathFullyQualified($env:APPDATA) -or
        -not [string]::Equals([System.IO.Path]::GetFullPath($systemDirectory),
            [System.IO.Path]::GetFullPath($env:APPDATA), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'WINDOWS_INSTALLER_SMOKE_UNSAFE_SYSTEM_APPDATA'
    }
    $directory = Get-Item -LiteralPath $systemDirectory -Force
    if (-not $directory.PSIsContainer -or
        ($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw 'WINDOWS_INSTALLER_SMOKE_UNSAFE_SYSTEM_APPDATA'
    }
    return $directory
}

function Write-NewFixtureFile {
    param([string] $Path, [byte[]] $Bytes)
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $stream.Write($Bytes, 0, $Bytes.Length)
    } finally {
        $stream.Dispose()
    }
}

function Assert-OwnedAppDataFixture {
    $systemDirectory = Get-SystemAppDataDirectory
    $expected = Join-Path $systemDirectory.FullName 'Moyu'
    if (-not $appDataFixtureCreated -or
        -not [string]::Equals($appDataFixture, $expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'WINDOWS_INSTALLER_SMOKE_APPDATA_OWNERSHIP_INVALID'
    }
    $directory = Get-Item -LiteralPath $appDataFixture -Force
    if (-not $directory.PSIsContainer -or
        ($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw 'WINDOWS_INSTALLER_SMOKE_APPDATA_OWNERSHIP_INVALID'
    }
    $owner = Get-Item -LiteralPath (Join-Path $appDataFixture '.installer-smoke-owner') -Force
    $manuscripts = Get-Item -LiteralPath (Join-Path $appDataFixture 'synthetic-manuscripts') -Force
    foreach ($entry in @($owner, $manuscripts)) {
        if ($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw 'WINDOWS_INSTALLER_SMOKE_APPDATA_OWNERSHIP_INVALID'
        }
    }
    if ($owner.PSIsContainer -or -not $manuscripts.PSIsContainer -or
        @(Get-ChildItem -LiteralPath $appDataFixture -Force).Count -ne 2) {
        throw 'WINDOWS_INSTALLER_SMOKE_APPDATA_CONTENT_CHANGED'
    }
    $manuscript = Get-Item -LiteralPath (Join-Path $manuscripts.FullName 'preserved.bin') -Force
    if ($manuscript.PSIsContainer -or
        ($manuscript.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
        @(Get-ChildItem -LiteralPath $manuscripts.FullName -Force).Count -ne 1) {
        throw 'WINDOWS_INSTALLER_SMOKE_APPDATA_CONTENT_CHANGED'
    }
    foreach ($entry in @($owner, $manuscript)) {
        $hash = (Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($entry.Length -ne $appDataExpectedLengths[$entry.Name] -or
            $hash -ne $appDataExpectedHashes[$entry.Name]) {
            throw 'WINDOWS_INSTALLER_SMOKE_APPDATA_CONTENT_CHANGED'
        }
    }
    return $appDataExpectedHashes['preserved.bin']
}

function Invoke-BoundedProcess {
    param(
        [Parameter(Mandatory)][string] $Executable,
        [string[]] $Arguments = @(),
        [string] $RawArguments = '',
        [Parameter(Mandatory)][int] $TimeoutSeconds,
        [Parameter(Mandatory)][string] $Label,
        [switch] $AllowNonZeroExitCode
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    if ($RawArguments) {
        $startInfo.Arguments = $RawArguments
    } else {
        foreach ($argument in $Arguments) {
            $startInfo.ArgumentList.Add($argument)
        }
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $started = $false
    try {
        Write-Host (@{ stage = $Label; phase = 'start';
                executable_exists = [System.IO.File]::Exists($Executable);
                argument_count = $Arguments.Count } | ConvertTo-Json -Compress)
        $started = $process.Start()
        if (-not $started) {
            throw "WINDOWS_INSTALLER_SMOKE_START_$Label"
        }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            throw "WINDOWS_INSTALLER_SMOKE_TIMEOUT_$Label"
        }
        if (-not [System.Threading.Tasks.Task]::WaitAll(
                [System.Threading.Tasks.Task[]]@($stdout, $stderr), 10000)) {
            throw "WINDOWS_INSTALLER_SMOKE_OUTPUT_TIMEOUT_$Label"
        }
        Write-Host (@{ stage = $Label; exit_code = $process.ExitCode } | ConvertTo-Json -Compress)
        if ($process.ExitCode -ne 0 -and -not $AllowNonZeroExitCode) {
            throw "WINDOWS_INSTALLER_SMOKE_PROCESS_FAILED_$Label"
        }
        return @{
            ExitCode = $process.ExitCode
            StandardOutput = $stdout.GetAwaiter().GetResult()
            StandardError = $stderr.GetAwaiter().GetResult()
        }
    } finally {
        if ($started -and -not $process.HasExited) {
            $process.Kill($true)
            if (-not $process.WaitForExit(10000)) {
                throw "WINDOWS_INSTALLER_SMOKE_STOP_FAILED_$Label"
            }
        }
        $process.Dispose()
    }
}

function Stop-InstalledProcesses {
    $prefix = [System.IO.Path]::GetFullPath($installDirectory) + [System.IO.Path]::DirectorySeparatorChar
    foreach ($entry in Get-CimInstance Win32_Process) {
        if (-not $entry.ExecutablePath -or
            -not $entry.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            continue
        }
        $running = $null
        try {
            $running = [System.Diagnostics.Process]::GetProcessById([int]$entry.ProcessId)
            if (-not $running.HasExited) {
                $running.Kill($true)
                if (-not $running.WaitForExit(10000)) {
                    throw 'WINDOWS_INSTALLER_SMOKE_OWNED_PROCESS_STILL_RUNNING'
                }
            }
        } catch [System.ArgumentException] {
            # A child may exit between the process inventory and lookup.
        } finally {
            if ($null -ne $running) {
                $running.Dispose()
            }
        }
    }
}

try {
    $nodeCommand = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $nodeCommand) {
        throw 'WINDOWS_INSTALLER_SMOKE_NODE_MISSING_OR_UNSAFE'
    }
    $nodeExecutable = [string] $nodeCommand.Source
    if (-not [System.IO.Path]::IsPathFullyQualified($nodeExecutable) -or
        -not [System.IO.File]::Exists($nodeExecutable)) {
        throw 'WINDOWS_INSTALLER_SMOKE_NODE_MISSING_OR_UNSAFE'
    }
    $package = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'package.json') | ConvertFrom-Json -AsHashtable
    if ($package.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$') {
        throw 'WINDOWS_INSTALLER_SMOKE_INVALID_PACKAGE_VERSION'
    }
    $installer = Join-Path $projectRoot ("dist\Moyu-{0}-win-x64.exe" -f $package.version)
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf) -or
        ((Get-Item -LiteralPath $installer).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw 'WINDOWS_INSTALLER_SMOKE_INSTALLER_MISSING_OR_UNSAFE'
    }
    if (Test-Path -LiteralPath $temporaryRoot) {
        throw 'WINDOWS_INSTALLER_SMOKE_TEMPORARY_DIRECTORY_EXISTS'
    }
    New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
    $temporaryCreated = $true

    $stage = 'PREPARE_APPDATA'
    $systemAppData = Get-SystemAppDataDirectory
    $appDataFixture = Join-Path $systemAppData.FullName 'Moyu'
    if ($null -ne (Get-Item -LiteralPath $appDataFixture -Force -ErrorAction SilentlyContinue)) {
        throw 'WINDOWS_INSTALLER_SMOKE_EXISTING_APPDATA_REFUSED'
    }
    New-Item -ItemType Directory -Path $appDataFixture | Out-Null
    $appDataFixtureCreated = $true
    $ownerBytes = [System.Text.Encoding]::UTF8.GetBytes([guid]::NewGuid().ToString('N'))
    $manuscriptBytes = [System.Text.Encoding]::UTF8.GetBytes(
        'Synthetic preserved manuscript ' + [guid]::NewGuid().ToString('N'))
    foreach ($entry in @(
        @{ Name = '.installer-smoke-owner'; Bytes = $ownerBytes },
        @{ Name = 'preserved.bin'; Bytes = $manuscriptBytes }
    )) {
        $appDataExpectedHashes[$entry.Name] = [Convert]::ToHexString(
            [System.Security.Cryptography.SHA256]::HashData($entry.Bytes)).ToLowerInvariant()
        $appDataExpectedLengths[$entry.Name] = $entry.Bytes.Length
    }
    Write-NewFixtureFile -Path (Join-Path $appDataFixture '.installer-smoke-owner') -Bytes $ownerBytes
    $fixtureManuscripts = Join-Path $appDataFixture 'synthetic-manuscripts'
    New-Item -ItemType Directory -Path $fixtureManuscripts | Out-Null
    Write-NewFixtureFile -Path (Join-Path $fixtureManuscripts 'preserved.bin') -Bytes $manuscriptBytes
    $appDataInitialHash = Assert-OwnedAppDataFixture

    $stage = 'INSTALL'
    # NSIS consumes everything after /D= as the path; it must remain unquoted and last.
    $null = Invoke-BoundedProcess -Executable $installer `
        -RawArguments "/S /currentuser /D=$installDirectory" -TimeoutSeconds 180 -Label $stage
    foreach ($required in @($installedExecutable, $installedBackend, $uninstaller)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf) -or
            ((Get-Item -LiteralPath $required).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'WINDOWS_INSTALLER_SMOKE_INSTALLED_FILES_MISSING_OR_UNSAFE'
        }
    }
    $installationVerified = $true

    $stage = 'INSTALLED_DESKTOP'
    $result = Invoke-BoundedProcess -Executable $nodeExecutable -Arguments @(
        (Join-Path $projectRoot 'tests\desktop_smoke.cjs'), '--executable', $installedExecutable
    ) -TimeoutSeconds 420 -Label $stage -AllowNonZeroExitCode
    try {
        $report = $result.StandardOutput.Trim() | ConvertFrom-Json -AsHashtable
        if ($report -isnot [System.Collections.IDictionary]) {
            throw 'WINDOWS_INSTALLER_SMOKE_DESKTOP_REPORT_UNREADABLE'
        }
    } catch {
        $codes = @([regex]::Matches($result.StandardError,
                '\b(?:ERR_[A-Z0-9_]+|MODULE_NOT_FOUND|ENOENT|EACCES|EPERM|TimeoutError|TargetClosedError)\b') |
            ForEach-Object { $_.Value } | Sort-Object -Unique)
        Write-Host (@{ stage = $stage; error = 'DESKTOP_REPORT_UNREADABLE'; stderr_error_codes = $codes;
                stdout_length = $result.StandardOutput.Length; stderr_length = $result.StandardError.Length } |
            ConvertTo-Json -Compress)
        throw 'WINDOWS_INSTALLER_SMOKE_DESKTOP_REPORT_UNREADABLE'
    }
    $safeReport = [ordered]@{
        stage = $stage
        exit_code = $result.ExitCode
        passed = $report['passed'] -eq $true
        completed_cases = @($report['cases']).Count
        synthetic_data_only = $report['synthetic_data_only'] -eq $true
    }
    $failed = $report['failure']
    if ($failed -is [System.Collections.IDictionary]) {
        foreach ($field in @('case', 'step')) {
            if ($failed[$field] -is [string] -and $failed[$field] -match '^[a-z0-9-]{1,120}$') {
                $safeReport[$field] = $failed[$field]
            }
        }
        if ($failed['location'] -is [string] -and
            $failed['location'] -match '^tests[/\\]desktop_smoke\.cjs:[0-9]+:[0-9]+$') {
            $safeReport['location'] = $failed['location']
        }
        $safeReport['error_codes'] = @([regex]::Matches([string]$failed['message'],
                '\b(?:TIMEOUT_[A-Z0-9_]+|DESKTOP_[A-Z0-9_]+|ERR_[A-Z0-9_]+|ENOENT|EACCES|EPERM)\b') |
            ForEach-Object { $_.Value } | Sort-Object -Unique)
    }
    Write-Host ($safeReport | ConvertTo-Json -Compress)
    if ($result.ExitCode -ne 0 -or $report['passed'] -ne $true -or
        $report['synthetic_data_only'] -ne $true -or @($report['cases']).Count -ne 10 -or
        @($report['cases'] | Where-Object { $_.passed -ne $true }).Count -ne 0 -or
        $report['provider_jobs'] -ne 0 -or $report['unexpected_external_requests'] -ne 0 -or
        $report['page_errors'] -ne 0) {
        throw 'WINDOWS_INSTALLER_SMOKE_DESKTOP_REPORT_FAILED'
    }
    Write-Host 'windows-installer-smoke: installed desktop 10/10 passed'
    $installedDesktopCases = 10

    $stage = 'UNINSTALL'
    $null = Assert-OwnedAppDataFixture
    # _?= prevents NSIS self-copying; it also must be unquoted and last.
    $defaultUninstallationAttempted = $true
    $null = Invoke-BoundedProcess -Executable $uninstaller `
        -RawArguments "/S /currentuser _?=$installDirectory" -TimeoutSeconds 120 -Label $stage
    if ((Test-Path -LiteralPath $installedExecutable) -or (Test-Path -LiteralPath $installedBackend)) {
        throw 'WINDOWS_INSTALLER_SMOKE_APPLICATION_REMAINED_AFTER_UNINSTALL'
    }
    $uninstallationVerified = $true
    $stage = 'VERIFY_PRESERVED_APPDATA'
    $appDataPreservedHash = Assert-OwnedAppDataFixture
    $appDataPreservationVerified = $true
} catch {
    $failureRecord = $_
    $exceptionTypes = @()
    $nativeErrorCodes = @()
    $diagnosticException = $failureRecord.Exception
    for ($depth = 0; $depth -lt 6 -and $null -ne $diagnosticException; $depth++) {
        $exceptionTypes += $diagnosticException.GetType().FullName
        if ($diagnosticException -is [System.ComponentModel.Win32Exception]) {
            $nativeErrorCodes += $diagnosticException.NativeErrorCode
        }
        $diagnosticException = $diagnosticException.InnerException
    }
    $failureDiagnostics = [ordered]@{
        stage = $stage
        script_line = $failureRecord.InvocationInfo.ScriptLineNumber
        category = [string] $failureRecord.CategoryInfo.Category
        exception_types = $exceptionTypes
        native_error_codes = $nativeErrorCodes
        node_executable_exists = [System.IO.File]::Exists($nodeExecutable)
    }
    Write-Host ($failureDiagnostics | ConvertTo-Json -Compress)
    $failureCode = "WINDOWS_INSTALLER_SMOKE_FAILED_$stage"
    if ($failureRecord.Exception.Message -match '^WINDOWS_INSTALLER_SMOKE_[A-Z0-9_]+$') {
        $failureCode = $failureRecord.Exception.Message
    }
} finally {
    if ($temporaryCreated) {
        try {
            Stop-InstalledProcesses
        } catch {
            $cleanupFailed = $true
            Write-Host 'windows-installer-smoke: owned process cleanup failed'
        }
        try {
            if (-not $uninstallationVerified -and (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
                $null = Assert-OwnedAppDataFixture
                $cleanupUninstallationAttempted = $true
                $null = Invoke-BoundedProcess -Executable $uninstaller `
                    -RawArguments "/S /currentuser _?=$installDirectory" `
                    -TimeoutSeconds 120 -Label 'CLEANUP_UNINSTALL'
                if ((Test-Path -LiteralPath $installedExecutable) -or
                    (Test-Path -LiteralPath $installedBackend)) {
                    throw 'WINDOWS_INSTALLER_SMOKE_CLEANUP_UNINSTALL_INCOMPLETE'
                }
                $null = Assert-OwnedAppDataFixture
                $cleanupUninstallationVerified = $true
            }
        } catch {
            $cleanupFailed = $true
            Write-Host 'windows-installer-smoke: cleanup uninstall failed'
        } finally {
            try {
                $ownedRoot = Get-Item -LiteralPath $temporaryRoot -ErrorAction SilentlyContinue
                if ($null -ne $ownedRoot) {
                    if ($ownedRoot.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                        throw 'WINDOWS_INSTALLER_SMOKE_UNSAFE_CLEANUP_DIRECTORY'
                    }
                    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
                }
            } catch {
                $cleanupFailed = $true
                Write-Host 'windows-installer-smoke: temporary cleanup failed'
            }
        }
    }
    if ($appDataFixtureCreated) {
        try {
            if ($null -ne (Get-Item -LiteralPath $appDataFixture -Force -ErrorAction SilentlyContinue)) {
                $null = Assert-OwnedAppDataFixture
                Remove-Item -LiteralPath $appDataFixture -Recurse -Force
                if ($null -ne (Get-Item -LiteralPath $appDataFixture -Force -ErrorAction SilentlyContinue)) {
                    throw 'WINDOWS_INSTALLER_SMOKE_APPDATA_CLEANUP_INCOMPLETE'
                }
            }
            $appDataCleanupVerified = $true
        } catch {
            $cleanupFailed = $true
            Write-Host 'windows-installer-smoke: owned app data cleanup refused or failed'
        }
    }
}

$finalReport = [ordered]@{
    passed = $null -eq $failureCode -and -not $cleanupFailed -and
        $installationVerified -and $installedDesktopCases -eq 10 -and
        $uninstallationVerified -and $appDataPreservationVerified -and $appDataCleanupVerified
    installation_verified = $installationVerified
    installed_desktop_cases = $installedDesktopCases
    default_uninstallation_attempted = $defaultUninstallationAttempted
    uninstallation_verified = $uninstallationVerified
    app_data_fixture_created = $appDataFixtureCreated
    app_data_path_kind = 'system_application_data'
    app_data_initial_sha256 = $appDataInitialHash
    app_data_preserved_sha256 = $appDataPreservedHash
    app_data_preservation_verified = $appDataPreservationVerified
    app_data_cleanup_verified = $appDataCleanupVerified
    cleanup_uninstallation_attempted = $cleanupUninstallationAttempted
    cleanup_uninstallation_verified = $cleanupUninstallationVerified
    cleanup_failed = $cleanupFailed
    synthetic_data_only = $true
}
if ($null -ne $failureCode) {
    $finalReport['failure_code'] = $failureCode
    $finalReport['failure_diagnostics'] = $failureDiagnostics
}
Write-Host ($finalReport | ConvertTo-Json -Compress)
if ($null -ne $failureCode) {
    throw $failureCode
}
if ($cleanupFailed) {
    throw 'WINDOWS_INSTALLER_SMOKE_CLEANUP_FAILED'
}
if (-not $finalReport['passed']) {
    throw 'WINDOWS_INSTALLER_SMOKE_VERIFICATION_INCOMPLETE'
}
