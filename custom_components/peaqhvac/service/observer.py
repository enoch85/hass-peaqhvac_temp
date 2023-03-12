from __future__ import annotations

import logging
import time
from typing import Tuple

_LOGGER = logging.getLogger(__name__)
COMMAND_WAIT = 3

class ObserverBroadcaster:
    def __init__(self, message: str, hub):
        self._observer_message = message
        self.hub = hub

    def _broadcast_changes(self):
        if self._observer_message is not None:
            self.hub.observer.broadcast(self._observer_message)


class Observer:
    def __init__(self, hub):
        self._subscribers: dict = {}
        self._broadcast_queue = []
        self._wait_queue = {}
        self._active = False
        self._hub = hub

    def activate(self) -> None:
        self._active = True

    def add(self, command: str, func):
        if command in self._subscribers.keys():
            self._subscribers[command].append(func)
        else:
            self._subscribers[command] = [func]

    def broadcast(self, command: str, timeout: int = None):
        _expiration = None
        if timeout is not None:
            _expiration = time.time() + timeout
        if (command, _expiration) not in self._broadcast_queue:
            self._broadcast_queue.append((command, _expiration))
        self._prepare_dequeue()        

    def _prepare_dequeue(self, attempt:int = 0) -> None:
        if self._active:
            for q in self._broadcast_queue:
                if q[0] in self._subscribers.keys():
                    self._dequeue_and_broadcast(q)
        elif attempt < 5:
            _ = self._hub.is_initialized
            attempt += 1
            return self._prepare_dequeue(attempt)

    def _ok_to_broadcast(self, command) ->  bool:
        if command not in self._wait_queue.keys():
            self._wait_queue[command] = time.time()
            return True
        if time.time() - self._wait_queue[command] > COMMAND_WAIT:
            self._wait_queue[command] = time.time()
            return True
        return False

    def _dequeue_and_broadcast(self, command: Tuple[str, int | None]):
        #_LOGGER.debug(f"ready to broadcast: {command[0]}")
        if command[1] is None or command[1] > time.time():
            if self._ok_to_broadcast(command[0]):
                for func in self._subscribers[command[0]]:
                    func()
                self._broadcast_queue.remove(command)
