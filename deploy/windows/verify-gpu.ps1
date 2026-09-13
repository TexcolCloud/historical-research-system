$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$serviceRoot = Join-Path $projectRoot "services\document-extraction"
$python = Join-Path $serviceRoot ".venv\Scripts\python.exe"
$nvidiaRoot = Join-Path $serviceRoot ".venv\Lib\site-packages\nvidia"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Environment missing. Run deploy\windows\bootstrap.ps1 first."
}

Get-ChildItem -LiteralPath $nvidiaRoot -Directory -ErrorAction SilentlyContinue |
    ForEach-Object {
        $bin = Join-Path $_.FullName "bin"
        if (Test-Path -LiteralPath $bin) { $env:PATH = "$bin;$env:PATH" }
    }

& $python -c 'import json, torch; assert torch.version.cuda == "12.9", f"Expected Torch CUDA 12.9, got {torch.version.cuda}"; assert torch.cuda.is_available(), "Torch CUDA unavailable"; print(json.dumps({"torch": torch.__version__, "cuda_runtime": torch.version.cuda, "cuda_available": True, "device": torch.cuda.get_device_name(0), "capability": torch.cuda.get_device_capability(0)}, ensure_ascii=False))'
& $python -c 'import json, paddle; runtime = paddle.version.cuda(); assert runtime == "12.9", f"Expected Paddle CUDA 12.9, got {runtime}"; assert paddle.device.is_compiled_with_cuda(), "Paddle CUDA unavailable"; print(json.dumps({"paddle": paddle.__version__, "cuda_runtime": runtime, "cuda_compiled": True, "device": paddle.device.get_device(), "device_count": paddle.device.cuda.device_count()}, ensure_ascii=False)); paddle.utils.run_check()'
& (Join-Path $serviceRoot ".venv\Scripts\history-extract.exe") check
