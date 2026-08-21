<#
.SYNOPSIS
    本地开发用的 PostgreSQL 16，不需要 Docker、不需要管理员权限。

.DESCRIPTION
    首选方案仍是 `docker compose up -d db`（那套带 pgvector）。这个脚本是没有 Docker 时的替代：
    PyPI 的 pgserver 包自带 PostgreSQL 16 二进制，用 uv 拉一个 3.12 venv 专门跑服务端
    （pgserver 只有 cp39-cp312 的 Windows wheel），后端仍用自己的解释器走 TCP 连它。

    账号 / 密码 / 端口与 docker-compose.yml 一致，所以 ZY_DATABASE_URL 不用改：
        postgresql+psycopg://zhiyuan:zhiyuan_dev@localhost:5433/zhiyuan

    局限：这个 Windows 构建不带 pgvector 扩展。M1 用不到；M2 的向量召回需要
    docker compose 的 pgvector/pgvector:pg16，或另找带扩展的 PG。

.PARAMETER Action
    init    创建 venv + 数据目录（只需一次；已存在会拒绝，除非加 -Force）
    start   启动服务
    stop    停止服务
    status  看是否在跑 + 各状态资产条数
    psql    开一个交互式 psql
    reset   删掉整个数据目录重来（危险：数据全没）

.EXAMPLE
    powershell -File scripts/devdb.ps1 init
    powershell -File scripts/devdb.ps1 start
    powershell -File scripts/devdb.ps1 status
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('init', 'start', 'stop', 'status', 'psql', 'reset')]
    [string]$Action,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root '.pgvenv'
$Data = Join-Path $Root '.pgdata'
$Log = Join-Path $Root '.pgdata.log'
$Bin = Join-Path $Venv 'Lib\site-packages\pgserver\pginstall\bin'

$Port = 5433
$User = 'zhiyuan'
$Password = 'zhiyuan_dev'
$Database = 'zhiyuan'

function Assert-Installed {
    if (-not (Test-Path (Join-Path $Bin 'pg_ctl.exe'))) {
        throw "还没初始化。先跑： powershell -File scripts/devdb.ps1 init"
    }
}

function Start-Server {
    # 启动 daemon 有两个坑，都会让调用方永远挂住：
    #   1. 直接 & pg_ctl ... start：后台 postgres 继承调用方的 stdout/stderr 句柄，管道不关；
    #      加 -NoNewWindow 也一样，daemon 仍共享父控制台。
    #   2. Start-Process -Wait：它等的是整个进程树，包含那个不会退出的 daemon。
    # 所以用隐藏窗口切断句柄继承，且不加 -Wait —— 就绪与否由下面的轮询判断。
    Start-Process -FilePath (Join-Path $Bin 'pg_ctl.exe') -WindowStyle Hidden -ArgumentList @(
        '-D', "`"$Data`"", '-o', "`"-p $Port -c listen_addresses=localhost`"", '-l', "`"$Log`"", 'start'
    )
    for ($i = 0; $i -lt 30; $i++) {
        if (Test-Running) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Test-Running {
    & (Join-Path $Bin 'pg_isready.exe') -h localhost -p $Port -U $User *> $null
    return $LASTEXITCODE -eq 0
}

switch ($Action) {
    'init' {
        if ((Test-Path $Data) -and -not $Force) {
            throw "$Data 已存在。要重建请用： scripts/devdb.ps1 reset"
        }
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            throw "需要 uv：https://docs.astral.sh/uv/getting-started/installation/"
        }

        Write-Host '[1/4] 拉 Python 3.12 venv（pgserver 没有 cp313 的 wheel）...'
        uv venv --python 3.12 $Venv
        Write-Host '[2/4] 安装 pgserver（自带 PostgreSQL 16 二进制）...'
        uv pip install --python (Join-Path $Venv 'Scripts\python.exe') pgserver

        Write-Host '[3/4] initdb...'
        if (Test-Path $Data) { Remove-Item -Recurse -Force $Data }
        # 密码经临时文件传给 initdb，不走命令行参数（命令行会进程列表可见）
        $pwFile = Join-Path ([System.IO.Path]::GetTempPath()) "zy-pgpw-$PID.txt"
        try {
            Set-Content -Path $pwFile -Value $Password -NoNewline -Encoding ascii
            & (Join-Path $Bin 'initdb.exe') -D $Data -U $User `
                --encoding=UTF8 --locale=C --auth=scram-sha-256 --pwfile=$pwFile | Out-Null
        } finally {
            if (Test-Path $pwFile) { Remove-Item $pwFile }
        }

        Write-Host '[4/4] 启动并建库...'
        if (-not (Start-Server)) { throw "启动失败，看日志：$Log" }
        $env:PGPASSWORD = $Password
        & (Join-Path $Bin 'createdb.exe') -h localhost -p $Port -U $User $Database

        Write-Host ''
        Write-Host "PostgreSQL 已就绪：localhost:$Port/$Database" -ForegroundColor Green
        Write-Host '接着跑：  cd backend; alembic upgrade head; python scripts/seed.py'
    }

    'start' {
        Assert-Installed
        if (Test-Running) { Write-Host "已经在跑了（localhost:$Port）"; break }
        if (Start-Server) { Write-Host "已启动：localhost:$Port" -ForegroundColor Green }
        else { throw "启动失败，看日志：$Log" }
    }

    'stop' {
        Assert-Installed
        & (Join-Path $Bin 'pg_ctl.exe') -D $Data -m fast stop | Out-Null
        Write-Host '已停止'
    }

    'status' {
        Assert-Installed
        if (-not (Test-Running)) {
            Write-Host "未运行。启动： powershell -File scripts/devdb.ps1 start" -ForegroundColor Yellow
            break
        }
        Write-Host "运行中：localhost:$Port/$Database" -ForegroundColor Green
        $env:PGPASSWORD = $Password
        $psql = Join-Path $Bin 'psql.exe'
        $count = & $psql -h localhost -p $Port -U $User -d $Database -tAc `
            "select count(*) from information_schema.tables where table_schema='public'"
        Write-Host "表数量：$($count.Trim())"
        Write-Host '资产分布：'
        # SQL 保持纯 ASCII：命令行参数会被按系统 ANSI 码页编码传给 psql.exe，
        # 中文（比如列别名）到了服务端就是非法 UTF-8。
        & $psql -h localhost -p $Port -U $User -d $Database -c `
            "select status, count(*) as n from knowledge_asset group by status order by 2 desc"
    }

    'psql' {
        Assert-Installed
        $env:PGPASSWORD = $Password
        & (Join-Path $Bin 'psql.exe') -h localhost -p $Port -U $User -d $Database
    }

    'reset' {
        Assert-Installed
        if (Test-Running) { & (Join-Path $Bin 'pg_ctl.exe') -D $Data -m fast stop | Out-Null }
        Remove-Item -Recurse -Force $Data
        Write-Host "$Data 已删除。重建： scripts/devdb.ps1 init"
    }
}
