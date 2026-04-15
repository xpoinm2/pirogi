Get-Content .\codex.patch |
  Where-Object { $_ -notmatch '^\s*\(cd ' -and $_ -notmatch '^EOF\s*$' -and $_ -notmatch '^\)\s*$' } |
  Set-Content .\fix.patch -Encoding utf8