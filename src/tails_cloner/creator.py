from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from tails_cloner.models import PostWriteOptions
from tails_cloner.post_write import apply_post_write_options
from tails_cloner.source import LocalImageSource

RunCloneCommand = Callable[[list[str], Callable[[str], None]], int]



def build_clone_command(image_path: str | Path, device_path: str, use_pkexec: bool = True) -> list[str]:
    command = [
        "dd",
        f"if={Path(image_path)}",
        f"of={device_path}",
        "bs=4M",
        "status=progress",
        "oflag=direct",
        "conv=fsync",
    ]
    if use_pkexec:
        return ["pkexec", *command]
    return command



def _stream_process_output(process: subprocess.Popen[str], progress_callback: Callable[[str], None]) -> int:
    assert process.stderr is not None
    for line in process.stderr:
        message = line.strip()
        if message:
            progress_callback(message)
    return process.wait()



def run_clone_command(command: list[str], progress_callback: Callable[[str], None]) -> int:
    process = subprocess.Popen(  # noqa: S603 - destructive system command is the tool's core job
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    return _stream_process_output(process, progress_callback)



def clone_image_to_device(
    image_path: str | Path,
    device_path: str,
    run_command: RunCloneCommand = run_clone_command,
    progress_callback: Callable[[str], None] | None = None,
    post_write_options: PostWriteOptions | None = None,
    post_write_runner: Callable[[str, PostWriteOptions, Callable[[str], None] | None], None] = apply_post_write_options,
) -> None:
    image = LocalImageSource(Path(image_path))
    image.validate()
    callback = progress_callback or (lambda _message: None)
    exit_code = run_command(build_clone_command(image.path, device_path), callback)
    if exit_code != 0:
        raise RuntimeError(f"Clone process exited with status {exit_code}")

    options = post_write_options or PostWriteOptions()
    post_write_runner(device_path, options, callback)
