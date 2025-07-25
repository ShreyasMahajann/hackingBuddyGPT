import warnings
from dataclasses import dataclass
from typing import Tuple

from hackingBuddyGPT.utils import PSExecConnection
from .capability import Capability


@dataclass
class PSExecTestCredential(Capability):
    conn: PSExecConnection

    def describe(self) -> str:
        return "give credentials to be tested"

    def get_name(self) -> str:
        return "test_credential"

    def __call__(self, username: str, password: str) -> Tuple[str, bool]:
        try:
            test_conn = self.conn.new_with(username=username, password=password)
            test_conn.init()
            warnings.warn(
                message="full credential testing is not implemented yet for psexec, we have logged in, but do not know who we are, returning True for now",
                stacklevel=1,
            )
            return "Login as root was successful\n", True
        except Exception:
            return "Authentication error, credentials are wrong\n", False
