"""Linux process-tree resource sampling, off the UI thread."""
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time


def process_tree(root, proc=Path('/proc')):
    entries = {}
    for directory in proc.iterdir():
        if not directory.name.isdigit():
            continue
        try:
            fields = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
            entries[int(directory.name)] = (int(fields[1]), int(fields[19]),
                int(fields[11]) + int(fields[12]), int(fields[21]) * os.sysconf('SC_PAGE_SIZE'))
        except (OSError, ValueError, IndexError):
            continue
    selected = {root}
    while True:
        more = {pid for pid, entry in entries.items() if entry[0] in selected} - selected
        if not more:
            break
        selected.update(more)
    return {pid: entries[pid] for pid in selected if pid in entries}


def parse_gpu(output, pids):
    header = None
    utilization, memory = [], []
    matched = False
    for line in output.splitlines():
        fields = line.split()
        if line.lstrip().startswith('#'):
            candidate = line.lstrip()[1:].split()
            if 'pid' in candidate and 'sm' in candidate:
                header = candidate
            continue
        if not header:
            continue
        row = dict(zip(header, fields))
        if not row.get('pid', '').isdigit() or int(row['pid']) not in pids:
            continue
        matched = True
        for key, values in [('sm', utilization), ('fb', memory)]:
            try:
                values.append(float(row[key]))
            except (KeyError, ValueError):
                pass
    if header is None:
        return None, None
    if not matched:
        return 0., 0.
    return (sum(utilization) if utilization else None, sum(memory) if memory else None)


class ResourceMonitor(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self.latest = 'App load · CPU: … · RAM: … · GPU: … · VRAM: …'
        self.previous = {}
        self.last_time = time.monotonic()
        self.smi = shutil.which('nvidia-smi')
        self.start()

    def sample(self):
        now = time.monotonic()
        entries = process_tree(os.getpid())
        ticks = 0
        for pid, (_, start, used, _) in entries.items():
            old = self.previous.get((pid, start))
            if old is not None:
                ticks += max(0, used - old)
        cpu = ticks / os.sysconf('SC_CLK_TCK') / max(now - self.last_time, .001) * 100
        self.previous = {(pid, row[1]): row[2] for pid, row in entries.items()}
        self.last_time = now
        ram = sum(row[3] for row in entries.values()) / 1024**2
        gpu, vram = None, None
        if self.smi:
            try:
                result = subprocess.run([self.smi, 'pmon', '-s', 'um', '-c', '1'],
                    capture_output=True, text=True, timeout=3)
                if result.returncode == 0:
                    gpu, vram = parse_gpu(result.stdout, entries)
            except (OSError, subprocess.TimeoutExpired):
                pass
        gpu_text = 'N/A' if gpu is None else f'{gpu:.0f}%'
        vram_text = 'N/A' if vram is None else f'{vram:.0f} MiB'
        return f'App load · CPU: {cpu:.1f}% · RAM: {ram:.0f} MiB · GPU: {gpu_text} · VRAM: {vram_text}'

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.latest = self.sample()
            except (OSError, ValueError):
                self.latest = 'App load unavailable'
            self.stop_event.wait(1)

    def close(self):
        self.stop_event.set()
