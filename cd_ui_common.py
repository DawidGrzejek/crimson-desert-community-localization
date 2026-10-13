"""Small Qt helpers shared by the windows of the translation comparer."""

import traceback

from PyQt6.QtCore import QThread, pyqtSignal


class Worker(QThread):
    """Runs fn(progress, stop) in a background thread.

    progress(done, total, label) and the result/error arrive as Qt signals in the GUI thread.
    stop() returns True after cancel() was called.
    """

    progressed = pyqtSignal(int, int, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            result = self.fn(lambda i, n, label="": self.progressed.emit(i, n, label), lambda: self._cancel)
        except Exception as e:  # reported to the user, the thread must not die silently
            traceback.print_exc()
            self.failed.emit(str(e))
        else:
            self.succeeded.emit(result)


def start_worker(parent, fn, on_done=None, on_error=None, on_progress=None):
    """Create, connect and start a Worker; the caller keeps the returned object alive."""
    worker = Worker(fn, parent)
    if on_progress:
        worker.progressed.connect(on_progress)
    if on_done:
        worker.succeeded.connect(on_done)
    if on_error:
        worker.failed.connect(on_error)
    worker.start()
    return worker
