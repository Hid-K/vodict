param([string]$Path)
$t = $null
$e = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$t, [ref]$e)
if (-not $e) {
    Write-Host "PARSE OK: $Path"
}
else {
    $e | ForEach-Object {
        "{0} @line {1}: {2}" -f $_.ErrorId, $_.Extent.StartLineNumber, $_.Message
    } | Select-Object -First 5
}

