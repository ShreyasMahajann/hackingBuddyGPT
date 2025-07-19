from dataclasses import dataclass, field
from typing import Optional, Tuple
import os

from hackingBuddyGPT.utils.configurable import configurable

@configurable("local_shell", "attaches to a running local shell using PTY (by PID)")
@dataclass
class LocalShellConnection:
    pid: int = field(metadata={"help": "PID of running local shell for PTY connection"})

    _pty_path: Optional[str] = None
    _pty: Optional["file"] = field(default=None, repr=False, compare=False)

    def init(self):
        # Find PTY path using self.pid:
        fd0 = os.readlink(f"/proc/{self.pid}/fd/0")
        # Validate it's a PTY (e.g., startswith /dev/pts/)
        if not fd0.startswith("/dev/pts/"):
            raise ValueError(f"PID {self.pid} fd0 does not point to a PTY: {fd0}")
        self._pty_path = fd0
        # Open PTY as binary read/write
        self._pty = open(fd0, "rb+", buffering=0)

    def run(self, cmd, *args, **kwargs) -> Tuple[str, str, int]:
        # Write command to PTY (end with newline)
        self._pty.write(cmd.encode() + b"\n")
        # Read output until you detect prompt or time out:
        import time
        output = b""
        start = time.time()
        while time.time() - start < 2:  # wait max 2 sec
            try:
                part = self._pty.read(4096)
                if not part:
                    break
                output += part
                # Optionally break if prompt found
            except BlockingIOError:
                time.sleep(0.05)
        # Output splitting into stdout/stderr if possible (or all as stdout)
        return output.decode(errors="replace"), "", 0 # Adjust for real split
