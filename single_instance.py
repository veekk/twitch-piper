"""Per-user Linux instance lock and local activation channel."""
import fcntl
import os
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance:
    def __init__(self, directory=None):
        root = Path(directory) if directory else Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation)) / 'twitch-piper'
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.socket_name = str(root / 'activate')
        self.fd = os.open(root / 'instance.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        self.server = None
        self.activate = None
        self.owned = False
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.owned = True
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None

    def notify_existing(self):
        socket = QLocalSocket()
        socket.connectToServer(self.socket_name)
        if socket.waitForConnected(1000):
            socket.write(b'activate\n')
            socket.waitForBytesWritten(1000)
            socket.disconnectFromServer()
            return True
        return False

    def listen(self, activate):
        if not self.owned:
            raise RuntimeError('Only the primary instance can listen')
        self.activate = activate
        # Only the flock owner can clean up a socket left behind by a crash.
        QLocalServer.removeServer(self.socket_name)
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._connected)
        if not self.server.listen(self.socket_name):
            raise OSError(self.server.errorString())

    def _connected(self):
        while self.server.hasPendingConnections():
            connection = self.server.nextPendingConnection()
            connection.close()
            connection.deleteLater()
            # A local connection itself is enough; there are no commands to execute.
            self.activate()

    def close(self):
        if self.server is not None:
            self.server.close()
            self.server = None
        if self.fd is not None:
            os.close(self.fd)  # Kernel releases the lock, including on crashes.
            self.fd = None
        self.owned = False
