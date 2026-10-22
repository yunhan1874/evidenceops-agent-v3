param(
  [string]$BaseUrl = "http://localhost:8000",
  [string]$DevUser = "demo-user"
)

$ErrorActionPreference = "Stop"

$base = $BaseUrl.TrimEnd("/")
$headers = @{
  "X-Dev-User" = $DevUser
  "Content-Type" = "application/json; charset=utf-8"
}

function PostJson($Path, $Body) {
  $json = $Body | ConvertTo-Json -Compress -Depth 20
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
  Invoke-RestMethod -Uri ($base + $Path) -Method POST -Headers $headers -Body $bytes
}

function GetJson($Path) {
  Invoke-RestMethod -Uri ($base + $Path) -Method GET -Headers @{ "X-Dev-User" = $DevUser }
}

function DeleteJson($Path) {
  Invoke-RestMethod -Uri ($base + $Path) -Method DELETE -Headers @{ "X-Dev-User" = $DevUser }
}

$live = GetJson "/health/live"
$ready = GetJson "/health/ready"

$session = PostJson "/api/v2/sessions" @{ title = "powershell-legacy-feature-test" }

$firstRun = PostJson "/api/v2/agent/runs" @{
  session_id = $session.id
  query = "What is the company purchase policy?"
}

$secondRun = PostJson "/api/v2/agent/runs" @{
  session_id = $session.id
  query = "What is the company reimbursement policy?"
}

$historyBefore = GetJson "/api/v2/sessions/$($session.id)/history"
if ($historyBefore.total -ne 2) {
  throw "History check failed: expected 2 items, got $($historyBefore.total)"
}

$deleteMessage = DeleteJson "/api/v2/sessions/$($session.id)/messages/$($firstRun.run_id)"
if ($deleteMessage.status -ne "deleted") {
  throw "Message delete failed: status is $($deleteMessage.status)"
}

$historyAfter = GetJson "/api/v2/sessions/$($session.id)/history"
if ($historyAfter.total -ne 1) {
  throw "History filter failed after message delete: expected 1 item, got $($historyAfter.total)"
}

$deleteByRun = DeleteJson "/api/v2/agent/runs/$($secondRun.run_id)"
if ($deleteByRun.status -ne "deleted") {
  throw "Run delete failed: status is $($deleteByRun.status)"
}

$historyEmpty = GetJson "/api/v2/sessions/$($session.id)/history"
if ($historyEmpty.total -ne 0) {
  throw "History filter failed after deleting both messages: expected 0 items, got $($historyEmpty.total)"
}

Invoke-RestMethod -Uri ($base + "/api/v2/sessions/$($session.id)") -Method DELETE -Headers @{ "X-Dev-User" = $DevUser }

try {
  GetJson "/api/v2/sessions/$($session.id)/history" | Out-Null
  throw "Session history is still readable after deleting the session"
} catch {
  if ($_.Exception.Response.StatusCode.value__ -ne 404) {
    throw
  }
}

@{
  live = $live.status
  ready = $ready.status
  session_id = $session.id
  deleted_message_by_session = $firstRun.run_id
  deleted_message_by_run = $secondRun.run_id
  history_before_delete = $historyBefore.total
  history_after_first_delete = $historyAfter.total
  history_after_second_delete = $historyEmpty.total
  deleted_session_history_status = 404
  result = "ok"
} | ConvertTo-Json -Depth 20
