param([string]$Path)
$enc = New-Object System.Text.UTF8Encoding($true)
$content = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
[System.IO.File]::WriteAllText($Path, $content, $enc)
Write-Host "BOM added: $Path"
