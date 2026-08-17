param(
    [Parameter(Mandatory = $true)][string]$Manifest,
    [string]$CrawlerRoot = "E:\豆包创作线Agent\MediaCrawler",
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [int]$BatchSize = 20,
    [int]$MaxComments = 100,
    [int]$CrawlerConcurrency = 3,
    [int]$MaxAttempts = 2,
    [ValidateSet("yes", "no")][string]$Headless = "yes"
)

$ErrorActionPreference = "Stop"
$logRoot = Join-Path $OutputRoot "logs"
$dataRoot = Join-Path $OutputRoot "data"
New-Item -ItemType Directory -Force $OutputRoot, $logRoot, $dataRoot | Out-Null

$selected = @(Import-Csv -LiteralPath $Manifest)
$total = $selected.Count
$batchCount = [math]::Ceiling($total / [double]$BatchSize)
$failures = @()
Write-Output "SCAN_START videos=$total batches=$batchCount batch_size=$BatchSize concurrency=$CrawlerConcurrency"

for ($offset = 0; $offset -lt $total; $offset += $BatchSize) {
    $batch = @($selected | Select-Object -Skip $offset -First $BatchSize)
    $batchNo = [int]($offset / $BatchSize) + 1
    $ids = ($batch | ForEach-Object {
        $id = ([string]$_.aweme_id).Trim()
        if ([string]::IsNullOrWhiteSpace($id)) { $id = ([string]$_.video_url).Trim() }
        $id
    }) -join ","
    $done = [math]::Min($offset + $batch.Count, $total)
    $succeeded = $false

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $logPath = Join-Path $logRoot ("batch-{0:D3}-attempt-{1}.log" -f $batchNo, $attempt)
        $batchData = Join-Path $dataRoot ("batch-{0:D3}-attempt-{1}" -f $batchNo, $attempt)
        New-Item -ItemType Directory -Force $batchData | Out-Null
        Write-Output "BATCH_START number=$batchNo/$batchCount attempt=$attempt range=$($offset + 1)-$done"

        Push-Location $CrawlerRoot
        try {
            $savedPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            & uv run main.py `
                --platform dy `
                --lt qrcode `
                --type detail `
                --specified_id $ids `
                --get_comment yes `
                --get_sub_comment no `
                --max_comments_count_singlenotes $MaxComments `
                --save_data_option jsonl `
                --save_data_path $batchData `
                --max_concurrency_num $CrawlerConcurrency `
                --headless $Headless *>&1 | Tee-Object -FilePath $logPath
            $exitCode = $LASTEXITCODE
            $ErrorActionPreference = $savedPreference
        } finally {
            Pop-Location
        }

        if ($exitCode -eq 0) {
            $succeeded = $true
            Write-Output "BATCH_DONE number=$batchNo/$batchCount completed=$done/$total attempt=$attempt"
            break
        }
        Write-Output "BATCH_RETRY number=$batchNo/$batchCount exit=$exitCode attempt=$attempt log=$logPath"
    }

    if (-not $succeeded) {
        $failures += $batchNo
        Write-Output "BATCH_FAILED number=$batchNo/$batchCount after_attempts=$MaxAttempts"
    }
}

$failureText = ($failures -join ",")
Write-Output "SCAN_DONE videos=$total failed_batches=$($failures.Count) failures=$failureText data=$dataRoot logs=$logRoot"
if ($failures.Count -gt 0) { exit 2 }
