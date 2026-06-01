param(
    [string]$PrdPath,
    [string]$OutputRoot = "test-cases",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 4096,
    [string]$Model,
    [string]$Agent,
    [int]$TimeoutMinutes = 90,
    [switch]$KeepServer,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

function Resolve-InputPath {
    param(
        [Parameter(Mandatory = $true)][string]$PathText,
        [Parameter(Mandatory = $true)][string]$Workspace
    )

    $expanded = [Environment]::ExpandEnvironmentVariables($PathText.Trim('"'))
    if ([System.IO.Path]::IsPathRooted($expanded)) {
        return (Resolve-Path -LiteralPath $expanded).Path
    }

    $fromCurrent = Join-Path (Get-Location).Path $expanded
    if (Test-Path -LiteralPath $fromCurrent) {
        return (Resolve-Path -LiteralPath $fromCurrent).Path
    }

    $fromWorkspace = Join-Path $Workspace $expanded
    return (Resolve-Path -LiteralPath $fromWorkspace).Path
}

function Get-RelativeParentUnderPrd {
    param(
        [Parameter(Mandatory = $true)][string]$PrdFile,
        [Parameter(Mandatory = $true)][string]$Workspace
    )

    $prdRoot = [System.IO.Path]::GetFullPath((Join-Path $Workspace "prd"))
    if (-not $prdRoot.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $prdRoot = $prdRoot + [System.IO.Path]::DirectorySeparatorChar
    }

    $prdParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $PrdFile))
    if (-not $prdParent.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $prdParent = $prdParent + [System.IO.Path]::DirectorySeparatorChar
    }

    if ($prdParent.StartsWith($prdRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $prdParent.Substring($prdRoot.Length).TrimEnd("\", "/")
    }

    return ""
}

function Test-OpenCodeHealth {
    param([Parameter(Mandatory = $true)][string]$BaseUrl)

    try {
        $health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/global/health" -TimeoutSec 3
        return [bool]$health.healthy
    }
    catch {
        return $false
    }
}

function Invoke-OpenCodeApi {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [object]$Body,
        [int]$TimeoutSec = 60
    )

    $params = @{
        Method = $Method
        Uri = $Uri
        TimeoutSec = $TimeoutSec
    }

    if ($null -ne $Body) {
        $params.ContentType = "application/json; charset=utf-8"
        $params.Body = ($Body | ConvertTo-Json -Depth 32)
    }

    return Invoke-RestMethod @params
}

function Get-ShortText {
    param(
        [AllowNull()][object]$Value,
        [int]$MaxLength = 1200
    )

    if ($null -eq $Value) {
        return ""
    }

    $text = [string]$Value
    $text = $text -replace "`r`n", "`n"
    $text = $text.Trim()
    if ($text.Length -le $MaxLength) {
        return $text
    }

    return $text.Substring(0, $MaxLength) + "`n... <truncated>"
}

function Get-SessionStatusType {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [Parameter(Mandatory = $true)][string]$DirectoryQuery,
        [Parameter(Mandatory = $true)][string]$SessionID
    )

    try {
        $status = Invoke-OpenCodeApi -Method Get -Uri "$BaseUrl/session/status?directory=$DirectoryQuery" -TimeoutSec 10
        $sessionStatus = $status.PSObject.Properties[$SessionID]
        if ($null -eq $sessionStatus) {
            return "idle"
        }

        return [string]$sessionStatus.Value.type
    }
    catch {
        return "unknown"
    }
}

function Write-OpenCodePart {
    param([Parameter(Mandatory = $true)][object]$Part)

    switch ($Part.type) {
        "reasoning" {
            $text = Get-ShortText -Value $Part.text
            if (-not [string]::IsNullOrWhiteSpace($text)) {
                Write-Host ""
                Write-Host "[opencode:thinking]"
                Write-Host $text
            }
        }
        "text" {
            $text = Get-ShortText -Value $Part.text -MaxLength 2000
            if (-not [string]::IsNullOrWhiteSpace($text)) {
                Write-Host ""
                Write-Host "[opencode:message]"
                Write-Host $text
            }
        }
        "tool" {
            $toolName = [string]$Part.tool
            $status = [string]$Part.state.status
            $title = [string]$Part.state.title
            if ([string]::IsNullOrWhiteSpace($title)) {
                $title = [string]$Part.state.input.description
            }
            if ([string]::IsNullOrWhiteSpace($title)) {
                $title = [string]$Part.state.input.command
            }
            if ([string]::IsNullOrWhiteSpace($title)) {
                $title = "(no title)"
            }

            Write-Host ""
            Write-Host "[opencode:tool][$toolName][$status] $title"

            if ($Part.state.input.command) {
                $command = Get-ShortText -Value $Part.state.input.command -MaxLength 600
                Write-Host "command:"
                Write-Host $command
            }

            if ($status -eq "completed" -and $Part.state.output) {
                $output = Get-ShortText -Value $Part.state.output -MaxLength 1200
                if (-not [string]::IsNullOrWhiteSpace($output)) {
                    Write-Host "output:"
                    Write-Host $output
                }
            }
        }
        "file" {
            $path = [string]$Part.path
            if (-not [string]::IsNullOrWhiteSpace($path)) {
                Write-Host ""
                Write-Host "[opencode:file] $path"
            }
        }
        "patch" {
            Write-Host ""
            Write-Host "[opencode:patch]"
            if ($Part.files) {
                $Part.files | ForEach-Object { Write-Host "  $_" }
            }
        }
        "step-start" {
            Write-Host ""
            Write-Host "[opencode:step-start]"
        }
        "step-finish" {
            $reason = [string]$Part.reason
            Write-Host ""
            Write-Host "[opencode:step-finish] $reason"
        }
        default {
            if ($Part.type) {
                Write-Host ""
                Write-Host "[opencode:$($Part.type)]"
            }
        }
    }
}

function Watch-OpenCodeSession {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [Parameter(Mandatory = $true)][string]$DirectoryQuery,
        [Parameter(Mandatory = $true)][string]$SessionID,
        [Parameter(Mandatory = $true)][int]$TimeoutSec
    )

    $printed = @{}
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $idleSeen = $false

    while ((Get-Date) -lt $deadline) {
        $messages = Invoke-OpenCodeApi -Method Get -Uri "$BaseUrl/session/$SessionID/message?directory=$DirectoryQuery&limit=200" -TimeoutSec 30

        $orderedMessages = @($messages | Sort-Object {
            if ($_.info.time.created) { [Int64]$_.info.time.created } else { 0 }
        })

        foreach ($message in $orderedMessages) {
            if ($message.info.role -ne "assistant") {
                continue
            }

            foreach ($part in @($message.parts)) {
                if ($null -eq $part.id) {
                    continue
                }

                $status = ""
                if ($part.type -eq "tool" -and $part.state.status) {
                    $status = [string]$part.state.status
                }

                $key = "$($part.id):$status"
                if (-not $printed.ContainsKey($key)) {
                    Write-OpenCodePart -Part $part
                    $printed[$key] = $true
                }
            }
        }

        $statusType = Get-SessionStatusType -BaseUrl $BaseUrl -DirectoryQuery $DirectoryQuery -SessionID $SessionID
        if ($statusType -ne "busy") {
            if ($idleSeen) {
                return
            }
            $idleSeen = $true
        }
        else {
            $idleSeen = $false
        }

        Start-Sleep -Seconds 2
    }

    throw "Timed out while waiting for OpenCode generation after $TimeoutSec seconds."
}

function Start-OpenCodeServer {
    param(
        [Parameter(Mandatory = $true)][string]$Workspace,
        [Parameter(Mandatory = $true)][string]$HostName,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$BaseUrl
    )

    if (Test-OpenCodeHealth -BaseUrl $BaseUrl) {
        Write-Host "OpenCode server already available at $BaseUrl"
        return $null
    }

    $command = Get-Command opencode.cmd -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        $command = Get-Command opencode -ErrorAction Stop
    }

    $args = @("serve", "--hostname", $HostName, "--port", $Port)
    $process = Start-Process -FilePath $command.Source -ArgumentList $args -WorkingDirectory $Workspace -WindowStyle Hidden -PassThru

    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        if (Test-OpenCodeHealth -BaseUrl $BaseUrl) {
            Write-Host "Started OpenCode server at $BaseUrl"
            return $process
        }
    } while ((Get-Date) -lt $deadline)

    throw "OpenCode server did not become healthy within 30 seconds. Process ID: $($process.Id)"
}

function Stop-OpenCodeServer {
    param(
        [object]$Process,
        [Parameter(Mandatory = $true)][int]$Port
    )

    $processIds = @()
    if ($Process) {
        $processIds += $Process.Id
    }

    try {
        $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        $processIds += @($listeners | ForEach-Object { $_.OwningProcess })
    }
    catch {
        # Get-NetTCPConnection may be unavailable in older shells; the direct process is still stopped below.
    }

    $processIds |
        Where-Object { $_ } |
        Sort-Object -Unique |
        ForEach-Object {
            Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
        }
}

function New-TestcasePrompt {
    param(
        [Parameter(Mandatory = $true)][string]$PrdFile,
        [Parameter(Mandatory = $true)][string]$Scene,
        [Parameter(Mandatory = $true)][string]$OutputDir,
        [Parameter(Mandatory = $true)][string]$JsonFile,
        [Parameter(Mandatory = $true)][string]$MarkdownFile,
        [Parameter(Mandatory = $true)][string]$AppCardFile,
        [Parameter(Mandatory = $true)][string]$AgentPromptFile
    )

    return @"
Use the local opencode skill harmonyrun-testcase-gen to generate HarmonyRun black-box UI test cases from this PRD.

Input PRD:
$PrdFile

Output directory:
$OutputDir

Required output files:
- $JsonFile
- $MarkdownFile
- $AppCardFile
- $AgentPromptFile

Generation requirements:
1. Read the PRD from the input path and generate test cases for scene "$Scene".
2. Write all generated files into the output directory only.
3. The JSON file must be the HarmonyRun executable test suite input.
4. In suite.app_card use "file:./$([System.IO.Path]::GetFileName($AppCardFile))".
5. In suite.agent_prompt use "file:./$([System.IO.Path]::GetFileName($AgentPromptFile))".
6. If the output directory already exists, overwrite only the four required files for this scene.
7. Do not edit the PRD source file and do not write generated files outside the output directory.
8. Preserve Chinese text as UTF-8. If using PowerShell to read or write files, use -Encoding UTF8.
9. The JSON must be strict valid JSON: escape any double quotes inside string values, do not leave unterminated strings, and do not write mojibake text.
10. Before finishing, validate that the JSON is parseable and that suite plus test_cases exist. If validation fails, fix the file before completing.
"@
}

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $workspace

if ([string]::IsNullOrWhiteSpace($PrdPath)) {
    $PrdPath = Read-Host "Please input PRD file path"
}

$prdFile = Resolve-InputPath -PathText $PrdPath -Workspace $workspace
if ([System.IO.Path]::GetExtension($prdFile) -ne ".md") {
    throw "PRD file must be a markdown file: $prdFile"
}

$scene = [System.IO.Path]::GetFileNameWithoutExtension($prdFile)
$relativeParent = Get-RelativeParentUnderPrd -PrdFile $prdFile -Workspace $workspace
$suiteDirName = "$scene-test-suite"

if ([string]::IsNullOrWhiteSpace($relativeParent)) {
    $outputDir = Join-Path (Join-Path $workspace $OutputRoot) $suiteDirName
}
else {
    $outputDir = Join-Path (Join-Path (Join-Path $workspace $OutputRoot) $relativeParent) $suiteDirName
}

$jsonFile = Join-Path $outputDir "$scene-test-cases.json"
$markdownFile = Join-Path $outputDir "$scene-test-cases.md"
$appCardFile = Join-Path $outputDir "$scene-app-card.md"
$agentPromptFile = Join-Path $outputDir "$scene-agent-prompt.md"

$prompt = New-TestcasePrompt `
    -PrdFile $prdFile `
    -Scene $scene `
    -OutputDir $outputDir `
    -JsonFile $jsonFile `
    -MarkdownFile $markdownFile `
    -AppCardFile $appCardFile `
    -AgentPromptFile $agentPromptFile

Write-Host "Workspace: $workspace"
Write-Host "PRD: $prdFile"
Write-Host "Scene: $scene"
Write-Host "Output: $outputDir"

if ($DryRun) {
    Write-Host ""
    Write-Host "Dry run only. Prompt that would be sent to OpenCode:"
    Write-Host "------------------------------------------------------"
    Write-Host $prompt
    exit 0
}

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

$baseUrl = "http://${HostName}:$Port"
$serverProcess = $null

try {
    $serverProcess = Start-OpenCodeServer -Workspace $workspace -HostName $HostName -Port $Port -BaseUrl $baseUrl

    $directoryQuery = [System.Uri]::EscapeDataString($workspace)
    $sessionBody = @{
        title = "Generate HarmonyRun test cases for $scene"
    }
    $session = Invoke-OpenCodeApi -Method Post -Uri "$baseUrl/session?directory=$directoryQuery" -Body $sessionBody
    Write-Host "OpenCode session: $($session.id)"

    $messageBody = @{
        parts = @(
            @{
                type = "text"
                text = $prompt
            }
        )
    }

    if (-not [string]::IsNullOrWhiteSpace($Model)) {
        $modelParts = $Model -split "/", 2
        if ($modelParts.Count -ne 2) {
            throw "Model must use provider/model format, for example: bailian-coding-plan/qwen3-coder-plus"
        }
        $messageBody.model = @{
            providerID = $modelParts[0]
            modelID = $modelParts[1]
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($Agent)) {
        $messageBody.agent = $Agent
    }

    $timeoutSec = [Math]::Max(60, $TimeoutMinutes * 60)
    Write-Host "Sending testcase generation prompt to OpenCode..."
    Invoke-OpenCodeApi -Method Post -Uri "$baseUrl/session/$($session.id)/prompt_async?directory=$directoryQuery" -Body $messageBody -TimeoutSec 60 | Out-Null
    Write-Host "OpenCode is generating files. Live log follows; this can take several minutes."
    Watch-OpenCodeSession -BaseUrl $baseUrl -DirectoryQuery $directoryQuery -SessionID $session.id -TimeoutSec $timeoutSec
    Write-Host "OpenCode generation request completed. Validating output files..."

    if (-not (Test-Path -LiteralPath $jsonFile)) {
        throw "Generation finished but expected JSON was not found: $jsonFile"
    }

    try {
        $json = Get-Content -Raw -Encoding UTF8 -LiteralPath $jsonFile | ConvertFrom-Json
    }
    catch {
        throw "Generated JSON is not parseable: $jsonFile. Reason: $($_.Exception.Message)"
    }
    if ($null -eq $json.suite -or $null -eq $json.test_cases) {
        throw "Generated JSON is missing required fields: suite/test_cases"
    }

    Write-Host "Generated HarmonyRun test suite:"
    Write-Host "  $jsonFile"
    Write-Host "  $markdownFile"
    Write-Host "  $appCardFile"
    Write-Host "  $agentPromptFile"
}
finally {
    if ($serverProcess -and -not $KeepServer) {
        Stop-OpenCodeServer -Process $serverProcess -Port $Port
        Write-Host "Stopped OpenCode server process $($serverProcess.Id)"
    }
}
