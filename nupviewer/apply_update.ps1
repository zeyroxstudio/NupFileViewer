$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'update.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$root = (Resolve-Path -LiteralPath $config.root).ProviderPath
$stage = (Resolve-Path -LiteralPath $PSScriptRoot).ProviderPath
if ((Split-Path -Parent $root) -ne (Split-Path -Parent $stage) -or
    (Split-Path -Leaf $stage) -notlike '.NupFileViewer-update-*' -or
    !(Test-Path -LiteralPath (Join-Path $root 'portable-version.txt') -PathType Leaf)) {
    throw 'Invalid update location.'
}
$allowed = @('NupFileViewer.exe', '_internal', 'licenses', 'source', 'README.md',
             'LICENSE', 'THIRD-PARTY-NOTICES.md', 'portable-version.txt')
if (($config.managed | Where-Object { $_ -cnotin $allowed }) -or
    @($config.managed | Sort-Object -Unique).Count -ne $allowed.Count) {
    throw 'Invalid update paths.'
}
$next = Join-Path $PSScriptRoot 'new'
$backup = Join-Path $PSScriptRoot 'backup'
$exe = Join-Path $root 'NupFileViewer.exe'
$saved = [Collections.Generic.List[string]]::new()
$installed = [Collections.Generic.List[string]]::new()
$committed = $false
$updated = $null
$ready = Join-Path $stage 'ready'

function Open-Viewer($ReadyPath) {
    $options = @{ FilePath = $exe; WorkingDirectory = $root }
    $arguments = @()
    if ($ReadyPath) { $arguments += @('--update-ready', ('"' + $ReadyPath + '"')) }
    if ($config.archive) { $arguments += '"' + $config.archive + '"' }
    if ($arguments.Count) { $options.ArgumentList = $arguments }
    Start-Process @options -PassThru
}

try {
    $process = Get-Process -Id $config.pid -ErrorAction SilentlyContinue
    if ($process -and !$process.WaitForExit(30000)) { throw 'NupFileViewer is still open.' }
    $active = Get-Process -Name NupFileViewer -ErrorAction SilentlyContinue | Where-Object Path -eq $exe
    if ($active) { throw 'Close the other NupFileViewer windows before updating.' }
    [IO.Directory]::CreateDirectory($backup) | Out-Null
    foreach ($name in $config.managed) {
        $target = Join-Path $root $name
        if (Test-Path -LiteralPath $target) {
            Move-Item -LiteralPath $target -Destination (Join-Path $backup $name)
            $saved.Add($name)
        }
        Move-Item -LiteralPath (Join-Path $next $name) -Destination $target
        $installed.Add($name)
    }
    $updated = Open-Viewer $ready
    $deadline = [DateTime]::UtcNow.AddSeconds(25)
    while (!(Test-Path -LiteralPath $ready) -and !$updated.HasExited -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 200
    }
    if (!(Test-Path -LiteralPath $ready)) {
        throw 'The updated application could not start.'
    }
    $committed = $true
} catch {
    $errorMessage = $_.Exception.Message
    try {
        if ($updated -and !$updated.HasExited) {
            Stop-Process -InputObject $updated
            $updated.WaitForExit()
        }
        foreach ($name in $installed) {
            Move-Item -LiteralPath (Join-Path $root $name) -Destination (Join-Path $next $name)
        }
        foreach ($name in $saved) {
            Move-Item -LiteralPath (Join-Path $backup $name) -Destination (Join-Path $root $name)
        }
    } catch {
        $errorMessage += "`nRollback: " + $_.Exception.Message
    }
    Set-Content -LiteralPath (Join-Path $root 'update-error.txt') -Value $errorMessage -Encoding UTF8
    if (!(Get-Process -Name NupFileViewer -ErrorAction SilentlyContinue | Where-Object Path -eq $exe)) {
        Open-Viewer | Out-Null
    }
}

if ($committed) {
    # These are fixed children of the private staging directory created by the app.
    foreach ($folder in @($backup, $next)) {
        if ([IO.Path]::GetFullPath($folder).StartsWith($PSScriptRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath (Join-Path $PSScriptRoot 'update.json'), $ready, $PSCommandPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PSScriptRoot -ErrorAction SilentlyContinue
}
