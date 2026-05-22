# Kitchen Decisions (Why we schedule the way we do)

This document explains the scheduling and estimation choices for the Cloud Kitchen simulation.

---

## PROBLEM 1: HOW DO YOU DECIDE WHAT TO COOK NEXT?

**Choice: Deadline-based hybrid using EDF (Earliest Deadline First) + station-aware scheduling.**

- Every order has a **deadline** derived from its delivery-zone SLA.
- The scheduler prioritizes orders with the **earliest deadline first** (EDF).
- Within/around those orders, we also respect **station capacity** by scheduling item start/end times based on when each station becomes available.

**Why this is reasonable**
- It directly optimizes for “getting the most urgent orders finished first,” which is the only objective that can be evaluated against customer delivery times.

**What this gives up**
- It may leave “small fast dishes” waiting if they belong to orders whose deadlines are later.
- It doesn’t explicitly minimize waiting time as an objective—deadline satisfaction is the primary goal.

---

## PROBLEM 2: MULTI-ITEM ORDERS ARE A TRAP

**Choice: Backward scheduling anchored on the longest item in the order.**

If an order has items A (25m) and B (6m):
- We prevent item B from finishing too early by scheduling it so that it **finishes roughly when the long item finishes**.

**Implementation concept (working backward)**
1. Find the **anchor item** = the longest `prep_time` item in the order.
2. Schedule the anchor at the earliest feasible station slot(s).
3. For each other item:
   - Choose its start time as late as possible so it finishes at the **same order finish time** (while also respecting that its station may be busy).

**What this gives up**
- It assumes “finishing together” is always best for quality. In real systems, stations/demand variability can make perfect alignment impossible.

---

## PROBLEM 3: STATIONS HAVE LIMITED CAPACITY

**Choice: Model station availability as a contention queue (“next free at”).**

- Each station has a capacity (number of parallel slots).
- We track a `station_free` pointer (effectively “how soon this station can start another item”).
- When scheduling an item:
  - Its `sched_start` is the maximum of:
    - when its station has capacity available, and
    - the time required by the backward schedule alignment for that order.

**What this gives up**
- It approximates capacity management with a simplified “free-at” pointer rather than a fully simulated slot timeline for multi-slot stations.

---

## PROBLEM 4: REALITY DIVERGES FROM THE PLAN

**Choice: Fail soft by recomputing estimates continuously + flagging at-risk.**

- We run an automation step periodically and advance items through stages.
- The UI compares **estimated completion** vs **deadline**.
- If reality falls behind, we set `at_risk` so the dashboard highlights the issue.

**What the system should do (stated intent)**
- Update downstream estimates because **stage progression changes “when ready” becomes true**.
- Surface the change via `at_risk`.
- (Optional future improvement) add explicit user notifications/log events for the manager.

**What this gives up**
- The dashboard may show “rough” predictions if the plan/automation diverges sharply between recomputations.

---

## PROBLEM 5: TWO REQUIREMENTS THAT CONFLICT

**Conflict**
- Accuracy requires recomputing estimates.
- Manual reordering means estimates become invalid once the queue order changes.

**Choice: Deterministic recomputation with transparency (warn or log when manager actions invalidate prior promises).**

Because we can’t guarantee accuracy after manual reorder:
- When the manager reorders:
  - we recompute silently **only within the simulation window**,
  - but we also **log/flag** that promises have changed.

**What this gives up**
- We cannot preserve previously promised times as immutable truths.

---

## Summary
- Priority: **EDF on deadlines**
- Multi-item quality: **backward schedule anchored on the longest item**
- Real-world capacity: **station availability modeling**
- Divergence: **recompute + at-risk flagging**
- Manual reorder: **recompute + transparency/logging**

# Kitchen Decisions (Why we schedule the way we do)

This document explains the scheduling and estimation choices for the Cloud Kitchen simulation.

The system is NOT a basic First Come First Serve (FCFS) kitchen queue.

Instead, the project uses:
- EDF (Earliest Deadline First)
- Longest-Item Anchoring
- Backward Synchronization
- Station-Aware Scheduling
- Multi-Agent Coordination
- Real-Time Risk Detection

The main goal is:
- Deliver orders before deadlines
- Ensure all dishes of the same order finish together
- Prevent cold food
- Reduce kitchen congestion
- Automatically manage kitchen operations

---

# PROBLEM 1: HOW DO YOU DECIDE WHAT TO COOK NEXT?

## Choice: Deadline-based hybrid using EDF (Earliest Deadline First) + station-aware scheduling.

- Every order has a **deadline** derived from its delivery-zone SLA.
- The scheduler prioritizes orders with the **earliest deadline first** (EDF).
- Within those orders, the scheduler also respects:
  - station capacity,
  - currently cooking items,
  - station availability timelines.

The Scheduler Agent continuously recomputes:
- item priorities,
- estimated ready times,
- risk states.

---

## Example

| Order | Deadline |
|---|---|
| Order A | 10:25 |
| Order B | 10:40 |
| Order C | 10:50 |

Priority becomes:

```text
A → B → C