param(
    [Parameter(Mandatory=$true)][string]$PackageDirectory,
    [Parameter(Mandatory=$true)][string]$LogDirectory
)
$ErrorActionPreference = 'Stop'
$logs = (Resolve-Path $LogDirectory).Path
$localData = $null
function Get-ComparablePath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { throw 'Missing evidence path' }
    if ($Value.StartsWith('\\?\UNC\')) { $Value = '\\' + $Value.Substring(8) }
    elseif ($Value.StartsWith('\\?\')) { $Value = $Value.Substring(4) }
    return [IO.Path]::GetFullPath($Value).TrimEnd('\').ToLowerInvariant()
}
function Assert-ContainsBounds($Outer, $Inner) {
    if ($Outer.Count -ne 4 -or $Inner.Count -ne 4 -or
        $Inner[2] -le 0 -or $Inner[3] -le 0 -or
        $Inner[0] -lt $Outer[0] -or $Inner[1] -lt $Outer[1] -or
        ($Inner[0] + $Inner[2]) -gt ($Outer[0] + $Outer[2]) -or
        ($Inner[1] + $Inner[3]) -gt ($Outer[1] + $Outer[3])) { throw 'Color panel clipped or invalid bounds' }
}
function Assert-TitleGeometry($Title, $CanvasSize, [string]$Alignment) {
    $scale = [math]::Min($CanvasSize[0] / 1920.0, $CanvasSize[1] / 1080.0)
    $box = $Title.bbox
    $coords = $Title.coords
    $centered = $Alignment -eq 'centralizado'
    $expectedX = 88 * $scale
    $anchor = 'nw'; $justify = 'left'
    if ($centered) { $expectedX = 960 * $scale; $anchor = 'n'; $justify = 'center' }
    if ($box.Count -ne 4 -or $coords.Count -ne 2 -or
        $Title.anchor -ne $anchor -or $Title.justify -ne $justify -or
        [math]::Abs($coords[0] - $expectedX) -ge 0.01 -or
        [math]::Abs(($box[1] + $box[3])/2.0 - 99*$scale) -gt 0.51 -or
        [math]::Abs($coords[1] - $box[1]) -gt 1 -or
        $box[0] -lt 88*$scale-2 -or $box[2] -gt 1832*$scale+2 -or
        $box[3] -lt $box[1] -or $box[2] -lt $box[0] -or
        $box[1] -lt -1 -or $box[3] -gt 198*$scale+1 -or
        ($box[3]-$box[1]) -gt 122*$scale+2) { throw 'Actual title bbox is not centered above the divider' }
}
function Assert-Interface($Interface, [string]$LogRoot) {
    if ($null -eq $Interface -or $Interface.schema_version -ne 1 -or $Interface.cases.Count -ne 4) {
        throw 'Missing native interface DPI/scrolling evidence'
    }
    $seen = @{}
    foreach ($row in $Interface.cases) {
        $key = "$($row.scale)-$($row.size[0])"
        if ($seen.ContainsKey($key) -or $row.scale -notin @(100,150) -or $row.size[0] -notin @(1024,1366) -or
            $row.size[1] -ne 768 -or [math]::Abs($row.tk_scaling - (4.0/3 * $row.scale/100)) -ge 0.04 -or
            $row.new_manual_seconds -ne 60 -or $row.legacy_seconds -ne 15 -or
            $row.music_manual_seconds -ne 37.25 -or $row.music_mode -ne 'musica' -or
            ($row.format_values -join ',') -ne 'MP4,AVI' -or $row.format_readonly -ne $true -or
            ($row.alignment_values -join ',') -ne 'esquerda,centralizado' -or
            $row.background -ne '#252525' -or $row.field -ne '#1e1e1e' -or $row.focus -ne '#78b8ed' -or
            $row.views.Count -ne 4) { throw 'Native interface defaults/geometry/controls differ' }
        if (($row.scene_headings -join '|') -ne 'Nome da cena|Arquivo de vídeo' -or
            $row.alignment_probes.Count -ne 2) { throw 'Missing current scene headings or title geometry' }
        foreach ($probe in $row.alignment_probes) {
            Assert-TitleGeometry $probe.title $probe.canvas_size $probe.alignment
        }
        $expectedButtons = @('Novo projeto','Abrir projeto…','Salvar projeto como…',
            'Adicionar vídeos…','Remover','Subir','Descer','Aplicar nome e trecho',
            'Escolher cor…','Escolher cor…','Escolher cor…','Escolher música…','Retirar música',
            'Aplicar duração','Exportar vídeo (1080p)…','Abrir último vídeo','Abrir pasta do vídeo','Cancelar')
        if ((@($row.button_measures | ForEach-Object { $_.text } | Sort-Object) -join '|') -ne
            (@($expectedButtons | Sort-Object) -join '|')) { throw 'Missing native button measurements' }
        foreach ($measure in $row.button_measures) {
            if ($measure.requested_size.Count -ne 2 -or $measure.text_width -le 0 -or
                $measure.bounds[2] -lt $measure.requested_size[0] -or
                $measure.bounds[3] -lt $measure.requested_size[1] -or
                $measure.text_width -gt $measure.bounds[2] -or
                $measure.bounds[0] -lt $row.button_window[0] -or
                ($measure.bounds[0]+$measure.bounds[2]) -gt ($row.button_window[0]+$row.button_window[2])) {
                throw 'Native button text or requested size is clipped'
            }
            Assert-ContainsBounds $measure.parent $measure.bounds
        }
        $seen[$key] = $true
        foreach ($flag in @('selection_preserved','pending_preserved','scrollable','time_columns_aligned','external_disabled','disabled_legible')) {
            if ($row.$flag -ne $true) { throw "Native interface missing $flag" }
        }
        $groups = @{}
        foreach ($view in $row.views) {
            if ($view.visible -ne $true -or $groups.ContainsKey($view.group) -or
                $view.group -notin @('scenes','visual','audio','export') -or $view.controls.Count -lt 1) {
                throw 'Native interface view missing or duplicated'
            }
            $groups[$view.group] = $true
            Assert-ContainsBounds $view.window $view.viewport
            Assert-ContainsBounds $view.viewport $view.target
            foreach ($control in $view.controls) { Assert-ContainsBounds $view.viewport $control }
            foreach ($control in @($view.cancel,$view.progress,$view.status)) { Assert-ContainsBounds $view.window $control }
            $shot = $view.capture
            if ($shot.method -ne 'Win32.GDI.BitBlt' -or $shot.width -ne $row.size[0] -or $shot.height -ne 768 -or
                $shot.sha256 -notmatch '^[0-9a-f]{64}$' -or -not (Test-Path -LiteralPath $shot.path)) {
                throw 'Missing actual native interface screenshot'
            }
            $shotPath = Get-ComparablePath $shot.path
            $logPath = Get-ComparablePath $LogRoot
            if (-not $shotPath.StartsWith($logPath + '\')) { throw 'Interface capture outside evidence directory' }
            if ((Get-FileHash -LiteralPath $shot.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $shot.sha256) {
                throw 'Native interface screenshot hash mismatch'
            }
            Add-Type -AssemblyName System.Drawing
            $bitmap = [Drawing.Bitmap]::FromFile($shot.path)
            try {
                if ($bitmap.Width -ne $shot.width -or $bitmap.Height -ne $shot.height) { throw 'Screenshot dimensions differ' }
            } finally { $bitmap.Dispose() }
        }
    }
}
function Assert-ColorPreview($Color, [string]$LogRoot) {
    if ($null -eq $Color -or $Color.schema_version -ne 1 -or
        $null -eq $Color.media_calls -or $Color.media_calls -ne 0 -or
        $Color.state_preserved -ne $true -or $Color.cancel_preserved -ne $true -or
        $Color.mutation_checks.Count -ne 6 -or ($Color.mutation_checks -contains $false) -or
        ($Color.mutation_checks -contains $null) -or $Color.probes.Count -ne 2 -or $Color.captures.Count -ne 2) {
        throw 'Missing native color Canvas/state/screenshot evidence'
    }
    Add-Type -AssemblyName System.Drawing
    $palettes = @(
        @{ fundo='#101f33'; texto='#f3ead6'; destaque='#c7a362' },
        @{ fundo='#f0e4cc'; texto='#192030'; destaque='#a52a50' }
    )
    $phases = @('before', 'after')
    foreach ($index in 0..1) {
        $probe = $Color.probes[$index]
        $capture = $Color.captures[$index]
        $palette = $palettes[$index]
        if ($probe.phase -ne $phases[$index] -or $probe.visible -ne $true -or
            $probe.eventloop_ticks -lt 1 -or $probe.redraw_count -lt 1 -or
            $probe.names.Count -ne 6 -or $probe.items.Count -ne 27 -or $probe.alignment -notin @('esquerda','centralizado') -or
            [string]::IsNullOrWhiteSpace($probe.title)) {
            throw 'Native color eventloop/Canvas geometry missing'
        }
        Assert-ContainsBounds $probe.viewport_bounds $probe.panel_bounds
        Assert-ContainsBounds $probe.screen_bounds $probe.panel_bounds
        Assert-ContainsBounds $probe.panel_bounds $probe.canvas_bounds
        if ($probe.canvas_bounds[2] -lt 200 -or
            [math]::Abs($probe.canvas_bounds[2] / $probe.canvas_bounds[3] - 16.0/9) -ge 0.02) {
            throw 'Color miniature is not visible 16:9'
        }
        $items = @{}
        foreach ($item in $probe.items) {
            if ($items.ContainsKey([string]$item.tag)) { throw 'Duplicate Canvas tag' }
            $items[[string]$item.tag] = $item
        }
        foreach ($key in @('fundo', 'texto', 'destaque')) {
            if ([string]$probe.palette.$key -ne $palette[$key]) { throw 'Unexpected confirmed palette' }
        }
        if ($items['background'].fill -ne $palette.fundo -or $items['title'].fill -ne $palette.texto -or
            $items['title'].text -ne $probe.title -or $items['line'].fill -ne $palette.destaque) { throw 'Canvas title/color roles differ' }
        Assert-TitleGeometry $items['title'] $probe.canvas_bounds[2..3] $probe.alignment
        foreach ($number in 1..6) {
            if ($items["card-$number"].fill -ne '#192c42' -or $items["video-$number"].fill -ne '#000000' -or
                $items["number-$number"].fill -ne $palette.destaque -or
                $items["number-$number"].text -ne ('{0:d2}' -f $number) -or
                $items["name-$number"].fill -ne $palette.texto -or
                ([string]$items["name-$number"].text).Replace("`n", '') -ne $probe.names[$number-1]) {
                throw 'Canvas six scene order, fixed cards or black slots differ'
            }
        }
        if ($capture.phase -ne $probe.phase -or $capture.method -ne 'Win32.GDI.BitBlt' -or
            $capture.sha256 -notmatch '^[0-9a-f]{64}$' -or
            $capture.width -ne $probe.panel_bounds[2] -or $capture.height -ne $probe.panel_bounds[3]) {
            throw 'Screenshot does not correlate with the visible panel'
        }
        $imagePath = Get-ComparablePath $capture.path
        $expectedPath = Get-ComparablePath (Join-Path $LogRoot (Split-Path -Leaf $capture.path))
        if ($imagePath -ne $expectedPath -or -not (Test-Path -LiteralPath $capture.path -PathType Leaf) -or
            (Get-FileHash -LiteralPath $capture.path -Algorithm SHA256).Hash -ne $capture.sha256) { throw 'Native PNG missing or hash differs' }
        $image = [Drawing.Image]::FromFile($capture.path)
        try {
            if ($image.RawFormat.Guid -ne [Drawing.Imaging.ImageFormat]::Png.Guid -or
                $image.Width -ne $capture.width -or $image.Height -ne $capture.height) { throw 'Native PNG dimensions differ' }
        } finally { $image.Dispose() }
    }
    if ($Color.probes[1].redraw_count -le $Color.probes[0].redraw_count -or
        $Color.probes[1].eventloop_ticks -le $Color.probes[0].eventloop_ticks -or
        $Color.captures[0].sha256 -eq $Color.captures[1].sha256) { throw 'No actual palette/eventloop/screenshot transition' }
}
function Assert-FamilyArtifact($Path, $Sha, [string]$LogRoot) {
    $actual = Get-ComparablePath $Path
    $logs = Get-ComparablePath $LogRoot
    if (-not $actual.StartsWith($logs + '\') -or $Sha -notmatch '^[0-9a-f]{64}$' -or
        -not (Test-Path -LiteralPath $Path -PathType Leaf) -or
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Sha) {
        throw "font_families.artifact.sha256: $Path"
    }
}
function Assert-FamilyRender($Render, $Pins, [string]$LogRoot, [switch]$Current) {
    if ($Render.font_manifest_sha256 -cne '3f7b34337bd197858072f05afac19d08c7efc57a7a5416a6db01eb7d29a3882b' -or $Render.composition_exit -ne 0) {
        throw 'font_families.render.manifest_or_exit'
    }
    $roles = @{'titre.txt'='cinzel';'label-1.txt'='arimo';'label-2.txt'='lobster';'number-1.txt'=$null;'number-2.txt'=$null}
    if (@($Render.font_roles.PSObject.Properties).Count -ne 5) { throw 'font_families.render.roles.count' }
    foreach ($filename in $roles.Keys) {
        $row = $Render.font_roles.$filename
        $identity = if ($Current) { $null } else { $roles[$filename] }
        $file = if ($null -eq $identity) { 'font.ttf' } else { "family-$identity.ttf" }
        $sha = if ($null -eq $identity) { (Get-FileHash (Join-Path $env:SystemRoot 'Fonts/arial.ttf') -Algorithm SHA256).Hash.ToLowerInvariant() } else { $Pins[$identity] }
        if ($null -eq $row -or $row.id -cne $identity -or $row.file -cne $file -or $row.sha256 -cne $sha -or
            [string]::IsNullOrWhiteSpace($row.text) -or $row.measured_width -le 0 -or $row.measured_height -le 0) {
            throw "font_families.render.$filename.id_pin_body"
        }
        $pattern = 'drawtext=fontfile=' + [regex]::Escape($file) + ':textfile=' +
            [regex]::Escape([IO.Path]::GetFileNameWithoutExtension($filename)) + '(?:-line\d+)?\.txt:expansion=none:fontsize=(\d+):'
        $matches = [regex]::Matches($Render.graph, $pattern)
        $lines = @($row.text.Split("`n"))
        if ($matches.Count -ne $lines.Count -or @($matches | Where-Object { [int]$_.Groups[1].Value -ne $row.size }).Count -ne 0) {
            throw "font_families.render.$filename.graph"
        }
        foreach ($line in $lines) {
            if (@($Render.measurements | Where-Object { $_.file -ceq $file -and $_.sha256 -ceq $sha -and
                $_.text -ceq $line -and $_.size -eq $row.size -and $_.width -gt 0 -and $_.height -gt 0 }).Count -lt 1) {
                throw "font_families.render.$filename.measured_text"
            }
        }
    }
    Assert-FamilyArtifact $Render.frame $Render.frame_sha256 $LogRoot
    Assert-FamilyArtifact $Render.sample $Render.sample_sha256 $LogRoot
}
function Assert-FontFamilies($Data, [string]$ManifestPath, [string]$LogRoot) {
    if ((Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne '3f7b34337bd197858072f05afac19d08c7efc57a7a5416a6db01eb7d29a3882b') {
        throw 'font_families.manifest.sha256'
    }
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding utf8 | ConvertFrom-Json
    $pins = @{}; $names = @{'arimo'='Arimo';'montserrat'='Montserrat';'oswald'='Oswald';'merriweather'='Merriweather';
        'playfairdisplay'='Playfair Display';'cinzel'='Cinzel';'dancingscript'='Dancing Script';'lobster'='Lobster'}
    foreach ($row in $manifest.families) { $pins[$row.id] = $row.files[0].sha256 }
    $native = $Data.font_families
    if ($native.schema_version -ne 1 -or $native.success -ne $true -or $native.errors.Count -ne 0 -or
        $native.manifest_sha256 -cne '3f7b34337bd197858072f05afac19d08c7efc57a7a5416a6db01eb7d29a3882b' -or $native.media_calls -ne 0 -or
        $native.families.Count -ne 9 -or $native.controls.Count -ne 2 -or $native.captures.Count -ne 2) {
        throw 'font_families.native.header'
    }
    foreach ($key in @('legacy_defaults','all_nine_actual','selected_event','enter_event','focus','pending_preserved',
        'invalid_preserved','sizes_preserved','independence','persisted','current_restored','ui_numbers_preserved','combos_fit','reorder_remove','preview_roles')) {
        if ($native.checks.$key -isnot [bool] -or $native.checks.$key -ne $true) { throw "font_families.native.checks.$key" }
    }
    $seen = @{}
    foreach ($row in $native.families) {
        if ($null -eq $row.id) { continue }
        if (-not $names.ContainsKey($row.id) -or $seen.ContainsKey($row.id) -or
            $row.actual -cne $names[$row.id] -or $row.sha256 -cne $pins[$row.id] -or
            [IO.Path]::GetFileName($row.file) -cne 'Regular.ttf') { throw "font_families.native.$($row.id).actual_pin" }
        $seen[$row.id] = $true
    }
    if ($seen.Count -ne 8 -or $native.persisted.title -cne 'cinzel' -or
        ($native.persisted.scenes -join ',') -cne 'arimo,lobster') { throw 'font_families.native.persistence' }
    foreach ($control in $native.controls) {
        if ($control.readonly -ne $true -or $control.values.Count -ne 9 -or
            $control.text_width -gt $control.bounds[2]-15 -or $control.bounds[2] -lt $control.requested_width) {
            throw 'font_families.native.combo.fit'
        }
        Assert-ContainsBounds $control.parent $control.bounds
        Assert-ContainsBounds $control.viewport $control.bounds
    }
    foreach ($shot in $native.captures) {
        if ($shot.method -cne 'Win32.GDI.BitBlt') { throw 'font_families.native.capture.method' }
        Assert-FamilyArtifact $shot.path $shot.sha256 $LogRoot
    }
    if ($Data.family_samples.manifest_sha256 -cne '3f7b34337bd197858072f05afac19d08c7efc57a7a5416a6db01eb7d29a3882b' -or $Data.family_samples.samples.Count -ne 9) {
        throw 'font_families.samples.header'
    }
    $sampleIds = @{}; $frames = @{}
    foreach ($row in $Data.family_samples.samples) {
        if ($null -ne $row.id) {
            if (-not $pins.ContainsKey($row.id) -or $sampleIds.ContainsKey($row.id) -or
                $row.sha256 -cne $pins[$row.id] -or $frames.ContainsKey($row.frame_sha256)) { throw 'font_families.samples.pin_or_duplicate' }
            $sampleIds[$row.id]=$true; $frames[$row.frame_sha256]=$true
        }
        if ($row.exit_code -ne 0 -or $row.measured_width -le 0 -or $row.measured_height -le 0 -or
            $row.text -cne 'Uma noite para recordar' -or $row.size -ne 44) { throw 'font_families.samples.body' }
        Assert-FamilyArtifact $row.frame $row.frame_sha256 $LogRoot
    }
    if ($sampleIds.Count -ne 8) { throw 'font_families.samples.ids' }
    foreach ($output in $Data.outputs) { Assert-FamilyRender $output.font_render $pins $LogRoot }
    Assert-FamilyRender $Data.current_font_render $pins $LogRoot -Current
}

try {
    $package = (Resolve-Path $PackageDirectory).Path
    $output = Join-Path $pwd 'ci-output'
    New-Item -ItemType Directory -Force $output | Out-Null
    $zip = Join-Path $output 'Gerador-de-Menus.zip'
    Compress-Archive -Path $package -DestinationPath $zip
    $archiveHash = (Get-FileHash -Algorithm SHA256 $zip).Hash
    $smokeRoot = Join-Path $env:RUNNER_TEMP ('menu-video-smoke-' + [guid]::NewGuid().ToString('N'))
    $extraction = Join-Path $smokeRoot 'fresh-extraction'
    $alienCwd = Join-Path $smokeRoot 'unrelated-cwd'
    New-Item -ItemType Directory -Force $extraction, $alienCwd | Out-Null
    Expand-Archive -Path $zip -DestinationPath $extraction
    $exe = (Resolve-Path (Join-Path $extraction 'Gerador-de-Menus/Gerador-de-Menus.exe')).Path
    $localData = Join-Path $smokeRoot 'localappdata'
    New-Item -ItemType Directory -Force $localData | Out-Null
    $launchIds = @()
    foreach ($launch in 1..2) {
        $report = Join-Path $logs "smoke-$launch.json"
        # Environment belongs ONLY to this child, never the runner or global PATH.
        $start = [System.Diagnostics.ProcessStartInfo]::new()
        $start.FileName = $exe
        $start.WorkingDirectory = $alienCwd
        $start.UseShellExecute = $false
        $start.Environment.Clear()
        $start.Environment['SystemRoot'] = $env:SystemRoot
        $start.Environment['WINDIR'] = $env:SystemRoot
        $start.Environment['PATH'] = "$env:SystemRoot\System32;$env:SystemRoot"
        $start.Environment['TEMP'] = $env:RUNNER_TEMP
        $start.Environment['TMP'] = $env:RUNNER_TEMP
        $start.Environment['LOCALAPPDATA'] = $localData
        $start.ArgumentList.Add('--smoke-test')
        $scaleSeconds = if ($launch -eq 1) { 15 } else { 30 }
        $start.ArgumentList.Add('--smoke-scale-seconds')
        $start.ArgumentList.Add([string]$scaleSeconds)
        if ($launch -eq 1) { $start.ArgumentList.Add('--smoke-download') }
        $start.ArgumentList.Add('--report')
        $start.ArgumentList.Add($report)
        $process = [System.Diagnostics.Process]::Start($start)
        if (-not $process.WaitForExit(900000)) {
            $process.Kill($true)
            $process.WaitForExit()
            "Smoke launch $launch timed out after 900s" | Set-Content (Join-Path $logs "smoke-$launch-timeout.txt")
            throw "Native smoke launch $launch timed out"
        }
        if ($process.ExitCode -ne 0) { throw "Native smoke launch $launch exited $($process.ExitCode)" }
        if (-not (Test-Path $report)) { throw 'Native smoke report missing' }
        $data = Get-Content -Raw -Encoding utf8 $report | ConvertFrom-Json
        Assert-Interface $data.interface $logs
        Assert-ColorPreview $data.color_preview $logs
        Assert-FontFamilies $data (Join-Path (Split-Path $exe) '_internal/font_assets/manifest.json') $logs
        if (-not $data.success -or -not $data.frozen -or $data.platform -ne 'win32' -or
            -not $data.window.viewable -or $data.window.width -lt 820 -or $data.window.height -lt 600 -or
            $data.window.map_events -lt 1 -or -not $data.window.destroy_event -or
            -not $data.worker_done -or $data.worker_heartbeats -lt 1 -or -not $data.close_requested -or
            $data.errors.Count -ne 0 -or $data.diagnostic_failures -ne 0) {
            throw 'Native window/worker/exception report gate failed'
        }
        if ((Get-ComparablePath $data.cwd) -ne (Get-ComparablePath $alienCwd) -or
            (Get-ComparablePath $data.executable) -ne (Get-ComparablePath $exe) -or $data.path -ne $start.Environment['PATH']) {
            throw 'Smoke did not run from fresh extraction, foreign cwd and minimal child PATH'
        }
        if (-not $data.bootstrap.cache_verified -or
            $data.bootstrap.archive_sha256 -ne '5a8898e31f2c452062d1f1be475befd8ffba04db11e9f8251da3d10d9a1ae2e5' -or
            $data.bootstrap.manifest_sha256 -notmatch '^[0-9a-f]{64}$') { throw 'Verified bootstrap missing' }
        if ($launch -eq 1) {
            if (-not $data.download_authorized -or -not $data.bootstrap.downloaded -or
                $data.bootstrap.download_requests -ne 1 -or $data.bootstrap.download_bytes -ne 193978038 -or
                $data.bootstrap.ui_heartbeats -lt 1 -or -not $data.bootstrap.window_viewable -or
                $data.network_events -lt 1) { throw 'First launch did not perform the authorized real download' }
        } else {
            if ($data.download_authorized -or $data.bootstrap.downloaded -or
                $data.bootstrap.download_requests -ne 0 -or $data.bootstrap.download_bytes -ne 0 -or
                $data.network_events -ne 0) { throw 'Reopen attempted an unexpected download/network operation' }
        }
        $portableTools = [System.IO.Path]::GetFullPath((Join-Path (Split-Path $exe) 'tools')) + [System.IO.Path]::DirectorySeparatorChar
        if (-not $data.bootstrap.directory.StartsWith($portableTools, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'Bootstrap cache outside portable tools'
        }
        foreach ($tool in @('ffmpeg', 'ffprobe')) {
            $expected = (Resolve-Path (Join-Path $data.bootstrap.directory "$tool.exe")).Path
            if ($data.tools.$tool -ne $expected) { throw "External tool resolution: $tool" }
        }
        if ($data.outputs.Count -ne 3 -or
            @($data.outputs | Where-Object { $_.profile -eq 'prova' -and $_.format -eq 'MP4' }).Count -ne 1 -or
            @($data.outputs | Where-Object { $_.profile -eq '1080p' -and $_.format -eq 'MP4' }).Count -ne 1 -or
            @($data.outputs | Where-Object { $_.profile -eq '1080p' -and $_.format -eq 'AVI' }).Count -ne 1) {
            throw 'MP4 proof plus MP4 and AVI 1080p real renders required'
        }
        $cancel = $data.cancellation
        if (-not $cancel.button_main_thread -or -not $cancel.composition_started -or
            -not $cancel.children_reaped -or -not $cancel.no_partials -or
            -not $cancel.sources_intact -or -not $cancel.retry_verified -or
            $cancel.heartbeats -lt 1 -or $cancel.seconds -le 0 -or $cancel.seconds -ge 12) {
            throw 'Real frozen Tk button cancellation, cleanup and subsequent render required'
        }
        foreach ($row in $data.outputs) {
            if (-not $row.verified -or -not $row.full_decode -or $row.sources.Count -ne 2) {
                throw 'Decode/hash verification missing'
            }
            $avi = $row.format -eq 'AVI'
            $expectedFile = if ($row.profile -eq 'prova') { 'previa.mp4' } elseif ($avi) { 'menu.avi' } else { 'menu.mp4' }
            $expectedVideo = if ($avi) { 'mpeg4' } else { 'h264' }
            $expectedAudio = if ($avi) { 'mp3' } else { 'aac' }
            if (($avi -and $row.container -ne 'avi') -or
                (-not $avi -and $row.container.Split(',') -notcontains 'mp4')) { throw 'Actual container mismatch' }
            if ($row.output_files.Count -ne 1 -or $row.output_files[0] -ne $expectedFile) { throw 'Single video required' }
            $width = if ($row.profile -eq 'prova') { 640 } else { 1920 }
            $height = if ($row.profile -eq 'prova') { 360 } else { 1080 }
            $video = $row.streams[0]
            if ($row.streams.Count -ne 2 -or $video.codec_name -ne $expectedVideo -or
                $video.width -ne $width -or $video.height -ne $height -or $video.pix_fmt -ne 'yuv420p' -or
                $video.r_frame_rate -ne '30/1' -or [int]$video.nb_read_frames -ne 12 -or
                $row.streams[1].codec_name -ne $expectedAudio -or $row.streams[1].channels -ne 2 -or
                $row.catalog_sha256 -notmatch '^[0-9a-f]{64}$') { throw 'Actual stream/hash profile mismatch' }
            foreach ($source in $row.sources) {
                if (-not $source.full_decode -or $source.source_sha256_before -ne $source.source_sha256_after -or
                    $source.source_sha256_before -notmatch '^[0-9a-f]{64}$') { throw 'Source before/after hash mismatch' }
            }
        }
        $scale = $data.scaled_render
        $identity = $data.scale_identity
        $composition = $scale.composition
        $scaledVideo = $scale.streams[0]
        $scaledAudio = $scale.streams[1]
        if ($data.smoke_scale_seconds -ne $scaleSeconds -or $scale.seconds -ne $scaleSeconds -or
            $scale.scene_count -ne 2 -or $scale.preview_seconds -ne 3 -or $scale.profile -ne '1080p' -or
            $scale.frames -ne ($scaleSeconds * 30) -or -not $scale.full_decode -or
            $scale.output_sha256 -notmatch '^[0-9a-f]{64}$' -or $scale.decode_sha256 -notmatch '^[0-9a-f]{64}$' -or
            $scale.streams.Count -ne 2 -or $scaledVideo.codec_name -ne 'h264' -or
            $scaledVideo.width -ne 1920 -or $scaledVideo.height -ne 1080 -or
            $scaledVideo.r_frame_rate -ne '30/1' -or $scaledVideo.pix_fmt -ne 'yuv420p' -or
            [int]$scaledVideo.nb_read_frames -ne ($scaleSeconds * 30) -or
            [math]::Abs([double]$scaledVideo.duration - $scaleSeconds) -ge 0.001 -or
            $scaledAudio.codec_name -ne 'aac' -or $scaledAudio.channels -ne 2 -or
            [math]::Abs([double]$scaledAudio.duration - $scaleSeconds) -ge 0.001) {
            throw 'Frozen scale requires actual 2 previews of 3s, 15/30s duration, exact frames and full decode'
        }
        if ($null -eq $composition.exit_code -or $composition.exit_code -ne 0 -or
            $null -eq $composition.timed_out -or $composition.timed_out -or
            -not $composition.spontaneous_exit -or -not $composition.progress_end -or
            $composition.progress_frames -ne ($scaleSeconds * 30) -or
            $composition.elapsed_seconds -le 0 -or $composition.elapsed_seconds -ge 90 -or
            $composition.background_seconds -ne $scaleSeconds -or $composition.output_limit_seconds -ne $scaleSeconds -or
            $composition.preview_loop_count -ne 2 -or $null -eq $composition.music_looped -or $composition.music_looped -or
            -not $scale.non_repeating -or $scale.music_seconds -ne 6 -or
            $scale.audio_rms_start -le 100 -or $scale.post_track_samples -lt 800 -or
            $null -eq $scale.post_track_rms -or $scale.post_track_rms -ge 2) {
            throw 'Frozen scale requires spontaneous exit, progress, finite EOF and silence after non-looped MP3'
        }
        if (-not $identity.frozen -or $identity.platform -ne 'win32' -or -not $identity.portable_verified -or
            $identity.manifest_sha256 -ne $data.bootstrap.manifest_sha256 -or
            $identity.archive_sha256 -ne $data.bootstrap.archive_sha256 -or
            $identity.manifest_file_sha256 -notmatch '^[0-9a-f]{64}$') { throw 'Frozen scale tool identity missing' }
        foreach ($tool in @('ffmpeg', 'ffprobe')) {
            if ($identity.tools.$tool.path -ne $data.tools.$tool -or
                $identity.tools.$tool.sha256 -notmatch '^[0-9a-f]{64}$' -or
                $identity.tools.$tool.version -notmatch '^ff(mpeg|probe) version ') { throw 'Frozen effective tool hash/version missing' }
        }
        if ($scale.sources.Count -ne 3) { throw 'Frozen scale needs both video sources and MP3' }
        foreach ($source in $scale.sources) {
            if (-not $source.full_decode -or $source.before -notmatch '^[0-9a-f]{64}$' -or
                $source.before -ne $source.after) { throw 'Frozen scale source changed or was not decoded' }
        }
        if ($launchIds -contains $data.launch_id) { throw 'Reused report; two real launches required' }
        $launchIds += $data.launch_id
        $process.Dispose()
    }
    if ((Get-FileHash -Algorithm SHA256 $zip).Hash -ne $archiveHash) { throw 'Final ZIP changed during smoke' }
    'Two independent native launches passed; first download, second offline verified reuse; ZIP unchanged.' | Set-Content (Join-Path $logs 'native-smoke-gate.log')
} catch {
    $_ | Out-String | Set-Content (Join-Path $logs 'smoke-orchestrator-error.log')
    throw
} finally {
    $diagnostics = if ($localData) { Join-Path $localData 'GeradorMenusVideo/logs' } else { $null }
    if ($diagnostics -and (Test-Path $diagnostics)) { Copy-Item -Recurse $diagnostics (Join-Path $logs 'application-diagnostics') }
}
