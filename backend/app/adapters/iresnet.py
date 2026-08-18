"""IResNet backbone used by AdaFace recognition models.

This is a re-implementation of the published IR-101 architecture, written here
rather than executed from the weights repository: loading a checkpoint should
never mean running code fetched alongside it. ``load_state_dict`` is called
strictly, so any divergence from the published architecture surfaces as a
loading error instead of silently wrong embeddings.

Reference: Kim et al., "AdaFace: Quality Adaptive Margin for Face Recognition"
(CVPR 2022), using the IResNet backbone of Deng et al., "ArcFace" (CVPR 2019).
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import Tensor, nn

#: Units per stage for each supported depth.
_STAGE_UNITS: dict[int, tuple[int, int, int, int]] = {
    18: (2, 2, 2, 2),
    50: (3, 4, 14, 3),
    100: (3, 13, 30, 3),
}

#: Channel width at each stage.
_STAGE_CHANNELS = (64, 128, 256, 512)


@dataclass(frozen=True, slots=True)
class _UnitSpec:
    in_channels: int
    out_channels: int
    stride: int


def _stage_specs(num_layers: int) -> list[_UnitSpec]:
    """Expand the stage table into one specification per residual unit."""
    if num_layers not in _STAGE_UNITS:
        raise ValueError(
            f"unsupported IResNet depth {num_layers}; expected one of {sorted(_STAGE_UNITS)}"
        )

    specs: list[_UnitSpec] = []
    in_channels = 64
    for channels, units in zip(_STAGE_CHANNELS, _STAGE_UNITS[num_layers], strict=True):
        # The first unit of a stage halves the spatial resolution.
        specs.append(_UnitSpec(in_channels, channels, stride=2))
        specs.extend(_UnitSpec(channels, channels, stride=1) for _ in range(units - 1))
        in_channels = channels
    return specs


class BasicBlockIR(nn.Module):
    """Improved residual unit: BN-Conv-BN-PReLU-Conv-BN with a projected shortcut."""

    def __init__(self, in_channels: int, out_channels: int, stride: int) -> None:
        """Build the residual and shortcut branches."""
        super().__init__()
        if in_channels == out_channels:
            self.shortcut_layer: nn.Module = nn.MaxPool2d(1, stride)
        else:
            self.shortcut_layer = nn.Sequential(
                nn.Conv2d(in_channels, out_channels, (1, 1), stride, bias=False),
                nn.BatchNorm2d(out_channels),
            )
        self.res_layer = nn.Sequential(
            nn.BatchNorm2d(in_channels),
            nn.Conv2d(in_channels, out_channels, (3, 3), (1, 1), 1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.PReLU(out_channels),
            nn.Conv2d(out_channels, out_channels, (3, 3), stride, 1, bias=False),
            nn.BatchNorm2d(out_channels),
        )

    def forward(self, x: Tensor) -> Tensor:
        """Apply the unit."""
        out: Tensor = self.res_layer(x) + self.shortcut_layer(x)
        return out


class Flatten(nn.Module):
    """Flattens all dimensions but the batch. Named to match the checkpoint."""

    def forward(self, x: Tensor) -> Tensor:
        """Flatten ``x``.

        ``reshape`` rather than ``view``: the preceding block can leave a
        non-contiguous tensor, which ``view`` refuses to handle.
        """
        return x.reshape(x.size(0), -1)


class Backbone(nn.Module):
    """IResNet feature extractor producing a fixed-width face embedding."""

    def __init__(self, num_layers: int = 100, output_dim: int = 512) -> None:
        """Build the backbone for a 112x112 input."""
        super().__init__()
        self.input_layer = nn.Sequential(
            nn.Conv2d(3, 64, (3, 3), 1, 1, bias=False),
            nn.BatchNorm2d(64),
            nn.PReLU(64),
        )
        self.body = nn.Sequential(
            *(
                BasicBlockIR(spec.in_channels, spec.out_channels, spec.stride)
                for spec in _stage_specs(num_layers)
            )
        )
        self.output_layer = nn.Sequential(
            nn.BatchNorm2d(512),
            nn.Dropout(0.4),
            Flatten(),
            nn.Linear(512 * 7 * 7, output_dim),
            # Affine-free: the checkpoint stores running statistics only.
            nn.BatchNorm1d(output_dim, affine=False),
        )

    def forward(self, x: Tensor) -> Tensor:
        """Return the unnormalised embedding for a batch of aligned faces."""
        embedding: Tensor = self.output_layer(self.body(self.input_layer(x)))
        return embedding


def ir_101(output_dim: int = 512) -> Backbone:
    """Build the IR-101 backbone AdaFace's published models use."""
    return Backbone(num_layers=100, output_dim=output_dim)


def count_parameters(model: nn.Module) -> int:
    """Total number of parameters, for sanity-checking a loaded model."""
    return sum(p.numel() for p in model.parameters())


__all__ = ["Backbone", "BasicBlockIR", "Flatten", "count_parameters", "ir_101", "torch"]
