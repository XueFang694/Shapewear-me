"""
Script de diagnostic à lancer À LA PLACE de main.py.
Affiche le traceback COMPLET de chaque erreur DB + les sessions simultanées.

    python debug_lock.py
"""
import sys
import os
import threading
import time
import traceback as tb_module
from contextlib import contextmanager

# Patch get_db avant tout import applicatif
import app.storage.database as _db_module

_open_sessions: dict = {}
_sessions_lock = threading.Lock()
_original_get_db = _db_module.get_db


@contextmanager
def _traced_get_db():
    tid = threading.get_ident()
    tname = threading.current_thread().name
    frame = "".join(tb_module.format_stack(limit=8)[:-2])
    key = id(frame) + int(time.time() * 1e6)
    ts = time.time()

    with _sessions_lock:
        _open_sessions[key] = {"tid": tid, "tname": tname, "frame": frame, "ts": ts}
        n = len(_open_sessions)

    print(f"\n[DB OPEN ] tid={tid} tname={tname!r} concurrent={n}")
    if n > 1:
        print("  ⚠️  SESSIONS SIMULTANÉES DÉTECTÉES :")
        for k, v in list(_open_sessions.items()):
            print(f"    → tid={v['tid']} tname={v['tname']!r} âge={time.time()-v['ts']:.3f}s")

    try:
        with _original_get_db() as session:
            yield session
    except Exception as exc:
        print(f"\n[DB ERROR] tid={tid} tname={tname!r}")
        print(f"  Erreur : {exc}")
        print("  Stack complet :")
        print("  " + tb_module.format_exc().replace("\n", "\n  "))
        raise
    finally:
        elapsed = time.time() - ts
        with _sessions_lock:
            _open_sessions.pop(key, None)
        print(f"[DB CLOSE] tid={tid} durée={elapsed:.3f}s")


_db_module.get_db = _traced_get_db

print("=" * 60)
print("DEBUG MODE — toutes les sessions DB sont tracées")
print("=" * 60)
print()

from app.storage.database import init_db
init_db()

db_path = os.path.join(os.path.dirname(__file__), "data", "shapewear.db")
import sqlite3
conn = sqlite3.connect(db_path)
mode  = conn.execute("PRAGMA journal_mode").fetchone()[0]
lmode = conn.execute("PRAGMA locking_mode").fetchone()[0]
conn.close()
print(f"Base     : {db_path}")
print(f"WAL mode : {mode}  |  locking : {lmode}")
print()
print("Lance l'analyse SPANX depuis l'interface, puis lis les logs ici.")
print("-" * 60)
print()

from PySide6.QtWidgets import QApplication
from app.ui.main_window import MainWindow

app = QApplication(sys.argv)
window = MainWindow()
window.show()
sys.exit(app.exec())