"""Garantiza que solo UNA ejecución corra a la vez en tu Mac.

En GitHub Actions lo garantiza además `concurrency: publicador` del workflow.
"""
from __future__ import annotations

import fcntl
from pathlib import Path


class BloqueoArchivo:
    """El sistema operativo lo libera solo si el proceso muere."""

    def __init__(self, ruta: str | Path = ".publicador.lock"):
        self.ruta = Path(ruta)
        self._f = None

    def adquirir(self, run: str) -> bool:
        self._f = open(self.ruta, "w")
        try:
            fcntl.flock(self._f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._f.close()
            self._f = None
            return False
        self._f.write(run)
        self._f.flush()
        return True

    def latido(self) -> None:
        pass

    def liberar(self) -> None:
        if self._f:
            fcntl.flock(self._f, fcntl.LOCK_UN)
            self._f.close()
            self._f = None
