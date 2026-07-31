$ErrorActionPreference = "Stop"

$port = 7070
$existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Output "DuckDuckGo MCP is already running: http://127.0.0.1:$port/mcp"
    exit 0
}

$uvx = (Get-Command uvx -ErrorAction Stop).Source
$arguments = @(
    "--with", "duckduckgo-mcp-server[browser]",
    "duckduckgo-mcp-server",
    "--transport", "streamable-http",
    "--host", "127.0.0.1",
    "--port", "$port"
)
Start-Process -FilePath $uvx -ArgumentList $arguments -WindowStyle Hidden | Out-Null

for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        Write-Output "DuckDuckGo MCP started: http://127.0.0.1:$port/mcp"
        exit 0
    }
    Start-Sleep -Seconds 2
}

throw "DuckDuckGo MCP did not start within 120 seconds."
