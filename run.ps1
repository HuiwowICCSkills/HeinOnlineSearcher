# Locate a Python that can run this skill, then hand off to
# scripts/heinonline_search.py.  beautifulsoup4 is the only Python dependency;
# PDF text extraction belongs to the caller, not to this skill.
$heinCandidates = @(
    (Join-Path $PSScriptRoot 'venv\Scripts\python.exe'),
    (Join-Path $PSScriptRoot 'venv/bin/python'),
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'),
    (Join-Path $env:USERPROFILE '.workbuddy\binaries\python\versions\3.13.12\python.exe')
)
$heinPathPython = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($heinPathPython) { $heinCandidates += $heinPathPython }
$heinPathPython3 = (Get-Command python3 -ErrorAction SilentlyContinue).Source
if ($heinPathPython3) { $heinCandidates += $heinPathPython3 }

$heinPython = $null
foreach ($heinCandidate in $heinCandidates) {
    if (-not $heinCandidate -or -not (Test-Path -LiteralPath $heinCandidate -PathType Leaf)) { continue }
    & $heinCandidate -c 'import bs4' 2>$null
    if ($LASTEXITCODE -eq 0) { $heinPython = $heinCandidate; break }
}
if (-not $heinPython) {
    throw 'No Python with beautifulsoup4 was found. Run: python -m pip install -r requirements.txt (or create venv/).'
}
$heinArguments = foreach ($heinArgument in $args) {
    if ($heinArgument -is [array]) { $heinArgument -join ',' } else { $heinArgument }
}
& $heinPython -X utf8 (Join-Path $PSScriptRoot 'scripts\heinonline_search.py') $heinArguments
exit $LASTEXITCODE
