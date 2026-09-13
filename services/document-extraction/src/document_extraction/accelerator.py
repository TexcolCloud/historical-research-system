"""Device selection and model memory budgets."""

from __future__ import annotations
import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AcceleratorProfile:
    device: str
    torch_device: str
    dtype: str
    attention: str | None
    tier: str
    name: str
    capability: tuple[int, int] | None
    vram_gb: float
    cpu_threads: int
    max_vram_gb: float
    torch_budget_gb: float
    paddle_budget_gb: float

    def report(self) -> dict:
        return asdict(self)


def tier_for_vram(vram_gb: float, fast_min_vram_gb: float = 12) -> str:
    return "fast" if vram_gb >= fast_min_vram_gb else "compat"


def memory_split(total_vram_gb: float, max_vram_gb: float) -> tuple[float, float]:
    budget = min(max_vram_gb, max(total_vram_gb - 0.5, 1.0))
    paddle_gb = min(1.5, max(1.0, budget * 0.2))
    return round(max(budget - paddle_gb, 0.0), 3), round(paddle_gb, 3)


def detect(settings) -> AcceleratorProfile:
    import torch

    requested = settings.device.lower()
    if requested not in {"auto", "cuda", "cpu"}:
        raise ValueError("acceleration.device只能是auto、cuda或cpu")
    cuda = torch.cuda.is_available() and requested != "cpu"
    if requested == "cuda" and not cuda and not settings.cpu_fallback:
        raise RuntimeError("请求CUDA，但当前PyTorch无法使用CUDA")
    if not cuda:
        return AcceleratorProfile(
            device="cpu",
            torch_device="cpu",
            dtype="float32",
            attention=None,
            tier="cpu",
            name="CPU",
            capability=None,
            vram_gb=0,
            cpu_threads=settings.cpu_threads,
            max_vram_gb=0,
            torch_budget_gb=0,
            paddle_budget_gb=0,
        )
    index = settings.device_id
    properties = torch.cuda.get_device_properties(index)
    vram_gb = properties.total_memory / 1024**3
    max_vram_gb = (
        settings.max_vram_gb if settings.enforce_vram_limit else max(vram_gb - 0.5, 1.0)
    )
    torch_budget_gb, paddle_budget_gb = memory_split(vram_gb, max_vram_gb)
    torch.cuda.set_per_process_memory_fraction(torch_budget_gb / vram_gb, index)
    os.environ["FLAGS_allocator_strategy"] = "auto_growth"
    os.environ["FLAGS_initial_gpu_memory_in_mb"] = "256"
    os.environ["FLAGS_reallocate_gpu_memory_in_mb"] = "128"
    os.environ["FLAGS_fraction_of_gpu_memory_to_use"] = str(paddle_budget_gb / vram_gb)
    return AcceleratorProfile(
        device=f"gpu:{index}",
        torch_device=f"cuda:{index}",
        dtype=settings.dtype,
        attention=None if settings.attention == "auto" else settings.attention,
        tier=tier_for_vram(vram_gb, settings.fast_min_vram_gb),
        name=properties.name,
        capability=torch.cuda.get_device_capability(index),
        vram_gb=round(vram_gb, 2),
        cpu_threads=settings.cpu_threads,
        max_vram_gb=round(max_vram_gb, 3),
        torch_budget_gb=torch_budget_gb,
        paddle_budget_gb=paddle_budget_gb,
    )


def cpu_profile(profile: AcceleratorProfile) -> AcceleratorProfile:
    return AcceleratorProfile(
        device="cpu",
        torch_device="cpu",
        dtype="float32",
        attention=None,
        tier="cpu-fallback",
        name="CPU",
        capability=None,
        vram_gb=0,
        cpu_threads=profile.cpu_threads,
        max_vram_gb=0,
        torch_budget_gb=0,
        paddle_budget_gb=0,
    )
