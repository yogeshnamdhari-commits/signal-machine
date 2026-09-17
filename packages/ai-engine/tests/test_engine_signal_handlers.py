from asyncio import Event

from main import _install_engine_signal_handlers


class _LoopWithoutMainThreadSignals:
    def add_signal_handler(self, *_args):
        raise RuntimeError("set_wakeup_fd only works in main thread")


def test_signal_handler_install_is_tolerant_in_background_thread():
    stop = Event()
    installed = _install_engine_signal_handlers(_LoopWithoutMainThreadSignals(), stop)
    assert installed is False
    assert stop.is_set() is False
