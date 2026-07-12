<#
.SYNOPSIS
    contour installer (Windows) — sets up the local-first proactive multimodal assistant.
    Auto-installs its prerequisites (Python, Ollama, Hermes) if missing.
    Idempotent; safe to re-run.

.DESCRIPTION
    Steps (target: install -> first voice Q&A in under 5 minutes):
      0. Install prerequisites if missing: Python 3.10+, Ollama, Hermes Agent
         (winget-first, direct-download fallback). Self-elevates for UAC if needed.
      1. Verify Python 3.10+.
      2. Ensure Ollama is installed/running; pull the vision + embedding models.
      3. Install contour Python dependencies.
      4. Verify Hermes; configure voice (local Whisper STT in; ElevenLabs TTS out via our
         `speak` tool — Hermes' own TTS is disabled to avoid double audio).
      5. Register the contour MCP server + drop the skill into Hermes.
      6. Point Hermes' web_search at the relay (zero-key search).
      7. Wire the device token + relay URL; set the ask_cloud opt-in.
      8. Confirm screen/mic permission.
      9. Start the watcher and print a first-run hint.

.PARAMETER RelayUrl      Base URL of the deployed relay (search + opt-in cloud).
.PARAMETER DeviceToken   Per-device token issued by the relay.
.PARAMETER AskCloud      Opt in to text-only cloud escalation (default: off).
.PARAMETER VisionModel   Ollama vision model tag (default gemma4:e4b; 12b = upgrade).
.PARAMETER SkipPrereqs   Skip the auto-install phase (assume prereqs already present).
#>
[CmdletBinding()]
param(
    [string]$RelayUrl = $env:CONTOUR_RELAY_URL,
    [string]$DeviceToken = $env:CONTOUR_DEVICE_TOKEN,
    [switch]$AskCloud,
    [string]$VisionModel = "gemma4:e4b",
    [string]$EmbedModel = "nomic-embed-text",
    [switch]$SkipPrereqs
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimePython = ""
$HermesDir = Join-Path $env:LOCALAPPDATA "hermes"
$HermesConfig = Join-Path $HermesDir "config.yaml"
$DataDir = Join-Path $HermesDir "contour"

function Step($n, $msg) { Write-Host "`n[$n] $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }
function Ok($msg) { Write-Host "  + $msg" -ForegroundColor Green }
function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Test-ProjectRoot($root) {
    return (Test-Path (Join-Path $root "pyproject.toml")) -and (Test-Path (Join-Path $root "watcher"))
}

function Ensure-ProjectRoot {
    if (Test-ProjectRoot $ProjectRoot) { return }
    if (-not $RelayUrl) {
        throw "Installer downloaded without project files and no -RelayUrl provided."
    }
    Step 0 "Downloading contour client bundle"
    $target = Join-Path $env:LOCALAPPDATA "contour\client"
    $zipPath = Join-Path $env:TEMP "contour-client.zip"
    Invoke-WebRequest -Uri "$RelayUrl/download/client.zip" -OutFile $zipPath -UseBasicParsing
    if (Test-Path $target) { Remove-Item -Recurse -Force $target }
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Expand-Archive -Path $zipPath -DestinationPath $target -Force
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    $script:ProjectRoot = $target
    Ok "Client bundle extracted to $ProjectRoot"
}

function Ensure-Uv {
    if (Have "uv") { return }
    Warn "uv not found; installing via winget"
    if (-not (Install-Winget "astral-sh.uv" "uv")) {
        Warn "Could not install uv automatically; will use pip fallback."
    }
}

# --- prerequisite helpers ----------------------------------------------------
function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

# Re-read Machine + User PATH into this process so freshly installed tools
# resolve without opening a new shell.
function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ";"
}

function Install-Winget($id, $name) {
    if (-not (Have "winget")) { return $false }
    Write-Host "  installing $name via winget ($id)..."
    try {
        winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
        Update-SessionPath
        return $true
    } catch {
        Warn "winget install of $name failed: $($_.Exception.Message)"
        return $false
    }
}

function Install-Direct($url, $file, $silentArgs) {
    $dest = Join-Path $env:TEMP $file
    Write-Host "  downloading $file..."
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    Write-Host "  running $file (silent)..."
    $p = Start-Process -FilePath $dest -ArgumentList $silentArgs -Wait -PassThru
    Update-SessionPath
    return ($p.ExitCode -eq 0)
}

# --- self-elevation ----------------------------------------------------------
# Installing Python/Ollama and registering the watcher scheduled task want admin.
if (-not $SkipPrereqs -and -not (Test-Admin)) {
    Warn "Elevating to install prerequisites (a UAC prompt will appear)..."
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($RelayUrl)    { $argList += @("-RelayUrl", $RelayUrl) }
    if ($DeviceToken) { $argList += @("-DeviceToken", $DeviceToken) }
    if ($AskCloud)    { $argList += "-AskCloud" }
    if ($VisionModel) { $argList += @("-VisionModel", $VisionModel) }
    if ($EmbedModel)  { $argList += @("-EmbedModel", $EmbedModel) }
    try {
        Start-Process powershell.exe -Verb RunAs -ArgumentList $argList
        Warn "Continued in an elevated window. You can close this one."
        exit 0
    } catch {
        Warn "Elevation declined; continuing unelevated. Some installs may fail - re-run as admin, or use -SkipPrereqs after installing prerequisites manually."
    }
}

Ensure-ProjectRoot

# 0. Prerequisites ------------------------------------------------------------
if (-not $SkipPrereqs) {
    Step 0 "Installing prerequisites (Python, Ollama, Hermes) if missing"

    if (-not (Have "python")) {
        if (-not (Install-Winget "Python.Python.3.12" "Python 3.12")) {
            try {
                Install-Direct "https://www.python.org/ftp/python/3.12.6/python-3.12.6-amd64.exe" `
                    "python-3.12.6-amd64.exe" "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1" | Out-Null
            } catch { Warn "Direct Python install failed: $($_.Exception.Message)" }
        }
    }
    if (Have "python") { Ok "Python available" } else { Warn "Python still not on PATH." }

    if (-not (Have "ollama")) {
        if (-not (Install-Winget "Ollama.Ollama" "Ollama")) {
            try {
                Install-Direct "https://ollama.com/download/OllamaSetup.exe" "OllamaSetup.exe" "/SILENT" | Out-Null
            } catch { Warn "Direct Ollama install failed: $($_.Exception.Message)" }
        }
    }
    if (Have "ollama") { Ok "Ollama available" } else { Warn "Ollama still not on PATH." }

    if (-not (Have "hermes")) {
        Write-Host "  installing Hermes Agent..."
        try {
            Invoke-Expression (Invoke-RestMethod https://hermes-agent.nousresearch.com/install.ps1)
            Update-SessionPath
        } catch { Warn "Hermes install failed: $($_.Exception.Message)" }
    }
    if (Have "hermes") { Ok "Hermes available" } else { Warn "Hermes still not on PATH." }
}

# 1. Python -------------------------------------------------------------------
Step 1 "Checking Python 3.10+"
if (-not (Have "python")) { throw "Python 3.10+ is required and was not found on PATH (open a new shell, or install manually and re-run with -SkipPrereqs)." }
$pyver = (python -c "import sys;print('%d.%d'%sys.version_info[:2])").Trim()
Ok "Python $pyver"
$Python = (Get-Command python).Source
$RuntimePython = $Python

# 2. Ollama + models ----------------------------------------------------------
Step 2 "Ensuring Ollama + local models"
if (-not (Have "ollama")) {
    Warn "Ollama not found. Install from https://ollama.com/download then re-run."
    Warn "Continuing so the rest can be configured, but capture/Q&A need Ollama."
} else {
    if (-not (Get-Process ollama -ErrorAction SilentlyContinue)) {
        Write-Host "  starting Ollama in the background..."
        try { Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden -ErrorAction Stop } catch {}
        Start-Sleep -Seconds 3
    }
    try { ollama list *> $null } catch { Warn "Ollama not responding yet; start the Ollama app if pulls fail." }
    Write-Host "  pulling $VisionModel (this can take a while the first time)..."
    ollama pull $VisionModel
    ollama pull $EmbedModel
    Ok "Models ready ($VisionModel, $EmbedModel). Tip: gemma4:12b is an optional upgrade."
}

# 3. contour dependencies ---------------------------------------------------------
Step 3 "Installing contour Python dependencies"
Ensure-Uv
if (Have "uv") {
    Push-Location $ProjectRoot; uv sync; Pop-Location
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) { $RuntimePython = $venvPython }
} else {
    Warn "uv not found; falling back to pip."
    python -m pip install --user -e $ProjectRoot
}
Ok "Dependencies installed"

# 4. Hermes + voice -----------------------------------------------------------
Step 4 "Verifying Hermes Agent + configuring voice"
if (-not (Have "hermes")) {
    Warn "Hermes not found. Install it first: iex (irm https://hermes-agent.nousresearch.com/install.ps1)"
} else {
    try { hermes doctor *> $null; Ok "Hermes present" } catch { Warn "hermes doctor reported issues." }
    Ok "Voice input: Hermes local Whisper STT (no key). Voice output: our ElevenLabs 'speak' tool."
    Warn "The installer disables Hermes' native TTS (step 5) so you don't get double audio."
    Warn "Install Hermes voice extras + system deps (portaudio/ffmpeg) if STT isn't working."
}

# 5. Register MCP server + skill ---------------------------------------------
Step 5 "Registering contour MCP server + skill into Hermes"
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$askCloudStr = if ($AskCloud) { "true" } else { "false" }
if (Have "uv") {
    Push-Location $ProjectRoot
    uv run --with pyyaml python (Join-Path $ProjectRoot "install\register_hermes.py") `
        --config $HermesConfig --python $RuntimePython `
        --relay-url $RelayUrl --token $DeviceToken `
        --data-dir $DataDir --ask-cloud $askCloudStr --enable-voice true
    Pop-Location
} else {
    python -m pip install --user --quiet pyyaml
    python (Join-Path $ProjectRoot "install\register_hermes.py") `
        --config $HermesConfig --python $RuntimePython `
        --relay-url $RelayUrl --token $DeviceToken `
        --data-dir $DataDir --ask-cloud $askCloudStr --enable-voice true
}

$skillsDir = Join-Path $HermesDir "skills\contour-activity"
New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null
Copy-Item (Join-Path $ProjectRoot "skill\SKILL.md") $skillsDir -Force
Ok "MCP server registered; skill copied to $skillsDir"

# 6. Zero-key web search ------------------------------------------------------
Step 6 "Zero-key web search"
if ($RelayUrl) {
    Warn "Point Hermes' web_search provider base URL at $RelayUrl/search to keep search key-free."
    Warn "If Hermes doesn't accept a custom search endpoint, enable the fallback web_search MCP tool."
} else {
    Warn "No RelayUrl given; web search + ask_cloud stay disabled until you pass -RelayUrl / -DeviceToken."
}

# 7. Token / opt-in -----------------------------------------------------------
Step 7 "Device token + cloud opt-in"
if ($DeviceToken) { Ok "Device token wired into the MCP env block." }
else { Warn "No DeviceToken; relay-backed features are inactive." }
if ($AskCloud) { Ok "ask_cloud ENABLED (text-only, PII-scrubbed, off by default elsewhere)." }
else { Ok "ask_cloud disabled (default). Re-run with -AskCloud to enable." }

# 8. Permissions --------------------------------------------------------------
Step 8 "Screen + microphone permission"
Warn "Grant microphone access: Settings > Privacy & security > Microphone."
Warn "Screen capture is read locally by the watcher; nothing raw ever leaves the device."

# 9. Start the watcher --------------------------------------------------------
Step 9 "Starting the watcher (background)"
$taskName = "contour-watcher"
$action = New-ScheduledTaskAction -Execute $RuntimePython -Argument "-m watcher.daemon" -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Force `
        -Description "contour activity watcher" | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Ok "Watcher scheduled task '$taskName' created and started."
} catch {
    Warn "Could not register a scheduled task ($($_.Exception.Message)). Start manually:"
    Warn "  cd `"$ProjectRoot`"; $RuntimePython -m watcher.daemon"
}

Write-Host "`nDone. Try saying to Hermes: " -NoNewline
Write-Host "`"what was I just doing?`"" -ForegroundColor Green
Write-Host "Reload MCP inside Hermes with /reload-mcp if it's already running.`n"
