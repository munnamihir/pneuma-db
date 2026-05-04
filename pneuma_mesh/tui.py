"""
PNEUMA Mesh Terminal Dashboard
==============================
A live terminal UI showing the mesh status.

Run it alongside your mesh node to see:
  - Which nodes are in the mesh
  - Whose TDMA slot is active right now
  - ML-KEM session status
  - Local DB record count
  - Recent events log

Uses only the standard library (curses) — no dependencies.
Press Q to quit. Press R to force-refresh.
"""

import curses
import time
import threading
from typing import Optional


class MeshDashboard:
    """
    Live terminal dashboard for a PNEUMA acoustic mesh node.

    Usage:
        node = AcousticMeshNode("laptop-a")
        node.start()
        dashboard = MeshDashboard(node)
        dashboard.run()   # blocks — press Q to exit
    """

    def __init__(self, node):
        self.node       = node
        self._events:   list[str] = []
        self._max_events = 12
        self._lock       = threading.Lock()

    def log_event(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        with self._lock:
            self._events.append(f"[{ts}] {msg}")
            if len(self._events) > self._max_events:
                self._events.pop(0)

    def run(self):
        """Start the curses dashboard. Blocks until Q is pressed."""
        curses.wrapper(self._main)

    def _main(self, stdscr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()

        # Colour pairs
        curses.init_pair(1, curses.COLOR_CYAN,    -1)   # title
        curses.init_pair(2, curses.COLOR_GREEN,   -1)   # active/ok
        curses.init_pair(3, curses.COLOR_YELLOW,  -1)   # warning
        curses.init_pair(4, curses.COLOR_RED,     -1)   # error
        curses.init_pair(5, curses.COLOR_BLUE,    -1)   # label
        curses.init_pair(6, curses.COLOR_MAGENTA, -1)   # accent
        curses.init_pair(7, curses.COLOR_WHITE,   -1)   # normal

        stdscr.nodelay(True)
        stdscr.timeout(200)   # refresh every 200ms

        while True:
            key = stdscr.getch()
            if key in (ord("q"), ord("Q")):
                break

            stdscr.erase()
            h, w = stdscr.getmaxyx()

            try:
                self._draw(stdscr, h, w)
            except curses.error:
                pass   # terminal too small — ignore

            stdscr.refresh()

    def _draw(self, scr, h: int, w: int):
        C = curses.color_pair
        BOLD = curses.A_BOLD

        status = self.node.status()
        now    = time.strftime("%H:%M:%S")

        # ── Header ───────────────────────────────────────────
        title = " PNEUMA Acoustic Mesh — Offline Mode "
        scr.addstr(0, 0, "=" * w, C(1))
        scr.addstr(0, max(0, (w - len(title)) // 2), title, C(1) | BOLD)
        scr.addstr(1, 0, f"  Node: {status['node_id']}  |  {now}  |  {status['algorithm']}  |  Press Q to quit", C(5))
        scr.addstr(2, 0, "─" * w, C(5))

        row = 3

        # ── TDMA clock ───────────────────────────────────────
        tdma     = self.node.tdma
        slot_now = tdma.current_slot()
        owner    = tdma.current_owner()
        n_nodes  = tdma.num_nodes
        bar_w    = min(n_nodes * 12, w - 20)
        slot_w   = max(1, bar_w // n_nodes) if n_nodes else bar_w

        scr.addstr(row, 2, "Time slots:", C(5) | BOLD)
        row += 1

        for i, nid in enumerate(tdma.all_nodes):
            label   = f" {nid[:8]:^8} "
            is_mine = (nid == self.node.node_id)
            is_now  = (i == slot_now)
            x = 2 + i * (slot_w + 1)
            if x + slot_w >= w:
                break
            if is_now:
                attr = C(2) | BOLD | curses.A_REVERSE
            elif is_mine:
                attr = C(6) | BOLD
            else:
                attr = C(7)
            scr.addstr(row, x, label[:slot_w], attr)

        row += 1
        scr.addstr(row, 2, f"Active: {owner} is transmitting", C(2) if owner == self.node.node_id else C(3))
        row += 2

        # ── Peers ────────────────────────────────────────────
        scr.addstr(row, 2, "Peers:", C(5) | BOLD)
        row += 1
        peers   = status.get("peers", [])
        sessions = status.get("sessions", [])

        if not peers:
            scr.addstr(row, 4, "Listening for peers...", C(3))
            row += 1
        else:
            for peer in peers:
                has_session = peer in sessions
                icon  = "[+]" if has_session else "[ ]"
                color = C(2) if has_session else C(3)
                ts    = self.node.discovery.peer_last_seen(peer)
                ago   = f"{time.time() - ts:.0f}s ago" if ts else "?"
                line  = f"  {icon} {peer:<20} ML-KEM: {'ok' if has_session else 'pending':<10} last seen: {ago}"
                scr.addstr(row, 2, line[:w-4], color)
                row += 1

        row += 1

        # ── DB stats ─────────────────────────────────────────
        scr.addstr(row, 2, "Local DB:", C(5) | BOLD)
        row += 1
        scr.addstr(row, 4, f"{status.get('local_records', 0)} records stored locally  |  outbox: {status.get('outbox_depth', 0)} packets queued", C(7))
        row += 2

        # ── Event log ────────────────────────────────────────
        scr.addstr(row, 2, "Events:", C(5) | BOLD)
        row += 1
        with self._lock:
            events = list(self._events)
        for ev in events:
            if row >= h - 2:
                break
            scr.addstr(row, 4, ev[:w - 6], C(7))
            row += 1

        # ── Footer ───────────────────────────────────────────
        scr.addstr(h - 1, 0, "=" * w, C(1))
        footer = " Q: quit  |  Offline — no internet — pure acoustic "
        scr.addstr(h - 1, max(0, (w - len(footer)) // 2), footer, C(1))


def run_dashboard(node):
    """Convenience function to start the dashboard."""
    dash = MeshDashboard(node)

    # Patch node's print statements into the dashboard log
    import builtins
    original_print = builtins.print
    def patched_print(*args, **kwargs):
        msg = " ".join(str(a) for a in args)
        if node.node_id in msg or "PNEUMA" in msg or "ML-KEM" in msg or "Peer" in msg:
            dash.log_event(msg.strip())
        original_print(*args, **kwargs)
    builtins.print = patched_print

    dash.run()
    builtins.print = original_print
