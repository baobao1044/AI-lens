#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

try:
    from .index_store import index_paths
    from .scan import scan_project
except ImportError:
    from index_store import index_paths
    from scan import scan_project


class CodeChangeHandler:
    def __init__(self, project_path: str | Path, *, debounce_seconds: float = 2.0) -> None:
        self.project_path = Path(project_path).resolve()
        self.debounce_seconds = debounce_seconds
        self.pending_files: set[str] = set()
        self.last_event = 0.0

    def on_event(self, event_path: str) -> None:
        normalized = Path(event_path).resolve()
        if self._should_track(normalized):
            self.pending_files.add(str(normalized))
            self.last_event = time.time()

    def flush(self) -> int:
        if not self.pending_files:
            return 0
        if (time.time() - self.last_event) < self.debounce_seconds:
            return 0
        changed = sorted(self.pending_files)
        self.pending_files.clear()
        manifest = scan_project(str(self.project_path), quiet=True, changed_files=changed)
        print(
            f"[ai-lens] Re-indexed {len(changed)} changed files "
            f"({manifest['project']['total_files']} indexed total)",
            file=sys.stderr,
        )
        return len(changed)

    def _should_track(self, path: Path) -> bool:
        try:
            path.relative_to(self.project_path)
        except ValueError:
            return False
        parts = path.parts
        if ".ai-lens" in parts or "__pycache__" in parts or "node_modules" in parts:
            return False
        return path.is_file() or not path.exists()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch a project and keep ai-lens fresh.")
    parser.add_argument("root", nargs="?", default=".", help="Project root to watch.")
    parser.add_argument("--debounce", type=float, default=2.0, help="Debounce window in seconds.")
    parser.add_argument("--install-hook", action="store_true", help="Install a git post-commit hook that refreshes ai-lens.")
    return parser.parse_args()


def install_hook(project_path: str | Path) -> int:
    root = Path(project_path).resolve()
    git_dir = root / ".git"
    if not git_dir.exists():
        print(f"Git repository not found under {root}", file=sys.stderr)
        return 1
    hook_path = git_dir / "hooks" / "post-commit"
    script = (
        "#!/bin/sh\n"
        f'python "{(Path(__file__).resolve().parent / "scan.py").as_posix()}" "{root.as_posix()}" > /dev/null 2>&1\n'
    )
    hook_path.write_text(script, encoding="utf-8")
    try:
        hook_path.chmod(0o755)
    except OSError:
        pass
    print(f"Installed git hook at {hook_path.as_posix()}")
    return 0


def main() -> int:
    args = parse_args()
    root = index_paths(args.root)["root"]
    if args.install_hook:
        return install_hook(root)

    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print("watchdog is not installed. Install it with: pip install watchdog", file=sys.stderr)
        return 2

    handler = CodeChangeHandler(root, debounce_seconds=args.debounce)

    class WatchdogHandler(FileSystemEventHandler):
        def on_modified(self, event):
            if not event.is_directory:
                handler.on_event(event.src_path)

        def on_created(self, event):
            if not event.is_directory:
                handler.on_event(event.src_path)

        def on_deleted(self, event):
            if not event.is_directory:
                handler.on_event(event.src_path)

        def on_moved(self, event):
            if not event.is_directory:
                handler.on_event(event.src_path)
                handler.on_event(event.dest_path)

    scan_project(str(root), quiet=True)
    observer = Observer()
    observer.schedule(WatchdogHandler(), str(root), recursive=True)
    observer.start()
    print(f"[ai-lens] Watching {root.as_posix()}", file=sys.stderr)
    try:
        while True:
            time.sleep(0.5)
            handler.flush()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
