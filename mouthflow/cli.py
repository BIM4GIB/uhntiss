"""Mouthflow CLI: `mouthflow record | run <wav> | dry-run <wav>`."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="Voice-driven arrangement agent for Ableton Live.")


@app.command()
def record(duration: float = 15.0) -> None:
    """Capture audio, run the pipeline, apply to Live."""
    raise NotImplementedError


@app.command()
def run(wav: Path) -> None:
    """Run the pipeline on an existing WAV and apply to Live."""
    raise NotImplementedError


@app.command("dry-run")
def dry_run(wav: Path, json: bool = False) -> None:
    """Run the pipeline, print the Plan, don't touch Live."""
    raise NotImplementedError


if __name__ == "__main__":
    app()
