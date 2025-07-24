from hackingBuddyGPT.capabilities import SSHRunCommand, SSHTestCredential
from hackingBuddyGPT.capabilities.local_shell import LocalShellCapability
from hackingBuddyGPT.usecases.base import AutonomousAgentUseCase, use_case
from hackingBuddyGPT.utils import SSHConnection
from hackingBuddyGPT.utils.local_shell import LocalShellConnection
from typing import Union
from .common import Privesc


class LinuxPrivesc(Privesc):
    conn: Union[SSHConnection, LocalShellConnection] = None
    system: str = "linux"

    def init(self):
        super().init()
        
        # Dynamically choose capabilities based on connection type
        if isinstance(self.conn, LocalShellConnection):
            # Use local shell capabilities
            self.add_capability(LocalShellCapability(conn=self.conn), default=True)
            # Note: You might need a local equivalent of test_credential or adapt SSHTestCredential
            # For now, keeping SSH test credential as fallback
            self.add_capability(SSHTestCredential(conn=self.conn))
        else:
            # Use SSH capabilities (existing behavior)
            self.add_capability(SSHRunCommand(conn=self.conn), default=True)
            self.add_capability(SSHTestCredential(conn=self.conn))


@use_case("Linux Privilege Escalation")
class LinuxPrivescUseCase(AutonomousAgentUseCase[LinuxPrivesc]):
    pass