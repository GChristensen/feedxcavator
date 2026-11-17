# Deployment to the Linux server over Samba.
#
#   just run [--dev]                    start the server in a new cmd window
#   just build                          clean install of the frontend packages and a console build
#   just build-demo                     build the static demo into demo/ (GitHub Pages, opens from disk too)
#   just demo-server                    start a server on demo/home for recording, in a new cmd window
#   just record-demo [args]             record the demo fixtures from that server, then build the demo
#   just lock                           update uv.lock and constraints.txt after a dependency change
#   just deploy                         copy the code to the server
#   just pull-data                      replace local data/, workspace/ and config.yml with the server's
#   just remote='\\host\share\dir' deploy   use another installation directory
#
# FEEDXCAVATOR_REMOTE can also be set in a .env file next to this justfile (not committed).
# Single-quote the value so the backslashes stay literal: FEEDXCAVATOR_REMOTE='\\host\share\dir'

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]
set dotenv-load

# The installation directory on the server (holds feedxcavator/, data/, workspace/, config.yml).
remote := env("FEEDXCAVATOR_REMOTE", '\\server\share\feedxcavator')

# robocopy: 2-second timestamps for Samba, few retries, quiet; exit codes below 8 mean success
rc := "/FFT /XJ /R:2 /W:2 /NP /NDL /NJH"
ok := "; if ($LASTEXITCODE -ge 8) { exit 1 } else { exit 0 }"

_default:
    @just --list

# Start the server through feedxcavator.cmd in a new cmd window, which stays open after it exits.
run *args:
    Start-Process cmd.exe -WorkingDirectory '{{justfile_directory()}}' -ArgumentList '/k', 'title Feedxcavator && feedxcavator.cmd {{args}}'

# The console is built straight into feedxcavator/static/console, which Django serves as is (no collectstatic).

# Clean install of the frontend packages, then build the console into feedxcavator/static/console.
[working-directory('frontend')]
build:
    npm ci
    npm run build

# Build the static demo console (mock backend, recorded fixtures) into demo/, next to demo/workspace and demo/sites.
[working-directory('frontend')]
build-demo:
    npm run build:demo

# The home demo/home is not in git; its config.yml is written on first use. It serves demo/workspace.
# Start a server on demo/home (port 21900) for recording the demo, in a new cmd window.
demo-server:
    if (-not (Test-Path demo\home\config.yml)) { New-Item -ItemType Directory -Force demo\home | Out-Null; [IO.File]::WriteAllText("$PWD\demo\home\config.yml", "port: 21900`nhostname: 'https://feeds.example.com'`nauthentication: false`nwebsub: false`nservice-task: {enabled: false}`nworkspace: ../workspace`n") }
    Start-Process cmd.exe -WorkingDirectory '{{justfile_directory()}}\demo\home' -ArgumentList '/k', 'title Feedxcavator demo && set FEEDXCAVATOR_HOME={{justfile_directory()}}\demo\home&& set PYTHONPATH={{justfile_directory()}}&& {{justfile_directory()}}\.venv\Scripts\python -m feedxcavator run'

# Arguments go to tools/record_demo.py, such as --feeds x-orbital-lab,youtube-orbital-lab --no-run.
# Record the demo fixtures from the server started by demo-server, then build the demo.
record-demo *args:
    .venv\Scripts\python tools\record_demo.py {{args}}; if ($LASTEXITCODE) { exit 1 }
    just build-demo

# Copy the code to the server. data/, log/, workspace/, config.yml and .venv there are not touched.
deploy:
    if (-not (Test-Path -LiteralPath '{{remote}}')) { throw 'Server directory not reachable: {{remote}}' }
    robocopy feedxcavator '{{remote}}\feedxcavator' /MIR /XD __pycache__ {{rc}}{{ok}}
    robocopy deploy '{{remote}}\deploy' /MIR {{rc}}{{ok}}
    robocopy docs '{{remote}}\docs' /MIR {{rc}}{{ok}}
    robocopy . '{{remote}}' pyproject.toml uv.lock constraints.txt install.py README.md config.example.yml *.sh feedxcavator.cmd {{rc}}{{ok}}
    @echo 'Deployed. If dependencies changed, run python3 install.py there first. Then restart the server.'

# Update uv.lock from pyproject.toml and export the pinned versions for the pip path of install.py.
lock:
    uv lock
    uv export --all-extras --no-hashes --no-emit-project --format requirements-txt -o constraints.txt --quiet

# Replace local data/, workspace/ and config.yml with the server's copy. Images (data/files/blob) are not copied.
pull-data:
    if (-not (Test-Path -LiteralPath '{{remote}}\data')) { throw 'Server data not reachable: {{remote}}\data' }
    if (Test-Path data\feedxcavator.db) { try { [IO.File]::Open("$PWD\data\feedxcavator.db", 'Open', 'ReadWrite', 'None').Close() } catch { throw 'The local database is in use: stop the local server first.' } }
    foreach ($p in 'data', 'workspace', 'config.yml') { if (Test-Path $p) { Remove-Item -LiteralPath $p -Recurse -Force } }
    robocopy '{{remote}}\data' data /E /XD '{{remote}}\data\files\blob' /XF children.pid worker.rpc {{rc}}{{ok}}
    robocopy '{{remote}}\workspace' workspace /E {{rc}}{{ok}}
    robocopy '{{remote}}' . config.yml {{rc}}{{ok}}
