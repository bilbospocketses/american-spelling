# The Windows twin of stall-watch.sh: runs a CI step's command and stops it only when it stops
# making progress, never for taking long. See stall-watch.sh for why, and for the rule both share.
#
#   pwsh -NoProfile -File .github/scripts/stall-watch.ps1 NAME COMMAND [ARGS...]
#
# It has no param() block on purpose: with one, PowerShell would bind the command's own switches
# (cmake's -B, -G, -D...) to this script's parameters. Every argument arrives untouched in $args.
# Unlike stall-watch.sh it keeps no log file: no Windows step needs one.
#
# Progress is a new line of output, or the command's own process tree doing at least
# STALL_IO_MIB MiB of I/O (default 4) or using at least STALL_CPU_S seconds of CPU (default 60)
# since the last line or the last such crossing. Unlike stall-watch.sh it does not count the
# runner's received bytes: a Windows runner receives about 40 KiB/s in the background (PR #64's
# Build step: 1.4 MiB in 35 s, downloading nothing), which would make a hang look like progress.
# A download still counts, as the bytes it writes: curl saving a file at 1 MB/s showed 1.2 MB of
# writes a second and almost no CPU. The bars sit far above what an idle process left in the
# tree accrues: an idle pwsh measured 0.17 s of CPU and 9.5 KiB of I/O in 120 s, so 1.7 s and
# 0.1 MiB in a 20-minute window, while any real compile passes 60 CPU-seconds in about a minute.
# A process that starts and ends between two polls is not seen; its parent's work still is.
# With none of the three for STALL_MIN minutes (default 20), the step has stalled: it prints a
# hang record (what was running, how long, when the last line came, the last lines and the
# process tree), kills the command's process tree and exits 124. Otherwise it exits with the command's own exit code. After the
# command exits, a process it left holding its output open with nothing new for STALL_DRAIN_S
# seconds (default 30) is named and killed. Either way it reports how long the step took and how
# CPU and I/O its processes used, and what the runner received, as a notice. For this script's
# own tests, STALL_S overrides STALL_MIN in seconds and STALL_POLL_S sets the poll interval.
$ErrorActionPreference = 'Stop'

$Name = if ($args.Count -gt 0) { "$($args[0])" } else { 'step' }
$Command = @($args | Select-Object -Skip 1 | ForEach-Object { "$_" })
if ($Command.Count -gt 0 -and $Command[0] -eq '--') { $Command = @($Command | Select-Object -Skip 1) }
if ($Command.Count -eq 0) {
    [Console]::Error.WriteLine('stall-watch.ps1: no command given')
    exit 2
}

$stallS = if ($env:STALL_S) { [double]$env:STALL_S } else { 60 * [double]($env:STALL_MIN ?? 20) }
$pollS = [double]($env:STALL_POLL_S ?? 5)
$drainS = [double]($env:STALL_DRAIN_S ?? 30)
$ioMib = [double]($env:STALL_IO_MIB ?? 4)
$ioStep = [long]($ioMib * 1MB)
$cpuStep = [double]($env:STALL_CPU_S ?? 60)

# What the runner received, for the notice only
function Get-RxBytes {
    $sum = 0L
    foreach ($nic in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
        if ($nic.NetworkInterfaceType -eq [System.Net.NetworkInformation.NetworkInterfaceType]::Loopback) { continue }
        try { $sum += $nic.GetIPStatistics().BytesReceived } catch { }
    }
    $sum
}
function Format-Span([double]$s) { '{0}m {1:00}s' -f [math]::Floor($s / 60), [math]::Floor($s % 60) }
function Format-Mib([long]$b) { '{0:0.0} MiB' -f ($b / 1MB) }
function Write-Line([string]$s) { [Console]::Out.WriteLine($s) }
# The command's process and every process under it, by parent id, which Windows keeps after the
# parent has exited
function Get-Tree([int]$root) {
    $all = @(Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, Name, CommandLine,
        KernelModeTime, UserModeTime, ReadTransferCount, WriteTransferCount, OtherTransferCount)
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    [void]$ids.Add($root)
    do {
        $grew = $false
        foreach ($p in $all) {
            if ($ids.Contains([int]$p.ParentProcessId) -and $ids.Add([int]$p.ProcessId)) { $grew = $true }
        }
    } while ($grew)
    $all | Where-Object { $ids.Contains([int]$_.ProcessId) -and $_.ProcessId -ne $PID }
}
function Show-Tree([int]$root) {
    foreach ($p in Get-Tree $root) {
        $cmd = "$($p.CommandLine)"
        if ($cmd.Length -gt 300) { $cmd = $cmd.Substring(0, 300) + '...' }
        Write-Line ('{0,7} ppid {1,7} {2}: {3}' -f $p.ProcessId, $p.ParentProcessId, $p.Name, $cmd)
    }
}

$exe = (Get-Command $Command[0] -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $exe) {
    [Console]::Error.WriteLine("stall-watch.ps1: $($Command[0]) is not a program on PATH")
    exit 127
}
$psi = [System.Diagnostics.ProcessStartInfo]::new($exe)
foreach ($a in @($Command | Select-Object -Skip 1)) { $psi.ArgumentList.Add($a) }
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
$psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8

$tail = [System.Collections.Generic.Queue[string]]::new()
$script:lastLine = [DateTime]::UtcNow

$start = [DateTime]::UtcNow
$rxPrev = Get-RxBytes
$rxTotal = 0L
# The tree's CPU seconds and I/O bytes, per process id, at the last poll
$seen = @{}
$cpuSince = 0.0
$ioSince = 0L
$cpuTotal = 0.0
$ioTotal = 0L
$probeFailed = $false
$proc = [System.Diagnostics.Process]::Start($psi)
$readers = @(
    @{ Stream = $proc.StandardOutput; Task = $proc.StandardOutput.ReadLineAsync(); Done = $false },
    @{ Stream = $proc.StandardError; Task = $proc.StandardError.ReadLineAsync(); Done = $false }
)
# Copies every line that has arrived; says whether either stream had one
function Read-Lines {
    $got = $false
    foreach ($r in $readers) {
        while (-not $r.Done -and $r.Task.IsCompleted) {
            $line = $r.Task.Result
            if ($null -eq $line) { $r.Done = $true; break }
            Write-Line $line
            $tail.Enqueue($line)
            if ($tail.Count -gt 40) { [void]$tail.Dequeue() }
            $script:lastLine = [DateTime]::UtcNow
            $got = $true
            $r.Task = $r.Stream.ReadLineAsync()
        }
    }
    $got
}
function Update-Rx {
    $now = Get-RxBytes
    $delta = $now - $script:rxPrev
    $script:rxPrev = $now
    # Only increases: an adapter that goes away takes its count out of the sum
    if ($delta -gt 0) { $script:rxTotal += $delta }
}
# Adds what each process in the command's tree did since the last poll (all of it, for a process
# seen for the first time). Says false when the process list could not be read: a probe that
# fails must never let a step be stopped, so the caller counts that poll as progress
function Update-Tree {
    try { $tree = @(Get-Tree $proc.Id) } catch { $script:probeFailed = $true; return $false }
    foreach ($p in $tree) {
        $id = [int]$p.ProcessId
        $cpu = ([double]$p.KernelModeTime + [double]$p.UserModeTime) / 1e7
        $io = [long]$p.ReadTransferCount + [long]$p.WriteTransferCount + [long]$p.OtherTransferCount
        $prev = $script:seen[$id]
        $dc = if ($prev) { $cpu - $prev[0] } else { $cpu }
        $di = if ($prev) { $io - $prev[1] } else { $io }
        if ($dc -gt 0) { $script:cpuSince += $dc; $script:cpuTotal += $dc }
        if ($di -gt 0) { $script:ioSince += $di; $script:ioTotal += $di }
        $script:seen[$id] = @($cpu, $io)
    }
    $true
}
function Format-Activity([double]$cpu, [long]$io) { '{0:0.0} s of CPU and {1}' -f $cpu, (Format-Mib $io) }

$progress = $start
$nextPoll = $start.AddSeconds($pollS)
$stalled = $false
while (-not $proc.HasExited) {
    [void](Read-Lines)
    $now = [DateTime]::UtcNow
    if ($now -ge $nextPoll) {
        $nextPoll = $now.AddSeconds($pollS)
        Update-Rx
        $probed = Update-Tree
        if ($script:lastLine -gt $progress) {
            $progress = $script:lastLine
            $cpuSince = 0.0
            $ioSince = 0L
        } elseif (-not $probed -or $cpuSince -ge $cpuStep -or $ioSince -ge $ioStep) {
            $progress = $now
            $cpuSince = 0.0
            $ioSince = 0L
        }
        $quiet = ($now - $progress).TotalSeconds
        if ($quiet -ge $stallS -and -not $proc.HasExited) {
            $stalled = $true
            $elapsed = ($now - $start).TotalSeconds
            Write-Line "::error title=$Name stalled::no output, and its processes used under $cpuStep s of CPU and $ioMib MiB of I/O, for $(Format-Span $quiet), after $(Format-Span $elapsed); stopped it. The hang record is in the step's log."
            Write-Line "===== HANG RECORD: $Name ====="
            Write-Line "running:     $($Command -join ' ')"
            Write-Line "elapsed:     $(Format-Span $elapsed)"
            Write-Line "last output: $(Format-Span ($now - $script:lastLine).TotalSeconds) ago, at $($script:lastLine.ToString('HH:mm:ss'))Z"
            Write-Line "activity:    $(Format-Activity $cpuSince $ioSince) since the last progress; $(Format-Activity $cpuTotal $ioTotal) in all"
            Write-Line '----- last 40 lines -----'
            foreach ($l in $tail) { Write-Line $l }
            Write-Line '----- process tree -----'
            Show-Tree $proc.Id
            Write-Line '===== END HANG RECORD ====='
            try { $proc.Kill($true) } catch { }
            break
        }
    }
    Start-Sleep -Milliseconds 100
}
$proc.WaitForExit(10000) | Out-Null

# Whatever the command left holding its output open, with nothing new for STALL_DRAIN_S seconds,
# is named and killed
$drained = [DateTime]::UtcNow
while (($readers | Where-Object { -not $_.Done }).Count -gt 0) {
    if (Read-Lines) { $drained = [DateTime]::UtcNow }
    if (([DateTime]::UtcNow - $drained).TotalSeconds -ge $drainS) {
        Write-Line "::notice title=$Name leftovers::the command ended, but these kept its output open for $($drainS)s with nothing new; killed them"
        try {
            $left = @(Get-Tree $proc.Id | Where-Object { $_.ProcessId -ne $proc.Id })
            Show-Tree $proc.Id
            foreach ($p in $left) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
        } catch {
            Write-Line "(the process list could not be read, so the leftovers were not killed: $($_.Exception.Message))"
        }
        break
    }
    Start-Sleep -Milliseconds 100
}
Update-Rx

$took = Format-Span ([DateTime]::UtcNow - $start).TotalSeconds
$used = "its processes used $(Format-Activity $cpuTotal $ioTotal); the runner received $(Format-Mib $rxTotal)"
if ($probeFailed) { $used += '; the process list could not always be read, and those polls counted as progress' }
if ($stalled) {
    Write-Line "::notice title=$Name duration::$Name stalled and was stopped after $took; $used"
    exit 124
}
$code = $proc.ExitCode
Write-Line "::notice title=$Name duration::$Name took $took (exit $code); $used"
exit $code
