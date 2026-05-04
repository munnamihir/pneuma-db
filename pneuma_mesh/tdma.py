"""
PNEUMA TDMA Scheduler
=====================
Time Division Multiple Access over the acoustic channel.

Problem: laptop speakers and microphones can't transmit and receive
simultaneously on the same device. Solution: give each node a fixed
time slot to transmit; all other nodes listen during that slot.

Slot structure (default 3 nodes):
  |-- 500ms A tx -->|-- 500ms B tx -->|-- 500ms C tx -->|-- repeat -->|

Each slot = one packet opportunity.
Slots are determined by node rank in sorted(known_nodes).
Nodes stay in sync using wall-clock time (no coordinator needed).

Adding a node: broadcast JOIN beacon → all nodes update their ring.
"""

import time
import math
import threading
from typing import List, Callable, Optional


class TDMAScheduler:
    """
    Coordinates transmit/listen turns across all nodes.
    Uses wall-clock modulo arithmetic — no coordinator needed.
    Every node independently computes whose slot it is.
    """

    def __init__(
        self,
        node_id:    str,
        all_nodes:  List[str],    # all node IDs in the mesh (sorted)
        slot_ms:    int = 500,    # milliseconds per slot
        guard_ms:   int = 50,     # silence gap between slots (avoid overlap)
    ):
        self.node_id   = node_id
        self.slot_ms   = slot_ms
        self.guard_ms  = guard_ms
        self._update_nodes(all_nodes)
        self._lock      = threading.Lock()
        self._callbacks: List[Callable] = []   # called when our slot starts

    def _update_nodes(self, nodes: List[str]):
        """Re-sort nodes and recompute our slot index."""
        self.all_nodes  = sorted(set(nodes))
        self.num_nodes  = len(self.all_nodes)
        self.cycle_ms   = self.slot_ms * self.num_nodes
        try:
            self.my_slot = self.all_nodes.index(self.node_id)
        except ValueError:
            self.my_slot = 0

    def add_node(self, node_id: str):
        with self._lock:
            nodes = list(self.all_nodes) + [node_id]
            self._update_nodes(nodes)

    def remove_node(self, node_id: str):
        with self._lock:
            nodes = [n for n in self.all_nodes if n != node_id]
            self._update_nodes(nodes)

    # ── Slot queries ─────────────────────────────────────────
    def current_slot(self) -> int:
        """Which slot index is active right now (0-based)?"""
        epoch_ms = int(time.time() * 1000)
        return (epoch_ms // self.slot_ms) % self.num_nodes

    def current_owner(self) -> str:
        """Which node should be transmitting right now?"""
        return self.all_nodes[self.current_slot()]

    def is_my_turn(self) -> bool:
        """Should THIS node be transmitting right now?"""
        return self.current_slot() == self.my_slot

    def ms_until_my_slot(self) -> float:
        """Milliseconds until this node's next transmit slot."""
        epoch_ms    = int(time.time() * 1000)
        cycle_pos   = epoch_ms % self.cycle_ms
        my_slot_start = self.my_slot * self.slot_ms

        if cycle_pos < my_slot_start:
            return my_slot_start - cycle_pos
        else:
            return self.cycle_ms - cycle_pos + my_slot_start

    def ms_remaining_in_slot(self) -> float:
        """How many ms are left in the current active slot?"""
        epoch_ms  = int(time.time() * 1000)
        cycle_pos = epoch_ms % self.cycle_ms
        slot_pos  = cycle_pos % self.slot_ms
        return self.slot_ms - slot_pos - self.guard_ms

    def is_guard_period(self) -> bool:
        """Is this the silence gap between slots?"""
        epoch_ms = int(time.time() * 1000)
        slot_pos = (epoch_ms % self.slot_ms)
        return slot_pos > (self.slot_ms - self.guard_ms)

    def slot_owner(self, slot_idx: int) -> str:
        """Which node owns slot N?"""
        return self.all_nodes[slot_idx % self.num_nodes]

    # ── Blocking wait helpers ─────────────────────────────────
    def wait_for_my_slot(self, timeout: float = 10.0) -> bool:
        """Block until this node's transmit slot begins. Returns False on timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_my_turn() and not self.is_guard_period():
                return True
            wait_ms = min(self.ms_until_my_slot(), 50)  # check every 50ms max
            time.sleep(wait_ms / 1000)
        return False

    def wait_for_slot_of(self, target_node: str, timeout: float = 10.0) -> bool:
        """Block until a specific node's slot begins."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.current_owner() == target_node and not self.is_guard_period():
                return True
            time.sleep(0.02)
        return False

    # ── Background slot watcher ───────────────────────────────
    def start_slot_watcher(self, on_my_slot: Callable, on_others_slot: Callable) -> threading.Event:
        """
        Start a background thread that calls:
          on_my_slot()       — when it's this node's turn to transmit
          on_others_slot(node_id) — when another node's slot starts (time to listen)

        Returns a stop_event. Call stop_event.set() to stop.
        """
        stop_event  = threading.Event()
        last_slot   = -1

        def _watch():
            nonlocal last_slot
            while not stop_event.is_set():
                slot  = self.current_slot()
                owner = self.slot_owner(slot)

                if slot != last_slot and not self.is_guard_period():
                    last_slot = slot
                    if owner == self.node_id:
                        try:
                            on_my_slot()
                        except Exception as e:
                            print(f"[TDMA] on_my_slot error: {e}")
                    else:
                        try:
                            on_others_slot(owner)
                        except Exception as e:
                            print(f"[TDMA] on_others_slot error: {e}")

                time.sleep(0.02)   # 20ms polling

        t = threading.Thread(target=_watch, daemon=True)
        t.start()
        return stop_event

    def __repr__(self):
        return (
            f"TDMAScheduler(node={self.node_id}, "
            f"slot={self.my_slot}/{self.num_nodes}, "
            f"cycle={self.cycle_ms}ms)"
        )
